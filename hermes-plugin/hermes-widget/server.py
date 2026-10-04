"""Dependency-free HTTP server exposing the v1 widget REST contract.

Runs inside the Hermes install: one ThreadingHTTPServer, bearer auth, and all
state delegated to store.py so the serving process and the agent tools share the
same SQLite DB. Nothing here opens SQLite or invents storage.

Auth model (see docs/CONNECTION.md):
  * AGENT  - the operator credential; loopback callers are trusted as agent-level.
  * DEVICE - per-paired-device token; required for device routes unless the
             caller is already agent-authorised.

Keep all request state local to the handler instance: ThreadingHTTPServer serves
requests concurrently, so module globals would race.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import threading
import uuid
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import preview, proactive, retention, store, watches
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import preview  # type: ignore
    import proactive  # type: ignore
    import retention  # type: ignore
    import store  # type: ignore
    import watches  # type: ignore

VERSION = "1.0.0"
MAX_BODY_BYTES = 384 * 1024  # bounded JSON; inline SVG is capped separately at 256 KiB
MAX_OVERSIZED_DRAIN_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT = 20  # bounded concurrency for private HTTPS (Phase C)
REQUEST_TIMEOUT = 30  # request deadline in seconds (Phase C)
WATCH_TICK_INTERVAL_SECONDS = 60
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_UNSET = object()  # sentinel: distinguishes "no body read yet" from "empty body"

_log = logging.getLogger("hermes_widget.server")

# Field round 8/9: the access log existed and never emitted — twice, for two different
# reasons, and it was green in tests both times. A logger with no level and no handler
# inherits the root WARNING threshold, so `_log.info(...)` was discarded. Then the fix
# guarded on `not logging.getLogger().handlers`, which is true in a bare test harness and
# false in the deployment, where the Hermes gateway puts its own handler on the root: so
# in production `hermes_widget` had no handler of its own, the record propagated to the
# host's WARNING-level handler, and it was dropped. Zero lines in server.log after hours
# of real traffic, while the test harness printed happily.
#
# Two lessons, both about testing the context rather than the code:
#   * a gate that is green in the harness and dead in the deployment is the same disease
#     as the release-evidence check that failed in CI for an unrelated reason;
#   * "do not hijack the host's handlers" is right, and was misread as "do not attach
#     our own". Attaching to *our* logger is not hijacking.
#
# stderr is the destination, and it is the right one: the startup hook spawns this process
# with `stdout=log, stderr=STDOUT` into <Hermes home>/widget/server.log
# (gateway_hook.py::_spawn_server), which is the file an operator greps. Adding a file
# handler as well would double every line.
def configure_access_log(verbose: bool | None = None) -> logging.Logger:
    """Give the access log a handler and a level, in whatever context we are started.

    * a handler is attached whenever *this* logger has none, whatever the root has;
    * `propagate` is disabled then, so a verbose host does not print every line twice;
    * a host that has already configured `hermes_widget` itself is left alone;
    * `HERMES_WIDGET_LOG=off` (or quiet) silences it deliberately.
    Never raises: a logging problem must not take the server down.
    """
    setting = os.environ.get("HERMES_WIDGET_LOG", "info").strip().lower()
    if verbose is None:
        verbose = setting in {"1", "true", "yes", "info", "debug", "verbose"}
    silent = setting in {"0", "false", "no", "off", "quiet", "none", "silent"}
    logger = logging.getLogger("hermes_widget")
    try:
        if silent:
            logger.setLevel(logging.WARNING)
            return _log
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            logger.addHandler(handler)
            # Our handler is the destination; do not also print through the host's root.
            logger.propagate = False
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    except Exception:  # pragma: no cover - logging must never break startup
        pass
    return _log


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


# Which control sent an event. Closed on purpose; see _widget_events.
EVENT_SOURCES = frozenset({"widget_action", "in_app_button"})

# Fields the attention route needs for routing, and that the store must never see.
_ATTENTION_ROUTING_FIELDS = frozenset({"widgetId", "widget_id"})


def _bearer(headers: Any) -> str:
    """Extract the bearer token. Never logged or echoed by callers."""
    raw = headers.get("Authorization", "") or ""
    if raw[:7].lower() != "bearer ":
        return ""
    return raw[7:].strip()


_COMBINED_VERSION = re.compile(r"^\s*(\S+)\s+\((\d+)\)\s*$")


def _split_combined_version(raw: Any) -> tuple[Any, Any]:
    """Accept both "0.2.0" and "0.2.0 (200)" in the version header.

    Returns (version, build_or_None). Unrecognised text is passed through untouched so the
    store's own validation is the single place that decides what is storable.
    """
    if not isinstance(raw, str):
        return raw, None
    match = _COMBINED_VERSION.match(raw)
    if not match:
        return raw, None
    return match.group(1), match.group(2)


def _map_store_error(exc: store.StoreError) -> _HttpError:
    message = str(exc)
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
    request_queue_size = MAX_CONCURRENT

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        import threading

        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class _Handler(BaseHTTPRequestHandler):
    server_version = "HermesWidget/" + VERSION
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT

    # -- response helpers ---------------------------------------------------

    def _json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._status = status
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
        self._status = status
        self._error_code = code
        if status >= 400 and self._kind in {"widget-events", "device", "device-instances", "device-attention"} and self._device_id:
            try:
                store.record_rejected_event(
                    self._widget_id or "",
                    self._device_id,
                    self._event_name,
                    method=self.command,
                    path=urlsplit(self.path).path,
                    status=status,
                    code=code,
                    detail="",
                    request_id=self._request_id,
                )
            except Exception:
                _log.debug("could not persist device rejection", exc_info=True)
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
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise _HttpError(400, "bad_request", "Transfer-Encoding is not supported")
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
            self._principal = "agent"
            return "agent", None
        if allow_device and token:
            device = store.device_for_token(token)
            if device:
                self._note_client(device["deviceId"])
                self._device_id = device["deviceId"]
                self._principal = "device"
                return "device", device["deviceId"]
        # An unauthenticated caller is still worth a row: a 401 with no trace is exactly
        # the round-5 failure mode.
        self._principal = "anonymous"
        # Loopback callers must present a valid bearer token; no automatic
        # agent-level trust. Required before Tailscale HTTPS exposes the
        # server beyond localhost. See review finding 2.
        raise _HttpError(401, "unauthorized", "a valid bearer token is required")

    # -- client build reporting --------------------------------------------

    _CLIENT_VERSION_HEADERS = ("X-Hermes-App-Version", "X-Hermes-Appversion")
    _CLIENT_BUILD_HEADERS = ("X-Hermes-App-Build", "X-Hermes-Appbuild")
    _CLIENT_SDK_HEADERS = ("X-Hermes-Os-Sdk", "X-Hermes-Android-Sdk")
    _CLIENT_SHA_HEADERS = ("X-Hermes-App-Sha", "X-Hermes-App-Commit")

    def _header(self, names: tuple[str, ...]) -> str | None:
        for name in names:
            value = self.headers.get(name)
            if value:
                return value
        return None

    def _client_identity(self) -> dict[str, object]:
        """The build this request came from, from headers or a `client` body block.

        Lenient on purpose (see store.record_device_client): a malformed value is treated
        as absent, never as a reason to refuse a publication fetch. The version header is
        also accepted in the combined "0.2.0 (200)" form a human-friendly client might
        send, because a space and a bracket in optional metadata should cost nothing.
        """
        raw_version = self._header(self._CLIENT_VERSION_HEADERS)
        version, build_from_version = _split_combined_version(raw_version)
        build = self._header(self._CLIENT_BUILD_HEADERS)
        return {
            "appVersion": version,
            "appBuildCode": build if build is not None else build_from_version,
            "osSdk": self._header(self._CLIENT_SDK_HEADERS),
            "appBuildSha": self._header(self._CLIENT_SHA_HEADERS),
        }

    def _client_from_body(self, body: Any) -> dict[str, object]:
        """Accept both `client: {...}` and the flat fields a thin client may send."""
        if not isinstance(body, dict):
            return {}
        block = body.get("client", body.get("clientInfo"))
        source = block if isinstance(block, dict) else body
        return {
            "appVersion": source.get("appVersion", source.get("app_version", source.get("version"))),
            "appBuildCode": source.get("appBuildCode", source.get("app_build_code", source.get("buildCode"))),
            "osSdk": source.get("osSdk", source.get("os_sdk", source.get("androidSdk"))),
            "appBuildSha": source.get("appBuildSha", source.get("app_build_sha", source.get("commit"))),
        }

    def _note_client(self, device_id: str, body: Any = None) -> dict | None:
        """Record the build for a device. Called on every authenticated device request."""
        identity = {**self._client_identity(), **self._client_from_body(body)}
        if not any(value is not None for value in identity.values()):
            return None
        try:
            return store.record_device_client(
                device_id,
                identity.get("appVersion"),
                identity.get("appBuildCode"),
                identity.get("osSdk"),
                identity.get("appBuildSha"),
            )
        except store.StoreError:
            # Metadata must never break the request it rode along with.
            return None

    # -- access log --------------------------------------------------------
    #
    # Field round 5: a press of "Request update" that reached no table left nothing
    # to look at, because the answer could have been 401, 403, 400, 429, 500, a
    # network abort or a silent client-side return. One line per device request
    # makes each of those distinguishable. Tokens never appear: the device id is
    # the identity, and it is not a credential.

    def _access_log(self) -> None:
        status = getattr(self, "_status", 0)
        if not status:
            return
        # Only the routes where a missing row is a real question.
        if self._kind not in {
            "widget-events", "device", "device-instances", "device-attention",
            "device-action-confirm",
        }:
            return
        code = getattr(self, "_error_code", None) if status >= 400 else None
        _log.info(
            "access %s %s -> %s req=%s device=%s event=%s code=%s widget=%s",
            self.command,
            self.path.split("?", 1)[0],
            status,
            self._request_id,
            self._device_id or self._principal or "-",
            self._event_name or "-",
            code or "-",
            self._widget_id or "-",
        )

    def _note_event(self, event: Any) -> None:
        """Remember the event name for the access line (never the payload)."""
        self._event_name = event[:64] if isinstance(event, str) and event else None

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
        match = re.fullmatch(r"/v1/device/action-intents/([^/]+)/confirm", path)
        if match:
            return "device-action-confirm", unquote(match.group(1))
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
        "device-action-confirm": ("POST",),
        "intents": ("GET", "POST"),
        "settings": ("GET", "PUT"),
        "asset": ("GET", "HEAD"),
        "publication": ("GET", "HEAD", "POST", "PUT"),
        "publication-ack": ("POST",),
        "history": ("GET",),
        "preview": ("POST",),
        "widget-events": ("POST",),
    }

    def _handle(self, method: str) -> None:
        self._body: Any = _UNSET
        self._device_id: str | None = None
        self._principal = ""
        self._event_name: str | None = None
        self._widget_id: str | None = None
        self._kind = ""
        self._error_code: str | None = None
        self._status = 0
        self._request_id = uuid.uuid4().hex[:12]
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
        self._kind = kind
        self._widget_id = widget_id
        allowed = self._ALLOWED[kind]
        if method not in allowed:
            self._error(405, "method_not_allowed", f"{method} is not allowed here")
            return
        try:
            handler = getattr(self, "_" + kind.replace("-", "_"))
            with store.db():
                handler(widget_id)
        except _HttpError as exc:
            self._error(exc.status, exc.code, exc.detail)
        except store.StoreError as exc:
            mapped = _map_store_error(exc)
            self._error(mapped.status, mapped.code, mapped.detail)
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception:
            _log.exception("request failed request_id=%s", self._request_id)
            self._error(
                500,
                "internal_error",
                f"request failed; reference {self._request_id}",
            )
        finally:
            # A request that leaves no row must at least leave a line. Tokens are never
            # logged: the identity here is the device id, which is not a credential.
            self._access_log()

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
            ticker_only = isinstance(ticker, dict) and body.get("text") is None and body.get("svg") is None and body.get("presentation") is None and file_path is None
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
                    presentation=body.get("presentation"),
                    visual_variants=body.get("visual_variants", body.get("visualVariants")),
                    work_context=body.get("work_context"),
                    refresh_id=body.get("refresh_id"),
                    ticker=ticker,
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
        if self.command == "GET" and role == "device" and device_id:
            store.record_publication_fetch(
                widget_id,
                device_id,
                revision,
                downloaded=publication.get("kind") == "text",
            )
        canonical = json.dumps(publication, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        etag = f'\"{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}\"'
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
        # Which build drew this revision, recorded with the acknowledgement so the
        # delivery chain can name it later. A client that reports nothing records nothing.
        store.record_render_build(
            widget_id,
            device_id,
            revision,
            body.get("clientVersion", body.get("appVersion")),
            body.get("clientBuildCode", body.get("appBuildCode")),
            instance_id=body.get("instanceId"),
            app_build_sha=body.get("clientBuildSha", body.get("appBuildSha")),
        )
        rendered_by = store.get_render_builds(widget_id).get((device_id, revision))
        if rendered_by:
            result["renderedBy"] = rendered_by
        self._json(200, result)

    def _asset(self, asset_id: str | None) -> None:
        role, device_id = self._authorize(allow_device=True)
        if asset_id is None:
            raise _HttpError(404, "not_found", "asset id is required")
        metadata = store.get_asset(asset_id)
        etag = f'\"{metadata["sha256"]}\"'
        if self.command == "GET" and role == "device" and device_id:
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
        if self._etag_matches(etag):
            self._not_modified(etag)
            return
        metadata, data = store.read_asset(asset_id)
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
        self._widget_id = widget_id
        # Routing fields are consumed here and must not reach the store: the attention
        # report is an aggregate-only allow-list that rejects anything it does not know,
        # so handing it the whole body rejected every report with a 400. Field round 8.
        report = {key: value for key, value in body.items() if key not in _ATTENTION_ROUTING_FIELDS}
        self._json(200, {"ok": True, "attention": store.report_attention(device_id, widget_id, report)})

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
            source_keys = {"title", "summary", "text", "svg", "file_path", "filePath", "presentation", "variants", "visual_variants", "visualVariants", "ticker", "sizes", "sizeClasses"}
            raw_publication = body if source_keys.intersection(body) else None
        if raw_publication is None:
            publication = store.get_publication(widget_id)
            if publication is None:
                raise _HttpError(404, "unknown_publication", f"no publication for widget {widget_id!r}")
        else:
            try:
                publication = preview.build_preview_publication(raw_publication, widget_id)
            except Exception as exc:  # normalize publication errors to the HTTP contract
                raise _HttpError(400, "invalid_publication", str(exc)) from exc
        inventory = store.list_widget_instances(widget_id)
        requested_sizes = body.get("sizes", body.get("sizeClasses"))
        try:
            previews = preview.render_publication_previews(
                publication,
                sizes=requested_sizes,
                inventory=inventory,
                asset_loader=lambda asset_id: store.read_asset(asset_id)[1],
                palette=body.get("palette", "light"),
                font_scale=body.get("font_scale", body.get("fontScale", 1.0)),
            )
        except (ValueError, ImportError, OSError) as exc:
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
        if "client" in body or "clientInfo" in body:
            updated = {**updated, "client": self._note_client(device_id, body)}
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
            and "client" not in body and "clientInfo" not in body
        ):
            raise _HttpError(400, "bad_request", "label, pushEndpoint, pushState, or client is required")
        self._json(200, updated)

    def _device_action_confirm(self, intent_id: str | None) -> None:
        role, device_id = self._authorize(allow_device=True)
        if role != "device" or not device_id:
            raise _HttpError(403, "device_required", "intent confirmation requires its paired device token")
        if not intent_id:
            raise _HttpError(404, "unknown_intent", "intent id is required")
        body = self._read_json_body()
        if body not in (None, {}):
            raise _HttpError(400, "bad_request", "confirmation body must be empty")
        self._json(200, {
            "ok": True,
            "intent": store.confirm_action_intent(intent_id, device_id),
        })

    def _widget_events(self, widget_id: str | None) -> None:
        _role, device_id = self._authorize(allow_device=True)
        if widget_id is None:
            raise _HttpError(404, "not_found", "widget id is required")
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise _HttpError(400, "bad_request", "body must be a JSON object")
        event = body.get("event")
        self._note_event(event)
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
        for field in ("clientEventId", "itemId", "actionClass", "confirmOnDevice", "revision",
                      "instanceId", "source"):
            if field in body:
                payload.setdefault(field, body[field])
        # `source` says which control was pressed. It is a closed vocabulary, so an
        # unexpected value is dropped rather than stored: this field must never become a
        # place to put content.
        if "source" in payload and payload.get("source") not in EVENT_SOURCES:
            payload.pop("source", None)
        if payload:
            body["payload"] = payload
        else:
            payload = None
        if store.get_publication(widget_id) is None:
            raise _HttpError(404, "unknown_widget", f"no widget with id {widget_id!r}")
        if event == "request_update":
            if not device_id:
                raise _HttpError(403, "device_required", "update requests require a paired device token")
            client_event_id = body.get("clientEventId", payload.get("clientEventId") if isinstance(payload, dict) else None)
            instance_id = body.get("instanceId", payload.get("instanceId") if isinstance(payload, dict) else None)
            event_id = store.post_event(
                widget_id, device_id, "request_update", payload, instance_id=instance_id
            )
            request = store.request_update(
                widget_id, device_id, client_event_id, instance_id=instance_id
            )
            should_trigger = bool(request.pop("_shouldTrigger", False))
            trigger = (
                proactive.trigger_refresh()
                if should_trigger
                else {"triggered": False, "duplicate": True}
            )
            if should_trigger and not trigger.get("triggered"):
                store.mark_update_request_triggered(request["requestId"], error=trigger.get("error"))
                request = store.list_update_requests(widget_id, limit=1)[0]
            elif should_trigger:
                request = store.mark_update_request_triggered(request["requestId"]) or request
            self._json(200, {
                "ok": True,
                "eventId": event_id,
                "request": request,
                "trigger": trigger,
                # Which instance was tapped, echoed back so the app can log it.
                "instanceId": request.get("instanceId"),
                "requestId": self._request_id,
            })
            return
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
    configure_access_log()
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
    retention.start_retention_job()
    httpd = make_server(host, port, certfile=certfile, keyfile=keyfile)
    watch_stop = threading.Event()
    watch_thread = threading.Thread(
        target=_date_watch_loop,
        args=(watch_stop,),
        name="hermes-widget-date-watches",
        daemon=True,
    )
    watch_thread.start()
    wake_thread = threading.Thread(target=_normal_wake_loop, args=(watch_stop,), name="hermes-widget-normal-wakes", daemon=True)
    wake_thread.start()
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
        watch_stop.set()
        watch_thread.join(timeout=WATCH_TICK_INTERVAL_SECONDS + 1)
        wake_thread.join(timeout=10)
        httpd.server_close()


def _date_watch_loop(stop_event: threading.Event) -> None:
    """Evaluate date watches and retry wakes once a minute."""
    while not stop_event.is_set():
        try:
            fired = watches.tick_watches(condition_types={"date_reached"})
            if fired:
                _log.info("date watch tick results: %s", fired)
        except Exception:
            _log.warning("date watch ticker failed", exc_info=True)
        try:
            retried = store.retry_failed_nudges()
            if retried:
                _log.info("retried %d failed priority wake(s)", retried)
        except Exception:
            _log.warning("priority wake retry failed", exc_info=True)
        stop_event.wait(WATCH_TICK_INTERVAL_SECONDS)


def _normal_wake_loop(stop_event: threading.Event) -> None:
    try:
        from .normal_wakes import flush
    except ImportError:
        from normal_wakes import flush
    while not stop_event.is_set():
        try:
            from .refresh import recover_expired
        except ImportError:
            from refresh import recover_expired
        try:
            if recover_expired() and store.has_unconsumed_update_requests():
                proactive.trigger_refresh()
        except Exception:
            _log.warning("interrupted refresh recovery failed", exc_info=True)
        try:
            flush()
        except Exception:
            _log.warning("normal wake dispatch failed", exc_info=True)
        stop_event.wait(1)


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
