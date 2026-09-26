"""Dependency-free HTTP server exposing the v1 widget REST contract.

Runs inside the Hermes install: one ThreadingHTTPServer, bearer auth, and all
state delegated to store.py so the serving process and the agent tools share the
same SQLite DB. Nothing here opens SQLite or invents storage.

Auth model (see docs/CONNECTION.md section 5):
  * AGENT  - the operator credential; loopback callers are trusted as agent-level.
  * DEVICE - per-paired-device token; required for device routes unless the
             caller is already agent-authorised.

Keep all request state local to the handler instance: ThreadingHTTPServer serves
requests concurrently, so module globals would race.
"""
from __future__ import annotations

import base64
import json
import logging
import ssl
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import preview, store
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import preview  # type: ignore
    import store  # type: ignore

VERSION = "1.0.0"
MAX_BODY_BYTES = 384 * 1024  # bounded JSON; inline SVG is capped separately at 256 KiB
MAX_OVERSIZED_DRAIN_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT = 20  # bounded concurrency for private HTTPS (Phase C)
REQUEST_TIMEOUT = 30  # request deadline in seconds (Phase C)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_UNSET = object()  # sentinel: distinguishes "no body read yet" from "empty body"

_log = logging.getLogger("hermes_widget.server")


class _HttpError(Exception):
    """Internal control-flow error carrying the HTTP status and JSON body."""

    __slots__ = ("status", "code", "detail")

    def __init__(self, status: int, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail or code


def _is_loopback(client_address: Any) -> bool:
    """True for the loopback forms a local caller can present.

    Accepts either a host string or the (host, port) tuple from client_address.
    """
    host = client_address[0] if isinstance(client_address, tuple) else client_address
    return host in _LOOPBACK_HOSTS


def _bearer(headers: Any) -> str:
    """Extract the bearer token. Never logged or echoed by callers."""
    raw = headers.get("Authorization", "") or ""
    if raw[:7].lower() != "bearer ":
        return ""
    return raw[7:].strip()


def _map_store_error(exc: store.StoreError) -> _HttpError:
    message = str(exc)
    if isinstance(exc, store.LayoutError):
        # The byte-size cap is a 413; node/field violations stay 400. Message
        # wording comes from validate.py, so match on "byte" rather than "cap".
        status = 413 if "byte" in message.lower() else 400
        return _HttpError(status, exc.code, message)
    if isinstance(exc, store.PublicationTooLarge):
        return _HttpError(413, exc.code, message)
    if isinstance(exc, store.RenderNotReady):
        return _HttpError(409, exc.code, message)
    if isinstance(exc, store.AssetNotFound):
        return _HttpError(404, exc.code, message)
    if isinstance(exc, store.AssetUnavailable):
        return _HttpError(503, exc.code, message)
    if isinstance(exc, store.PublicationError):
        return _HttpError(400, exc.code, message)
    if isinstance(exc, store.ActionIntentError):
        return _HttpError(400, exc.code, message)
    if isinstance(exc, store.RateLimitError):
        return _HttpError(429, exc.code, message)
    if isinstance(exc, store.PairingError):
        return _HttpError(400, exc.code, message)
    return _HttpError(400, exc.code, message)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    server_version = "HermesWidget/" + VERSION
    protocol_version = "HTTP/1.1"

    # -- response helpers ---------------------------------------------------

    def _json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, code: str, detail: str = "") -> None:
        retry = {"Retry-After": "5"} if status in {429, 503} else None
        self._json(status, {"error": code, "detail": detail or code}, retry)

    def _not_modified(self, etag: str) -> None:
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _etag_matches(self, etag: str) -> bool:
        raw = self.headers.get("If-None-Match", "") or ""
        candidates = {item.strip() for item in raw.split(",") if item.strip()}
        if "*" in candidates:
            return True
        return etag in candidates or any(item.removeprefix("W/") == etag for item in candidates)

    def _last_modified(self, timestamp: str | None) -> str | None:
        if not timestamp:
            return None
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return format_datetime(parsed.astimezone(timezone.utc), usegmt=True)

    def log_message(self, format: str, *args: Any) -> None:
        # format covers status/request-line only; headers (and tokens) never reach here.
        _log.info("%s - %s", self.address_string(), format % args)

    # -- request helpers ----------------------------------------------------

    def _read_body_once(self) -> bytes | None:
        """Read the request body exactly once, bounded by MAX_BODY_BYTES.

        Every request's body must be consumed (or the connection closed) before
        the response, because HTTP/1.1 keep-alive is on: an unread body would be
        parsed as the next request line when a client reuses the connection,
        which Android's HttpURLConnection does. Returns None when no body was
        sent, b"" for an explicitly empty body.
        """
        if self._body is not _UNSET:
            return self._body  # type: ignore[return-value]
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._body = None
            return None
        try:
            length = int(raw_length)
        except (TypeError, ValueError) as exc:
            raise _HttpError(400, "bad_request", "invalid Content-Length header") from exc
        if length < 0:
            raise _HttpError(400, "bad_request", "invalid Content-Length header")
        if length > MAX_BODY_BYTES:
            # Drain a bounded, modest oversized request so the client receives
            # the 413 instead of an aborted connection. Very large claims are
            # closed immediately rather than allowing unbounded resource use.
            if length <= MAX_OVERSIZED_DRAIN_BYTES:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            self.close_connection = True
            raise _HttpError(
                413, "payload_too_large",
                f"request body exceeds the {MAX_BODY_BYTES}-byte cap",
            )
        self._body = self.rfile.read(length) if length else b""
        return self._body

    def _read_json_body(self) -> Any:
        raw = self._read_body_once()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _HttpError(400, "invalid_json", f"request body is not valid JSON: {exc}") from exc

    def _authorize(self, *, allow_device: bool) -> tuple[str, str | None]:
        """Return (role, device_id). Raises 401 unless the caller is authorised.

        Loopback is trusted only at the AGENT level; a device route still needs a
        valid device token unless the caller is agent-authorised.
        """
        token = _bearer(self.headers)
        # A presented token must actually verify: a bogus bearer is rejected
        # even on loopback, so an invalid credential is never silently upgraded
        # to the trusted loopback principal.
        if token and store.verify_agent_token(token):
            return "agent", None
        if allow_device and token:
            device = store.device_for_token(token)
            if device:
                return "device", device["deviceId"]
        # Loopback callers must present a valid bearer token; no automatic
        # agent-level trust. Required before Tailscale HTTPS exposes the
        # server beyond localhost. See review finding 2.
        raise _HttpError(401, "unauthorized", "a valid bearer token is required")

    # -- routing ------------------------------------------------------------

    def _classify(self, path: str) -> tuple[str, str | None] | None:
        if path == "/v1/health":
            return "health", None
        if path == "/v1/capabilities":
            return "capabilities", None
        if path == "/v1/pairing-codes":
            return "pairing-codes", None
        if path == "/v1/pair":
            return "pair", None
        if path == "/v1/widgets":
            return "widgets", None
        if path == "/v1/events":
            return "events", None
        if path == "/v1/device":
            return "device", None
        if path == "/v1/device/instances":
            return "device-instances", None
        if path == "/v1/device/attention":
            return "device-attention", None
        if path == "/v1/intents":
            return "intents", None
        if path.startswith("/v1/assets/"):
            asset_id = path[len("/v1/assets/"):]
            if asset_id and "/" not in asset_id:
                return "asset", unquote(asset_id)
            return None
        prefix = "/v1/widgets/"
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if rest.endswith("/history") and rest[: -len("/history")]:
                return "history", unquote(rest[: -len("/history")])
            if rest.endswith("/publication/ack") and rest[: -len("/publication/ack")]:
                return "publication-ack", unquote(rest[: -len("/publication/ack")])
            if rest.endswith("/settings") and rest[: -len("/settings")]:
                return "settings", unquote(rest[: -len("/settings")])
            if rest.endswith("/preview") and rest[: -len("/preview")]:
                return "preview", unquote(rest[: -len("/preview")])
            if rest.endswith("/publication") and rest[: -len("/publication")]:
                return "publication", unquote(rest[: -len("/publication")])
            if rest.endswith("/events") and rest[: -len("/events")]:
                return "widget-events", unquote(rest[: -len("/events")])
            if rest and "/" not in rest:
                return "widget", unquote(rest)
        return None

    _ALLOWED: dict[str, tuple[str, ...]] = {
        "health": ("GET",),
        "capabilities": ("GET",),
        "pairing-codes": ("POST",),
        "pair": ("POST",),
        "widgets": ("GET",),
        "events": ("GET",),
        "device": ("PATCH",),
        "device-instances": ("PUT",),
        "device-attention": ("PUT",),
        "intents": ("GET", "POST"),
        "settings": ("GET", "PUT"),
        "asset": ("GET", "HEAD"),
        "publication": ("GET", "POST", "PUT"),
        "publication-ack": ("POST",),
        "history": ("GET",),
        "preview": ("POST",),
        "widget": ("GET", "PUT"),
        "widget-events": ("POST",),
    }

    def _handle(self, method: str) -> None:
        self._body: Any = _UNSET
        try:
            # Drain the body before any routing/auth decision so an error
            # response (401/404/405) still leaves the connection framed.
            self._read_body_once()
        except _HttpError as exc:
            self._error(exc.status, exc.code, exc.detail)
            return
        # Incompatible version check: client must send compatible transport (G)
        client_ver = self.headers.get("X-Hermes-Widget-Version", "") or self.headers.get("X-Widget-Version", "")
        if (
            client_ver
            and client_ver.strip() != VERSION
            and client_ver.strip() not in ("1.0.0", VERSION)
            and client_ver.strip().split(".")[0] != VERSION.split(".")[0]
        ):
            # Allow v1 and current; other versions get actionable upgrade message.
            self._error(426, "upgrade_required", f"client version {client_ver} incompatible with server {VERSION}; upgrade the app via the release link")
            return
        target = self._classify(urlsplit(self.path).path)
        if target is None:
            self._error(404, "not_found", "no such route")
            return
        kind, widget_id = target
        allowed = self._ALLOWED[kind]
        if method not in allowed:
            self._error(405, "method_not_allowed", f"{method} is not allowed here")
            return
        try:
            handler = getattr(self, "_" + kind.replace("-", "_"))
            handler(widget_id)
        except _HttpError as exc:
            self._error(exc.status, exc.code, exc.detail)
        except store.StoreError as exc:
            mapped = _map_store_error(exc)
            self._error(mapped.status, mapped.code, mapped.detail)
        except (BrokenPipeError, ConnectionResetError):
            raise

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._handle("OPTIONS")

    # -- route handlers -----------------------------------------------------

    def _health(self, _widget_id: str | None) -> None:
        self._json(
            200,
            {
                "status": "ok",
                "version": VERSION,
                "widgets": len(store.list_widgets()),
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )

    def _capabilities(self, _widget_id: str | None) -> None:
        self._authorize(allow_device=True)
        self._json(200, store.publication_capabilities())

    def _publication(self, widget_id: str | None) -> None:
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        if self.command in {"POST", "PUT"}:
            self._authorize(allow_device=False)
            body = self._read_json_body()
            if not isinstance(body, dict):
                raise _HttpError(400, "invalid_publication", "publication must be a JSON object")
            file_path = body.get("file_path", body.get("image_path", body.get("path")))
            ticker = body.get("ticker")
            ticker_only = isinstance(ticker, dict) and body.get("text") is None and body.get("svg") is None and file_path is None
            title = body.get("title") or (ticker.get("title") if ticker_only else None)
            summary = body.get("summary") or (ticker.get("summary") if ticker_only else None)
            if not isinstance(title, str) or not isinstance(summary, str):
                raise _HttpError(400, "invalid_publication", "title and summary are required strings")
            if ticker_only:
                result = store.put_ticker(
                    widget_id, title=title, summary=summary, text=ticker.get("text"),
                    svg=ticker.get("svg"),
                    file_path=ticker.get("file_path", ticker.get("filePath")),
                    expires_at=ticker.get("expires_at", ticker.get("expiresAt")),
                    ttl_seconds=ticker.get("ttl_seconds", ticker.get("ttlSeconds")),
                    max_age_seconds=ticker.get("max_age_seconds", ticker.get("maxAgeSeconds")),
                    priority=ticker.get("priority", "normal"),
                    item_id=ticker.get("item_id", ticker.get("itemId")),
                    actions=ticker.get("actions"), provenance=ticker.get("provenance"),
                    pinned=bool(ticker.get("pinned", False)), rotate=bool(ticker.get("rotate", False)),
                )
            else:
                result = store.put_publication(
                    widget_id,
                    title=title,
                    summary=summary,
                    text=body.get("text"),
                    svg=body.get("svg"),
                    file_path=file_path,
                    expires_at=body.get("expires_at"),
                    ttl_seconds=body.get("ttl_seconds"),
                    max_age_seconds=body.get("max_age_seconds", body.get("maxAgeSeconds")),
                    priority=body.get("priority", "normal"),
                    item_id=body.get("item_id", body.get("itemId")),
                    actions=body.get("actions"),
                    provenance=body.get("provenance"),
                    dark_palette=bool(body.get("darkPalette", body.get("dark_palette", False))),
                    variants=body.get("variants"),
                )
            self._json(200, result)
            return

        role, device_id = self._authorize(allow_device=True)
        publication = store.get_publication(widget_id)
        if publication is None:
            self._error(404, "unknown_publication", f"no publication for widget {widget_id!r}")
            return
        if publication.get("stale"):
            # Drop content that is already outside its freshness window instead of
            # letting the phone render it late; the publisher sees state "stale".
            self._error(
                410,
                "publication_stale",
                "publication is older than its maxAgeSeconds freshness window",
            )
            return
        revision = publication.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise _HttpError(503, "publication_unavailable", "stored publication revision is invalid")
        if role == "device" and device_id:
            store.record_publication_fetch(
                widget_id,
                device_id,
                revision,
                downloaded=publication.get("kind") == "text",
            )
        etag = f'\"{publication["publicationId"]}\"'
        if self._etag_matches(etag):
            self._not_modified(etag)
            return
        extra = {"ETag": etag, "Cache-Control": "private, no-cache"}
        last_modified = self._last_modified(publication.get("publishedAt"))
        if last_modified:
            extra["Last-Modified"] = last_modified
        self._json(200, publication, extra)

    def _publication_ack(self, widget_id: str | None) -> None:
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        role, device_id = self._authorize(allow_device=True)
        if role != "device" or not device_id:
            raise _HttpError(403, "device_required", "render acknowledgement requires a paired device token")
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "invalid_render_ack", "render acknowledgement must be an object")
        if body.get("status") not in {"render_submitted", "rendered"}:
            raise _HttpError(400, "invalid_render_ack", "status must be render_submitted or rendered")
        revision = body.get("revision")
        width = body.get("renderedWidth", body.get("width"))
        height = body.get("renderedHeight", body.get("height"))
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise _HttpError(400, "invalid_render_ack", "revision must be an integer")
        if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
            raise _HttpError(400, "invalid_render_ack", "rendered dimensions must be integers")
        result = store.acknowledge_publication_render(
            widget_id, device_id, revision, width, height,
            status=body.get("status"),
        )
        self._json(200, result)

    def _asset(self, asset_id: str | None) -> None:
        role, device_id = self._authorize(allow_device=True)
        if asset_id is None:
            raise _HttpError(404, "not_found", "asset id is required")
        metadata = store.get_asset(asset_id)
        etag = f'\"{metadata["sha256"]}\"'
        if self._etag_matches(etag):
            self._not_modified(etag)
            return
        metadata, data = store.read_asset(asset_id)
        if role == "device" and device_id:
            publication = store.publication_for_asset(asset_id)
            if publication is not None:
                revision = publication.get("revision")
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise _HttpError(503, "publication_unavailable", "stored publication revision is invalid")
                store.record_asset_download(
                    publication["widgetId"],
                    device_id,
                    revision,
                    asset_id,
                )
        self.send_response(200)
        self.send_header("Content-Type", metadata["mediaType"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", "inline")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _pairing_codes(self, _widget_id: str | None) -> None:
        self._authorize(allow_device=False)
        self._json(200, store.mint_pairing_code())

    def _pair(self, _widget_id: str | None) -> None:
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "invalid_or_expired_code", "pairing code is required")
        code = body.get("code")
        label = body.get("deviceLabel")
        if not isinstance(code, str) or not code.strip():
            raise _HttpError(400, "invalid_or_expired_code", "pairing code is required")
        if not isinstance(label, str):
            label = "unknown"
        device = store.register_device(code, label)
        if device is None:
            raise _HttpError(400, "invalid_or_expired_code", "the code is unknown, used, or expired")
        self._json(200, device)

    def _widgets(self, _widget_id: str | None) -> None:
        self._authorize(allow_device=True)
        self._json(200, {"widgets": store.list_widgets()})

    def _widget(self, widget_id: str | None) -> None:
        self._authorize(allow_device=True)
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        if self.command == "PUT":
            self._authorize(allow_device=False)
            layout = self._read_json_body()
            if not isinstance(layout, dict):
                raise _HttpError(400, "invalid_layout", "layout must be a JSON object")
            self._json(200, store.put_widget(widget_id, layout))
            return
        layout = store.get_widget(widget_id)
        if layout is None:
            self._error(404, "unknown_widget", f"no widget with id {widget_id!r}")
            return
        # The layout and publication channels are separate stores. Echo a pointer
        # so a raw API consumer does not read a stale layout and conclude that a
        # just-published revision was lost.
        current = store.get_publication(widget_id)
        if current is not None:
            layout = {
                **layout,
                "publication": {
                    "revision": current.get("revision"),
                    "publishedAt": current.get("publishedAt"),
                    "kind": current.get("kind"),
                    "state": (
                        "stale"
                        if current.get("stale")
                        else "expired"
                        if current.get("expired")
                        else "published"
                    ),
                },
            }
        self._json(200, layout)

    def _device_instances(self, _widget_id: str | None) -> None:
        role, device_id = self._authorize(allow_device=True)
        if role != "device" or not device_id:
            raise _HttpError(403, "device_required", "instance inventory requires a paired device token")
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        widget_id = body.get("widgetId", body.get("widget_id"))
        if not isinstance(widget_id, str) or not widget_id:
            raise _HttpError(400, "bad_request", "widgetId is required")
        instances = store.report_widget_instances(device_id, widget_id, body.get("instances"))
        self._json(200, {"ok": True, "widgetId": widget_id, "instances": instances})

    def _history(self, widget_id: str | None) -> None:
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        self._authorize(allow_device=True)
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        try:
            limit = int(query.get("limit", ["20"])[0])
        except (TypeError, ValueError):
            limit = 20
        self._json(200, {"widgetId": widget_id, "revisions": store.publication_history(widget_id, limit)})

    def _device_attention(self, _widget_id: str | None) -> None:
        role, device_id = self._authorize(allow_device=True)
        if role != "device" or not device_id:
            raise _HttpError(403, "device_required", "attention reports require a paired device token")
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        widget_id = body.get("widgetId", body.get("widget_id"))
        if not isinstance(widget_id, str) or not widget_id:
            raise _HttpError(400, "bad_request", "widgetId is required")
        self._json(200, {"ok": True, "attention": store.report_attention(device_id, widget_id, body)})

    def _settings(self, widget_id: str | None) -> None:
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        self._authorize(allow_device=False)
        if self.command == "GET":
            self._json(200, store.get_widget_settings(widget_id))
            return
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        self._json(200, {"ok": True, **store.set_widget_settings(
            widget_id, body.get("quietHours", body.get("quiet_hours"))
        )})

    def _preview(self, widget_id: str | None) -> None:
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        self._authorize(allow_device=False)
        body = self._read_json_body()
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise _HttpError(400, "invalid_preview", "preview body must be a JSON object")
        raw_publication = body.get("publication")
        if raw_publication is None:
            source_keys = {"title", "summary", "text", "svg", "file_path", "filePath"}
            raw_publication = body if source_keys.intersection(body) else None
        if raw_publication is None:
            publication = store.get_publication(widget_id)
            if publication is None:
                raise _HttpError(404, "unknown_publication", f"no publication for widget {widget_id!r}")
        else:
            if not isinstance(raw_publication, dict):
                raise _HttpError(400, "invalid_preview", "publication must be a JSON object")
            content_source = raw_publication.get("content")
            if isinstance(content_source, dict) and (
                content_source.get("filePath") or content_source.get("file_path")
            ) and not any(key in raw_publication for key in ("text", "svg", "file_path", "filePath")):
                raw_publication = {
                    **raw_publication,
                    "filePath": content_source.get("filePath", content_source.get("file_path")),
                }
                raw_publication.pop("content", None)
            if isinstance(raw_publication.get("content"), dict) and not any(
                key in raw_publication for key in ("text", "svg", "file_path", "filePath")
            ):
                publication = {**raw_publication, "widgetId": widget_id}
                raw_publication = None
        if raw_publication is not None:
            try:
                prepared = store.prepare_publication(
                    title=raw_publication.get("title"),
                    summary=raw_publication.get("summary"),
                    text=raw_publication.get("text"),
                    svg=raw_publication.get("svg"),
                    file_path=raw_publication.get("file_path", raw_publication.get("filePath")),
                    expires_at=raw_publication.get("expires_at", raw_publication.get("expiresAt")),
                    ttl_seconds=raw_publication.get("ttl_seconds", raw_publication.get("ttlSeconds")),
                    max_age_seconds=raw_publication.get("max_age_seconds", raw_publication.get("maxAgeSeconds")),
                    priority=raw_publication.get("priority", "normal"),
                    item_id=raw_publication.get("item_id", raw_publication.get("itemId")),
                    actions=raw_publication.get("actions"),
                )
            except Exception as exc:  # normalize publication errors to the HTTP contract
                raise _HttpError(400, "invalid_publication", str(exc)) from exc
            content: dict[str, Any]
            if prepared.kind == "text":
                content = {"type": "text", "mediaType": "text/plain; charset=utf-8", "text": prepared.text}
            else:
                assert prepared.asset is not None
                content = {
                    "type": "image", "mediaType": prepared.asset.media_type,
                    "width": prepared.asset.width, "height": prepared.asset.height,
                    "bytes": len(prepared.asset.data), "sha256": prepared.asset.sha256,
                    # The preview endpoint accepts inline asset bytes only for this
                    # one response; it never persists them.
                    "data": base64.b64encode(prepared.asset.data).decode("ascii"),
                }
            publication = {
                "version": store._publication_capabilities()["publicationVersion"],
                "widgetId": widget_id,
                "publicationId": "preview",
                "revision": 0,
                "kind": prepared.kind,
                "title": prepared.title,
                "summary": prepared.summary,
                "publishedAt": store._now(),
                "expiresAt": prepared.expires_at,
                "priority": prepared.priority,
                "itemId": prepared.item_id,
                "actions": list(prepared.actions),
                "content": content,
            }
        inventory = store.list_widget_instances(widget_id)
        requested_sizes = body.get("sizes", body.get("sizeClasses"))
        try:
            previews = preview.render_publication_previews(
                publication,
                sizes=requested_sizes,
                inventory=inventory,
                asset_loader=lambda asset_id: store.read_asset(asset_id)[1],
            )
        except ValueError as exc:
            raise _HttpError(400, "invalid_preview", str(exc)) from exc
        last_render = store._last_render_metrics()
        for item in previews:
            if item.get("renderer") == "svg-renderer-unavailable":
                if last_render:
                    item["note"] = (
                        "No local SVG renderer (CairoSVG/libcairo); "
                        f"last device render {last_render['width']}×{last_render['height']}px"
                    )
        self._json(200, {
            "ok": True,
            "widgetId": widget_id,
            "revision": publication.get("revision"),
            "sizes": [item["size"] for item in previews],
            "previews": previews,
            "warnings": store._capacity_warnings_for_publication(publication, widget_id),
            "note": "Preview is an advisory server raster; the Android device remains authoritative.",
        })

    def _intents(self, _widget_id: str | None) -> None:
        self._authorize(allow_device=False)
        if self.command == "GET":
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
            widget_id = query.get("widget_id", [None])[0]
            status = query.get("status", [None])[0]
            try:
                limit = int(query.get("limit", ["200"])[0])
            except (TypeError, ValueError):
                limit = 200
            self._json(200, {
                "intents": store.get_intents(widget_id, status=status, limit=limit),
                "audit": store.get_action_audit(widget_id, limit=limit),
            })
            return
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        result = store.resolve_intent(
            body.get("intentId", body.get("intent_id", "")),
            body.get("outcome", ""),
            result=body.get("result"),
            confirmed=body.get("confirmed", False),
        )
        self._json(200, {"ok": True, "intent": result})

    def _device(self, _widget_id: str | None) -> None:
        role, device_id = self._authorize(allow_device=True)
        if role != "device" or not device_id:
            raise _HttpError(403, "device_required", "device label changes require a paired device token")
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        label = body.get("label")
        if label is not None:
            updated = store.update_device_label(device_id, label)
            if updated is None:
                raise _HttpError(404, "unknown_device", "device does not exist")
        else:
            updated = {"deviceId": device_id}
        if "pushEndpoint" in body or "push_endpoint" in body:
            push_result = store.set_device_push_endpoint(
                device_id, body.get("pushEndpoint", body.get("push_endpoint"))
            )
            updated = {**updated, **push_result}
        if "pushState" in body or "push_state" in body:
            push_state = body.get("pushState", body.get("push_state"))
            if not isinstance(push_state, dict):
                raise _HttpError(400, "bad_request", "pushState must be an object")
            updated = {**updated, **store.set_device_push_state(
                device_id,
                push_state.get("state"),
                distributor_present=push_state.get("distributorPresent", push_state.get("distributor_present")),
                failure_reason=push_state.get("failureReason", push_state.get("failure_reason")),
            )}
        if (
            label is None
            and "pushEndpoint" not in body and "push_endpoint" not in body
            and "pushState" not in body and "push_state" not in body
        ):
            raise _HttpError(400, "bad_request", "label, pushEndpoint, or pushState is required")
        self._json(200, updated)

    def _widget_events(self, widget_id: str | None) -> None:
        _role, device_id = self._authorize(allow_device=True)
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        event = body.get("event")
        payload = body.get("payload")
        if not isinstance(event, str) or not event:
            raise _HttpError(400, "bad_request", "'event' is required")
        if payload is not None and not isinstance(payload, dict):
            raise _HttpError(400, "bad_request", "'payload' must be a JSON object")
        # The Android client places idempotency and action fields at the envelope
        # level; retain them in the durable payload so both wire shapes dedupe.
        if isinstance(payload, dict):
            payload = dict(payload)
        else:
            payload = {}
        for field in ("clientEventId", "itemId", "actionClass", "confirmOnDevice", "revision"):
            if field in body:
                payload.setdefault(field, body[field])
        if payload:
            body["payload"] = payload
        else:
            payload = None
        if store.get_widget(widget_id) is None and store.get_publication(widget_id) is None:
            raise _HttpError(404, "unknown_widget", f"no widget with id {widget_id!r}")
        if event == "answer":
            if not device_id:
                raise _HttpError(403, "device_required", "answers require a paired device token")
            question_id = body.get("questionId", payload.get("questionId") if isinstance(payload, dict) else None)
            text = body.get("answer", payload.get("answer") if isinstance(payload, dict) else None)
            result = store.answer_question(device_id, question_id, text)
            self._json(200, {"ok": True, **result})
            return
        intent_event = event
        if event == "event" and isinstance(payload, dict):
            candidate = payload.get("intent", payload.get("action", body.get("action", body.get("kind"))))
            if candidate in {"approve", "snooze", "open"}:
                intent_event = str(candidate)
        if intent_event in {"approve", "snooze", "open"}:
            if not device_id:
                raise _HttpError(403, "device_required", "actions require a paired device token")
            result = store.post_action_event(
                widget_id,
                device_id,
                intent_event,
                payload,
                revision=body.get("revision"),
                item_id=body.get("itemId", body.get("item_id")),
                action_class=body.get("actionClass", body.get("action_class")),
                confirm_on_device=body.get("confirmOnDevice", body.get("confirm_on_device", False)),
            )
            self._json(200, {"ok": True, **result})
            return
        event_id = store.post_event(widget_id, device_id or "agent", event, payload)
        self._json(200, {"ok": True, "id": event_id})

    def _events(self, _widget_id: str | None) -> None:
        self._authorize(allow_device=False)
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        since = query.get("since", [None])[0]
        widget_id = query.get("widget_id", [None])[0]
        limit_raw = query.get("limit", [200])[0]
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 200
        self._json(200, {"events": store.get_events(since=since, widget_id=widget_id, limit=limit)})


def make_server(
    host: str,
    port: int,
    *,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> ThreadingHTTPServer:
    """Bind a threaded HTTP(S) server without starting it.

    TLS is enabled by wrapping the listening socket, so certfile is the switch.
    """
    store.init_db()
    httpd = _Server((host, port), _Handler)
    if certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    return httpd


def run_server(
    host: str,
    port: int,
    *,
    certfile: str | None = None,
    keyfile: str | None = None,
    quiet: bool = False,
) -> None:
    """Serve until interrupted; prints the listen URL unless quiet."""
    httpd = make_server(host, port, certfile=certfile, keyfile=keyfile)
    scheme = "https" if certfile else "http"
    url = f"{scheme}://{host}:{httpd.server_address[1]}"
    if not quiet:
        print(f"hermes widget server listening on {url}", flush=True)
    _log.info("serving on %s", url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":  # pragma: no cover - manual entry point
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Hermes widget REST API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--certfile")
    parser.add_argument("--keyfile")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    run_server(args.host, args.port, certfile=args.certfile, keyfile=args.keyfile, quiet=args.quiet)
