"""Publication lifecycle, retrieval, and delivery receipts."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from .db import _LOCK, _connect
    from .errors import (
        ActionIntentError, AssetNotFound, PublicationError, PublicationTooLarge,
        RenderNotReady, StoreError,
    )
    from .timeutil import _as_int, _in_quiet_window, _iso_from_epoch, _now, _validate_timezone
    from . import push as _push
    from .publication import (
        PRIORITY_HIGH_MAX_PER_DAY, PRIORITY_HIGH_MAX_PER_HOUR, PublicationInputError,
        PreparedRegion, prepare_region, prepare_publication,
    )
    from .publication import PublicationTooLarge as _PublicationInputTooLarge
    from .publication import capabilities as _publication_capabilities
    from .bands import BAND_BODY_LINES as _BAND_BODY_LINES
    from .bands import chars_per_line as _chars_per_line
    from .bands import size_band as _size_band
    from .actions import _expire_stale_intents
    from .push_state import DEFAULT_WIDGET_ID, list_push_states, set_device_push_endpoint
    from .assets import _publication_asset_ids, _write_immutable_asset
except ImportError:  # direct import from scripts/tests
    from db import _LOCK, _connect, asset_path  # type: ignore
    from errors import (  # type: ignore
        ActionIntentError, AssetNotFound, PublicationError, PublicationTooLarge,
        RenderNotReady, StoreError,
    )
    from timeutil import _as_int, _in_quiet_window, _iso_from_epoch, _now, _validate_timezone  # type: ignore
    import push as _push  # type: ignore
    from publication import (  # type: ignore
        PRIORITY_HIGH_MAX_PER_DAY, PRIORITY_HIGH_MAX_PER_HOUR, PublicationInputError,
        PreparedRegion, prepare_region, prepare_publication,
    )
    from publication import PublicationTooLarge as _PublicationInputTooLarge  # type: ignore
    from publication import capabilities as _publication_capabilities  # type: ignore
    from bands import BAND_BODY_LINES as _BAND_BODY_LINES  # type: ignore
    from bands import chars_per_line as _chars_per_line  # type: ignore
    from bands import size_band as _size_band  # type: ignore
    from actions import _expire_stale_intents  # type: ignore
    from push_state import DEFAULT_WIDGET_ID, list_push_states, set_device_push_endpoint  # type: ignore
    from assets import _publication_asset_ids, _write_immutable_asset  # type: ignore

DEFAULT_WIDGET_ID = "hermes-brief"
NUDGE_MAX_ATTEMPTS = 5
NUDGE_RETRY_BASE_SECONDS = 60
NUDGE_RETRY_MAX_SECONDS = 3600
HIGH_PRIORITY_WINDOW_SECONDS = 3600
HIGH_PRIORITY_DAY_SECONDS = 24 * 3600
HIGH_PRIORITY_MAX_PER_HOUR = PRIORITY_HIGH_MAX_PER_HOUR
HIGH_PRIORITY_MAX_PER_DAY = PRIORITY_HIGH_MAX_PER_DAY
UPDATE_REQUEST_WINDOW_SECONDS = 60
UPDATE_REQUEST_MAX_PER_DEVICE = 5
UPDATE_REQUEST_SINGLE_FLIGHT_SECONDS = 30
MAX_WIDGET_INSTANCES = 32

# Client build reporting. Deliberately narrow: an app version, a build code and an OS
# API level are what "which build was this?" needs, and nothing that identifies a person
# or a place. Bounded so a hostile client cannot grow the row.
MAX_APP_VERSION_LEN = 32
MAX_APP_BUILD_CODE = 2_147_483_647
MAX_OS_SDK = 100
MAX_APP_SHA_LEN = 20
_APP_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
_APP_SHA_RE = re.compile(r"^[0-9a-f]{7,20}(-dirty)?$")
# Rejections are a diagnostic trail, not an archive: bounded so a flapping client cannot
# grow the file, and pruned oldest-first on insert.
MAX_REJECTION_ROWS = 2_000
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def _publication_semantics(publication: dict) -> dict:
    content = publication.get("content")
    if isinstance(content, dict):
        content = {key: value for key, value in content.items() if key != "assetId"}
    raw_regions = publication.get("regions", {})
    regions: dict[str, Any] = {}
    if isinstance(raw_regions, dict):
        for slot, raw_region in raw_regions.items():
            if not isinstance(raw_region, dict):
                continue
            region = {
                key: value for key, value in raw_region.items()
                if key not in {"publishedAt", "revision", "decayed"}
            }
            region_content = region.get("content")
            if isinstance(region_content, dict):
                region["content"] = {
                    key: value for key, value in region_content.items() if key != "assetId"
                }
            regions[str(slot)] = region
    return {
        "version": publication.get("version"),
        "widgetId": publication.get("widgetId"),
        "kind": publication.get("kind"),
        "title": publication.get("title"),
        "summary": publication.get("summary"),
        "expiresAt": None if publication.get("ttlSeconds") else publication.get("expiresAt"),
        "ttlSeconds": publication.get("ttlSeconds"),
        "maxAgeSeconds": publication.get("maxAgeSeconds"),
        "requestedPriority": publication.get("requestedPriority", publication.get("priority", "normal")),
        "itemId": publication.get("itemId"),
        "actions": publication.get("actions", []),
        "regions": regions,
        "watchId": publication.get("watchId"),
        "provenance": publication.get("provenance"),
        "darkPalette": publication.get("darkPalette", False),
        "variants": publication.get("variants", {}),
        "presentation": publication.get("presentation"),
        "visualVariants": {k: {f: v for f, v in descriptor.items() if f != "assetId"} for k, descriptor in publication.get("visualVariants", {}).items()},
        "content": content,
    }


def _priority_counts(widget_id: str, now: float | None = None) -> tuple[int, int]:
    current = time.time() if now is None else now
    hour = _iso_from_epoch(current - HIGH_PRIORITY_WINDOW_SECONDS)
    day = _iso_from_epoch(current - HIGH_PRIORITY_DAY_SECONDS)
    conn = _connect()
    try:
        hour_count = conn.execute(
            "SELECT COUNT(*) FROM publication_priorities WHERE widget_id = ? "
            "AND requested_priority = 'high' AND created_at > ?",
            (widget_id, hour),
        ).fetchone()[0]
        day_count = conn.execute(
            "SELECT COUNT(*) FROM publication_priorities WHERE widget_id = ? "
            "AND requested_priority = 'high' AND created_at > ?",
            (widget_id, day),
        ).fetchone()[0]
        return int(hour_count), int(day_count)
    finally:
        conn.close()


def _priority_effective(widget_id: str, requested: str) -> tuple[str, str | None]:
    if requested != "high":
        return "normal", None
    settings = get_widget_settings(widget_id)
    quiet = settings.get("quietHours")
    if isinstance(quiet, dict) and _in_quiet_window(
        quiet.get("start"), quiet.get("end"), quiet.get("timezone"), datetime.now(timezone.utc)
    ):
        return "normal", "quiet_hours"
    hour_count, day_count = _priority_counts(widget_id)
    if hour_count >= HIGH_PRIORITY_MAX_PER_HOUR:
        return "normal", "high_priority_hour_limit"
    if day_count >= HIGH_PRIORITY_MAX_PER_DAY:
        return "normal", "high_priority_day_limit"
    return "high", None


def _capacity_warnings_for_publication(publication: dict, widget_id: str) -> list[dict[str, str]]:
    """Advisory fit checks against the smallest band a device registered.

    Bands mirror the device's ladder (validate.size_band), so a warning names the band the
    user would actually see rather than a nominal canvas.
    """
    try:
        from .delivery import list_widget_instances
    except ImportError:  # direct import from scripts/tests
        from delivery import list_widget_instances  # type: ignore
    instances = list_widget_instances(widget_id)
    if not instances:
        return []
    smallest = min(
        (item for item in instances if _size_band(item["widthDp"], item["heightDp"])),
        key=lambda item: (item["heightDp"], item["widthDp"]),
        default=None,
    )
    if smallest is None:
        return []
    band = _size_band(smallest["widthDp"], smallest["heightDp"])
    size = f"{smallest['widthDp']}x{smallest['heightDp']}dp"
    warnings: list[dict[str, str]] = []
    if publication.get("kind") == "text":
        text = (publication.get("content") or {}).get("text", "")
        body_lines = _BAND_BODY_LINES[band]
        per_line = _chars_per_line(smallest["widthDp"])
        if body_lines == 0 and text:
            warnings.append({
                "code": f"BODY_HIDDEN_IN_{band.upper()}",
                "detail": (
                    f"{len(text)} characters of body text cannot render in the {band} band "
                    f"({size}); the smallest instance shows the title"
                    f"{' and summary' if band == 's' else ''} only"
                ),
            })
        elif len(text) > body_lines * per_line:
            warnings.append({
                "code": f"BODY_MAY_SCROLL_{band.upper()}",
                "detail": (
                    f"{len(text)} characters is about {max(1, -(-len(text) // per_line))} body "
                    f"lines (est. {per_line} chars/line at {smallest['widthDp']}dp) against a "
                    f"{band} budget of {body_lines}; the rest is reachable by scrolling"
                ),
            })
        title = str(publication.get("title") or "")
        summary = str(publication.get("summary") or "")
        if len(title) > per_line * 2:
            warnings.append({
                "code": "TITLE_MAY_CLIP",
                "detail": (
                    f"title is {len(title)} characters, about 2 lines at {per_line} "
                    "chars/line; the hero is capped at 2 lines and Glance has no ellipsis"
                ),
            })
        if band in {"xs"} and summary:
            warnings.append({
                "code": "SUMMARY_HIDDEN_IN_XS",
                "detail": f"the summary does not render in the xs band ({size})",
            })
    else:
        content = publication.get("content") or {}
        width, height = content.get("width"), content.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            target_ratio = smallest["widthDp"] / max(1, smallest["heightDp"])
            source_ratio = width / height
            if abs(source_ratio - target_ratio) / target_ratio > 0.35:
                warnings.append({
                    "code": "IMAGE_MAY_LETTERBOX",
                    "detail": f"image aspect {source_ratio:.2f} differs from the smallest registered {smallest['sizeClass']} aspect {target_ratio:.2f}; it will be letterboxed",
                })
    return warnings


def _publication_expired(publication: dict | None, now: datetime | None = None) -> bool:
    if not publication or not publication.get("expiresAt"):
        return False
    current = now or datetime.now(timezone.utc)
    try:
        expiry = datetime.fromisoformat(str(publication["expiresAt"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if expiry.tzinfo is None:
        return True
    return expiry <= current.astimezone(timezone.utc)


def _publication_stale(publication: dict | None, now: datetime | None = None) -> bool:
    """True once a publication is past its maxAgeSeconds freshness window."""
    if not publication:
        return False
    raw = publication.get("maxAgeSeconds")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        return False
    published = publication.get("publishedAt")
    if not published:
        return False
    try:
        published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if published_at.tzinfo is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current - published_at.astimezone(timezone.utc) > timedelta(seconds=raw)




def _dispatch_priority_nudges(widget_id: str, revision: int) -> None:
    """Best-effort, content-free wake delivery for one high-priority revision."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT d.device_id, p.endpoint FROM devices d "
                "LEFT JOIN device_push_endpoints p ON p.device_id = d.device_id "
                "JOIN widget_devices wd ON wd.device_id = d.device_id "
                "WHERE d.revoked = 0 AND wd.widget_id = ? ORDER BY d.device_id",
                (widget_id,),
            ).fetchall()
        finally:
            conn.close()
    for row in rows:
        device_id = str(row["device_id"])
        endpoint = row["endpoint"]
        attempted_at = _now()
        if not endpoint:
            _record_nudge(widget_id, revision, device_id, "not_subscribed", attempted_at, None, "no UnifiedPush endpoint")
            continue
        try:
            _push.wake(str(endpoint))
        except _push.EndpointGone as exc:
            set_device_push_endpoint(device_id, None)
            _record_nudge(widget_id, revision, device_id, "failed", attempted_at, None, str(exc))
            continue
        except ValueError as exc:
            _record_nudge(widget_id, revision, device_id, "failed", attempted_at, None, str(exc))
            continue
        except _push.PushError as exc:
            _record_nudge(
                widget_id, revision, device_id, "failed", attempted_at, None, str(exc),
                retryable=True,
            )
            continue
        sent_at = _now()
        _record_nudge(widget_id, revision, device_id, "sent", attempted_at, sent_at, None)


def _record_nudge(
    widget_id: str,
    revision: int,
    device_id: str,
    status: str,
    attempted_at: str,
    sent_at: str | None,
    detail: str | None,
    *,
    retryable: bool = False,
) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT attempt_count FROM publication_nudges "
                "WHERE widget_id = ? AND revision = ? AND device_id = ?",
                (widget_id, revision, device_id),
            ).fetchone()
            attempt_count = int(previous["attempt_count"] if previous else 0)
            if status in {"failed", "sent"}:
                attempt_count += 1
            next_attempt_at = None
            if retryable and status == "failed" and attempt_count < NUDGE_MAX_ATTEMPTS:
                delay = min(
                    NUDGE_RETRY_BASE_SECONDS * (2 ** (attempt_count - 1)),
                    NUDGE_RETRY_MAX_SECONDS,
                )
                attempted = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
                next_attempt_at = _iso_from_epoch(attempted.timestamp() + delay)
            conn.execute(
                "INSERT INTO publication_nudges "
                "(widget_id, revision, device_id, status, attempted_at, sent_at, detail, "
                "attempt_count, next_attempt_at, claim_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(widget_id, revision, device_id) DO UPDATE SET "
                "status=excluded.status, attempted_at=excluded.attempted_at, "
                "sent_at=excluded.sent_at, detail=excluded.detail, "
                "attempt_count=excluded.attempt_count, next_attempt_at=excluded.next_attempt_at, "
                "claim_until=NULL",
                (widget_id, revision, device_id, status, attempted_at, sent_at, detail,
                 attempt_count, next_attempt_at),
            )
            conn.commit()
        finally:
            conn.close()


def retry_failed_nudges(*, now: datetime | None = None, limit: int = 100) -> int:
    """Retry due transient wake failures with bounded exponential backoff."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    now_iso = current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    claim_until = _iso_from_epoch(current.timestamp() + 300)
    limit = max(1, min(int(limit), 500))
    claimed: list[sqlite3.Row] = []
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            candidates = conn.execute(
                "SELECT n.widget_id, n.revision, n.device_id, p.endpoint "
                "FROM publication_nudges n "
                "JOIN publication_priorities pr ON pr.widget_id=n.widget_id AND pr.revision=n.revision "
                "JOIN device_push_endpoints p ON p.device_id=n.device_id "
                "WHERE n.status='failed' AND n.attempt_count < ? AND n.next_attempt_at <= ? "
                "AND (n.claim_until IS NULL OR n.claim_until <= ?) "
                "AND pr.effective_priority='high' ORDER BY n.next_attempt_at LIMIT ?",
                (NUDGE_MAX_ATTEMPTS, now_iso, now_iso, limit),
            ).fetchall()
            for row in candidates:
                result = conn.execute(
                    "UPDATE publication_nudges SET claim_until=? WHERE widget_id=? AND revision=? "
                    "AND device_id=? AND status='failed' AND attempt_count < ? "
                    "AND next_attempt_at <= ? AND (claim_until IS NULL OR claim_until <= ?)",
                    (claim_until, row["widget_id"], row["revision"], row["device_id"],
                     NUDGE_MAX_ATTEMPTS, now_iso, now_iso),
                )
                if result.rowcount == 1:
                    claimed.append(row)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    for row in claimed:
        widget_id, revision, device_id = row["widget_id"], int(row["revision"]), row["device_id"]
        attempted_at = _now()
        try:
            _push.wake(str(row["endpoint"]))
        except _push.EndpointGone as exc:
            set_device_push_endpoint(str(device_id), None)
            _record_nudge(widget_id, revision, device_id, "failed", attempted_at, None, str(exc))
        except ValueError as exc:
            _record_nudge(widget_id, revision, device_id, "failed", attempted_at, None, str(exc))
        except _push.PushError as exc:
            _record_nudge(
                widget_id, revision, device_id, "failed", attempted_at, None, str(exc),
                retryable=True,
            )
        else:
            _record_nudge(widget_id, revision, device_id, "sent", attempted_at, _now(), None)
    return len(claimed)


def _region_specs(regions: Any = None, hero: Any = None, ticker: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if regions is not None:
        if isinstance(regions, dict):
            result.update(regions)
        elif isinstance(regions, list):
            for index, item in enumerate(regions):
                if not isinstance(item, dict) or item.get("slot") not in {"hero", "ticker"}:
                    raise PublicationError(f"regions[{index}] must name slot hero or ticker")
                result[str(item["slot"])] = item
        else:
            raise PublicationError("regions must be an object or a slot array")
    if hero is not None:
        result["hero"] = hero
    if ticker is not None:
        result["ticker"] = ticker
    for slot in result:
        if slot not in {"hero", "ticker"}:
            raise PublicationError("region slot must be hero or ticker")
    return result


def _legacy_region(payload: dict, slot: str = "hero") -> dict:
    existing = payload.get("regions", {}).get(slot) if isinstance(payload.get("regions"), dict) else None
    if isinstance(existing, dict):
        return dict(existing)
    return {
        "slot": slot,
        "title": payload.get("title", ""),
        "summary": payload.get("summary", ""),
        "priority": payload.get("priority", "normal"),
        "expiresAt": payload.get("expiresAt"),
        "maxAgeSeconds": payload.get("maxAgeSeconds"),
        "itemId": payload.get("itemId"),
        "actions": payload.get("actions", []),
        "content": payload.get("content", {}),
    }


def _region_payload(region: PreparedRegion, asset_id: str | None = None) -> dict:
    return region.metadata(asset_id)


def _region_expired(region: dict, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    resolve_on = region.get("resolveOn")
    if isinstance(resolve_on, str):
        try:
            if current >= datetime.fromisoformat(resolve_on.replace("Z", "+00:00")):
                return True
        except ValueError:
            return True
    expires = region.get("expiresAt")
    if isinstance(expires, str):
        try:
            if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= current.astimezone(timezone.utc):
                return True
        except ValueError:
            return True
    max_age = region.get("maxAgeSeconds")
    if isinstance(max_age, int) and not isinstance(max_age, bool) and max_age > 0:
        published = region.get("publishedAt")
        if isinstance(published, str):
            try:
                stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if current.astimezone(timezone.utc) - stamp.astimezone(timezone.utc) > timedelta(seconds=max_age):
                    return True
            except ValueError:
                return True
    return False


def _publication_regions_for_device(publication: dict, now: datetime | None = None) -> dict:
    regions = publication.get("regions")
    if not isinstance(regions, dict):
        return {}
    result: dict[str, Any] = {}
    for slot, raw in regions.items():
        if not isinstance(raw, dict):
            continue
        value = dict(raw)
        if _region_expired(value, now):
            content = value.get("content")
            if isinstance(content, dict):
                value["content"] = {"type": "text", "mediaType": "text/plain; charset=utf-8", "text": ""}
            value["decayed"] = True
        result[slot] = value
    return result


def put_publication(
    widget_id: str,
    *,
    title: Any,
    summary: Any,
    text: str | None = None,
    svg: str | None = None,
    file_path: str | os.PathLike[str] | None = None,
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
    max_age_seconds: int | None = None,
    priority: str = "normal",
    item_id: str | None = None,
    actions: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    regions: Any = None,
    hero: Any = None,
    ticker: Any = None,
    watch_id: str | None = None,
    preserve_regions: bool = False,
    provenance: str | None = None,
    dark_palette: bool = False,
    variants: Any = None,
    presentation: Any = None,
    visual_variants: Any = None,
    work_context: Any = None,
    refresh_id: str | None = None,
) -> dict:
    """Validate, store, and atomically publish one text or visual revision."""
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise PublicationError("widget_id must be a non-empty string of at most 128 characters")
    try:
        prepared = prepare_publication(
            title=title,
            summary=summary,
            text=text,
            svg=svg,
            file_path=file_path,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            max_age_seconds=max_age_seconds,
            priority=priority,
            item_id=item_id,
            actions=actions,
            provenance=provenance,
            dark_palette=dark_palette,
            variants=variants,
            presentation=presentation,
            visual_variants=visual_variants,
        )
    except _PublicationInputTooLarge as exc:
        raise PublicationTooLarge(str(exc)) from exc
    except PublicationInputError as exc:
        raise PublicationError(str(exc)) from exc

    try:
        from .refresh import normalize_context, save_context, finish
    except ImportError:
        from refresh import normalize_context, save_context, finish
    context = normalize_context(work_context, prepared.presentation)

    prepared_ticker: PreparedRegion | None = None
    if ticker is not None:
        try:
            prepared_ticker = prepare_region("ticker", ticker, now=datetime.now(timezone.utc))
        except _PublicationInputTooLarge as exc:
            raise PublicationTooLarge(str(exc)) from exc
        except PublicationInputError as exc:
            raise PublicationError(str(exc)) from exc

    effective_priority, degraded_reason = _priority_effective(widget_id, prepared.priority)
    content: dict[str, Any]
    if prepared.kind == "text":
        content = {"type": "text", "mediaType": "text/plain; charset=utf-8", "text": prepared.text}
    else:
        assert prepared.asset is not None
        content = {
            "type": "image",
            "mediaType": prepared.asset.media_type,
            "width": prepared.asset.width,
            "height": prepared.asset.height,
            "bytes": len(prepared.asset.data),
            "sha256": prepared.asset.sha256,
        }
    now = _now()
    new_asset_paths: list[Path] = []
    dispatch_priority = False
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            old_row = conn.execute(
                "SELECT revision, payload_json FROM publications WHERE widget_id = ?",
                (widget_id,),
            ).fetchone()
            if old_row:
                try:
                    old_payload = json.loads(old_row["payload_json"])
                except (TypeError, ValueError) as exc:
                    raise StoreError("stored publication is corrupt") from exc
            carried_regions = (
                dict(old_payload.get("regions", {}))
                if old_row and isinstance(old_payload.get("regions"), dict)
                else {}
            )
            candidate_regions = dict(carried_regions)
            retained_ttl = old_payload.get("ttlSeconds") if preserve_regions and old_row else ttl_seconds
            if preserve_regions and old_row:
                prior_context = conn.execute("SELECT context_json FROM widget_work_context WHERE widget_id=?", (widget_id,)).fetchone()
                if prior_context:
                    context = json.loads(prior_context[0])
            if prepared_ticker is not None:
                candidate_regions["ticker"] = prepared_ticker.metadata()
            candidate = {
                "version": _publication_capabilities()["publicationVersion"],
                "widgetId": widget_id,
                "kind": prepared.kind,
                "title": prepared.title,
                "summary": prepared.summary,
                "expiresAt": prepared.expires_at,
                "ttlSeconds": retained_ttl,
                "maxAgeSeconds": prepared.max_age_seconds,
                "priority": effective_priority,
                "requestedPriority": prepared.priority,
                "priorityDegradedReason": degraded_reason,
                "itemId": prepared.item_id,
                "actions": list(prepared.actions),
                "watchId": watch_id,
                "provenance": prepared.provenance,
                "darkPalette": prepared.dark_palette,
                "variants": prepared.variants or {},
                "presentation": prepared.presentation,
                "visualVariants": {key: asset.metadata() for key, asset in (prepared.visual_variants or {}).items()},
                "content": content,
                "regions": candidate_regions,
            }
            if (
                old_row
                and not _publication_expired(old_payload)
                and _publication_semantics(old_payload) == _publication_semantics(candidate)
            ):
                if work_context is not None:
                    save_context(widget_id, old_payload["revision"], context, conn)
                if refresh_id:
                    finish(widget_id, refresh_id, "unchanged", "rechecked content is unchanged", old_payload["revision"], conn)
                conn.commit()
                result = dict(old_payload)
                result["unchanged"] = True
                return result

            if prepared.asset is not None:
                asset_id, asset_path_value = _write_immutable_asset(conn, prepared.asset)
                if asset_path_value is not None:
                    new_asset_paths.append(asset_path_value)
                content = {**content, "assetId": asset_id}
            visual_descriptors = {}
            for key, visual_asset in (prepared.visual_variants or {}).items():
                visual_id, visual_path = _write_immutable_asset(conn, visual_asset)
                if visual_path is not None:
                    new_asset_paths.append(visual_path)
                visual_descriptors[key] = {**visual_asset.metadata(), "assetId": visual_id}
            revision = _as_int(old_row["revision"], "publication revision") + 1 if old_row else 1
            published_regions = dict(carried_regions)
            if prepared_ticker is not None:
                ticker_asset_id = None
                if prepared_ticker.asset is not None:
                    ticker_asset_id, ticker_asset_path = _write_immutable_asset(conn, prepared_ticker.asset)
                    if ticker_asset_path is not None:
                        new_asset_paths.append(ticker_asset_path)
                ticker_region = prepared_ticker.metadata(ticker_asset_id)
                ticker_region["publishedAt"] = now
                ticker_region["revision"] = revision
                published_regions["ticker"] = ticker_region
            publication_id = "pub_" + uuid.uuid4().hex
            publication = {
                "version": _publication_capabilities()["publicationVersion"],
                "widgetId": widget_id,
                "publicationId": publication_id,
                "revision": revision,
                "kind": prepared.kind,
                "title": prepared.title,
                "summary": prepared.summary,
                "publishedAt": now,
                "expiresAt": prepared.expires_at,
                "ttlSeconds": retained_ttl,
                "maxAgeSeconds": prepared.max_age_seconds,
                "priority": effective_priority,
                "requestedPriority": prepared.priority,
                "priorityDegradedReason": degraded_reason,
                "itemId": prepared.item_id,
                "actions": list(prepared.actions),
                "watchId": watch_id,
                "provenance": prepared.provenance,
                "darkPalette": prepared.dark_palette,
                "variants": prepared.variants or {},
                "presentation": prepared.presentation,
                "visualVariants": visual_descriptors,
                "content": content,
                "regions": published_regions,
            }
            if "ticker" in published_regions:
                publication["ticker"] = published_regions["ticker"]
            conn.execute(
                "INSERT INTO publications (widget_id, revision, publication_id, payload_json, published_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(widget_id) DO UPDATE SET revision=excluded.revision, "
                "publication_id=excluded.publication_id, payload_json=excluded.payload_json, "
                "published_at=excluded.published_at, expires_at=excluded.expires_at",
                (
                    widget_id,
                    revision,
                    publication_id,
                    json.dumps(publication, separators=(",", ":"), ensure_ascii=False),
                    now,
                    prepared.expires_at,
                ),
            )
            # Keep every revision as history instead of letting the current-row
            # upsert erase it; mark a superseded revision so "not polled yet" is
            # distinguishable from "lost".
            conn.execute(
                "INSERT INTO publication_revisions "
                "(widget_id, revision, published_at, expires_at, max_age_seconds, kind, title, summary, payload_json, watch_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(widget_id, revision) DO UPDATE SET payload_json=excluded.payload_json, watch_id=excluded.watch_id",
                (
                    widget_id,
                    revision,
                    now,
                    prepared.expires_at,
                    prepared.max_age_seconds,
                    prepared.kind,
                    prepared.title,
                    prepared.summary,
                    json.dumps(publication, separators=(",", ":"), ensure_ascii=False),
                    watch_id,
                ),
            )
            conn.execute(
                "INSERT INTO publication_priorities "
                "(widget_id, revision, requested_priority, effective_priority, degraded_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (widget_id, revision, prepared.priority, effective_priority, degraded_reason, now),
            )
            newly_superseded = [
                int(row["revision"])
                for row in conn.execute(
                    "SELECT revision FROM publication_revisions WHERE widget_id = ? AND revision < ? AND superseded_at IS NULL",
                    (widget_id, revision),
                ).fetchall()
            ]
            conn.execute(
                "UPDATE publication_revisions SET superseded_at = ?, "
                "superseded_reason = COALESCE(superseded_reason, 'superseded_by_revision_' || ?) "
                "WHERE widget_id = ? AND revision < ? AND superseded_at IS NULL",
                (now, revision, widget_id, revision),
            )
            for device in conn.execute("SELECT device_id FROM devices WHERE revoked = 0").fetchall():
                for old_revision in newly_superseded if not preserve_regions else []:
                    fetched = conn.execute(
                        "SELECT 1 FROM publication_fetches WHERE widget_id = ? AND device_id = ? AND revision = ?",
                        (widget_id, device["device_id"], old_revision),
                    ).fetchone()
                    if fetched is None:
                        conn.execute(
                            "INSERT INTO attention_aggregates (device_id, widget_id, revision, superseded_before_fetch, updated_at) "
                            "VALUES (?, ?, ?, 1, ?) ON CONFLICT(device_id, widget_id, revision) DO UPDATE SET "
                            "superseded_before_fetch=superseded_before_fetch+1, updated_at=excluded.updated_at",
                            (device["device_id"], widget_id, old_revision, now),
                        )
            for device in conn.execute("SELECT device_id FROM devices WHERE revoked = 0").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO widget_devices (widget_id, device_id) VALUES (?, ?)",
                    (widget_id, device["device_id"]),
                )
            save_context(widget_id, revision, context, conn)
            if refresh_id:
                finish(widget_id, refresh_id, "published", "", revision, conn)
            if effective_priority == "normal":
                try:
                    from .normal_wakes import queue_wake
                except ImportError:
                    from normal_wakes import queue_wake
                queue_wake(widget_id, revision, conn)
            conn.commit()
            dispatch_priority = effective_priority == "high"
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            for new_asset_path in new_asset_paths:
                with contextlib.suppress(OSError):
                    new_asset_path.unlink()
            raise
        finally:
            conn.close()
    if dispatch_priority:
        try:
            _dispatch_priority_nudges(widget_id, revision)
        except Exception:  # noqa: BLE001 - a wake failure must not lose a publication
            logger.warning("priority wake dispatch failed", exc_info=True)
    else:
        try:
            from .normal_wakes import flush
        except ImportError:
            from normal_wakes import flush
        try:
            flush()
        except Exception:
            logger.warning("normal wake dispatch failed; durable queue retained", exc_info=True)
    try:
        publication["warnings"] = _capacity_warnings_for_publication(publication, widget_id)
    except Exception:  # noqa: BLE001 - advisory warnings must not fail a commit
        publication["warnings"] = []
    return publication


def get_widget_settings(widget_id: str) -> dict:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT quiet_start, quiet_end, quiet_timezone, updated_at FROM widget_settings WHERE widget_id = ?",
            (widget_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["quiet_start"] or not row["quiet_end"]:
        return {"widgetId": widget_id, "quietHours": None, "updatedAt": row["updated_at"] if row else None}
    return {
        "widgetId": widget_id,
        "quietHours": {
            "start": row["quiet_start"],
            "end": row["quiet_end"],
            "timezone": row["quiet_timezone"] or "UTC",
        },
        "updatedAt": row["updated_at"],
    }


def ensure_widget(widget_id: str) -> None:
    """Register a widget id in its settings row without publishing content."""
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise StoreError("widget_id is required")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO widget_settings (widget_id, updated_at) VALUES (?, ?)",
                (widget_id, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def set_widget_settings(widget_id: str, quiet_hours: Any) -> dict:
    if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 128:
        raise StoreError("widget_id is required")
    clean = None
    if quiet_hours is not None:
        if not isinstance(quiet_hours, dict):
            raise StoreError("quiet_hours must be an object or null")
        start, end = quiet_hours.get("start"), quiet_hours.get("end")
        time_pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
        if (not isinstance(start, str) or not isinstance(end, str)
                or not re.fullmatch(time_pattern, start)
                or not re.fullmatch(time_pattern, end)):
            raise StoreError("quiet_hours start/end must use HH:MM")
        clean = {"start": start, "end": end, "timezone": _validate_timezone(quiet_hours.get("timezone"))}
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO widget_settings (widget_id, quiet_start, quiet_end, quiet_timezone, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(widget_id) DO UPDATE SET quiet_start=excluded.quiet_start, "
                "quiet_end=excluded.quiet_end, quiet_timezone=excluded.quiet_timezone, updated_at=excluded.updated_at",
                (widget_id, clean["start"] if clean else None, clean["end"] if clean else None,
                 clean["timezone"] if clean else "UTC", now),
            )
            conn.commit()
        finally:
            conn.close()
    return get_widget_settings(widget_id)


def put_ticker(
    widget_id: str,
    *,
    title: str,
    summary: str,
    text: str | None = None,
    svg: str | None = None,
    file_path: str | os.PathLike[str] | None = None,
    expires_at: str | datetime | None = None,
    ttl_seconds: int | None = None,
    max_age_seconds: int | None = None,
    priority: str = "normal",
    item_id: str | None = None,
    actions: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    provenance: str | None = None,
    pinned: bool = False,
    rotate: bool = False,
) -> dict:
    """Publish an independent ticker while retaining the current hero content."""
    current = get_publication(widget_id)
    if current is None:
        raise PublicationError("a ticker requires an existing hero publication")
    content = current.get("content") or {}
    hero_kwargs: dict[str, Any] = {
        "title": current.get("title", ""),
        "summary": current.get("summary", ""),
        "expires_at": current.get("expiresAt"),
        "max_age_seconds": current.get("maxAgeSeconds"),
        "priority": priority,
        "item_id": current.get("itemId"),
        "actions": current.get("actions", []),
        "provenance": current.get("provenance"),
        "dark_palette": bool(current.get("darkPalette", False)),
        "variants": current.get("variants", {}),
    }
    def image_source(descriptor):
        asset = descriptor.get("assetId")
        if not isinstance(asset, str):
            raise PublicationError("current hero image asset is unavailable")
        path = asset_path(asset)
        if descriptor.get("mediaType") == "image/svg+xml":
            return {"svg": path.read_text(encoding="utf-8")}
        return {"file_path": str(path)}

    if current.get("presentation"):
        hero_kwargs["presentation"] = current["presentation"]
    elif content.get("type") == "text":
        hero_kwargs["text"] = content.get("text", "")
    else:
        hero_kwargs.update(image_source(content))
        hero_kwargs["visual_variants"] = {key: image_source(descriptor) for key, descriptor in current.get("visualVariants", {}).items()}
    ticker_spec: dict[str, Any] = {
        "title": title,
        "summary": summary,
        "priority": priority,
        "maxAgeSeconds": max_age_seconds,
        "expiresAt": expires_at,
        "ttlSeconds": ttl_seconds,
        "itemId": item_id,
        "actions": actions,
        "provenance": provenance,
        "pinned": pinned,
        "rotate": rotate,
    }
    if text is not None:
        ticker_spec["text"] = text
    if svg is not None:
        ticker_spec["svg"] = svg
    if file_path is not None:
        ticker_spec["file_path"] = file_path
    return put_publication(widget_id, ticker=ticker_spec, preserve_regions=True, **hero_kwargs)




def ask_question(
    widget_id: str,
    prompt: Any,
    *,
    item_id: str | None = None,
    revision: int | None = None,
) -> dict:
    publication = get_publication(widget_id)
    if publication is None:
        raise StoreError("a question requires a current publication")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 500:
        raise StoreError("question prompt must be 1-500 characters")
    selected_revision = int(revision or publication.get("revision", 0))
    if selected_revision < 1:
        raise StoreError("question revision is invalid")
    now = _now()
    question_id = "question_" + uuid.uuid4().hex[:24]
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO widget_questions (question_id, widget_id, revision, item_id, prompt, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                (question_id, widget_id, selected_revision, item_id, prompt.strip(), now, now),
            )
            conn.commit()
        finally:
            conn.close()
    return {"questionId": question_id, "widgetId": widget_id, "revision": selected_revision, "itemId": item_id, "prompt": prompt.strip(), "status": "open", "createdAt": now}


def answer_question(device_id: str, question_id: str, text: Any) -> dict:
    if not device_id:
        raise ActionIntentError("a device token is required to answer")
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise StoreError("answer must be 1-500 characters")
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            device = conn.execute("SELECT device_id FROM devices WHERE device_id = ? AND revoked = 0", (device_id,)).fetchone()
            if device is None:
                raise ActionIntentError("device is revoked or unknown")
            row = conn.execute("SELECT * FROM widget_questions WHERE question_id = ?", (question_id,)).fetchone()
            if row is None:
                raise StoreError("question does not exist")
            if row["status"] != "open":
                return {"questionId": question_id, "status": row["status"], "answer": row["answer"]}
            conn.execute(
                "UPDATE widget_questions SET status='answered', answer=?, answered_at=?, updated_at=? WHERE question_id=?",
                (text.strip(), now, now, question_id),
            )
            conn.execute(
                "INSERT INTO action_audit (intent_id, widget_id, device_id, item_id, action_class, revision, event, outcome, detail, created_at) "
                "VALUES (NULL, ?, ?, ?, 'read_only', ?, 'answer', 'answered', ?, ?)",
                (row["widget_id"], device_id, row["item_id"] or question_id, row["revision"], text.strip()[:200], now),
            )
            conn.commit()
            return {"questionId": question_id, "status": "answered", "answer": text.strip(), "answeredAt": now}
        finally:
            conn.close()


def list_questions(widget_id: str | None = None, *, status: str | None = None, limit: int = 100) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    clauses: list[str] = []
    params: list[Any] = []
    if widget_id:
        clauses.append("widget_id = ?")
        params.append(widget_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT * FROM widget_questions{where} ORDER BY created_at DESC LIMIT ?", params).fetchall()
    finally:
        conn.close()
    return [
        {
            "questionId": row["question_id"], "widgetId": row["widget_id"], "revision": row["revision"],
            "itemId": row["item_id"], "prompt": row["prompt"], "status": row["status"], "answer": row["answer"],
            "createdAt": row["created_at"], "answeredAt": row["answered_at"],
        }
        for row in rows
    ]


def _open_question_for_publication(widget_id: str, revision: int) -> dict | None:
    rows = list_questions(widget_id, status="open", limit=10)
    for row in rows:
        if int(row["revision"]) == int(revision):
            return row
    return rows[0] if rows else None


def get_publication(widget_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM publications WHERE widget_id = ?", (widget_id,)
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    try:
        publication = json.loads(row["payload_json"])
    except (TypeError, ValueError) as exc:
        raise StoreError("stored publication is corrupt") from exc
    if not isinstance(publication, dict):
        raise StoreError("stored publication is corrupt")
    publication["expired"] = _publication_expired(publication)
    publication["stale"] = _publication_stale(publication)
    device_regions = _publication_regions_for_device(publication)
    if device_regions:
        publication["regions"] = device_regions
    question = _open_question_for_publication(widget_id, int(publication.get("revision", 0)))
    if question is not None:
        publication["question"] = {
            "questionId": question["questionId"],
            "itemId": question["itemId"],
            "prompt": question["prompt"],
            "status": question["status"],
        }
    # Add live intent outcomes without rewriting the immutable publication row.
    item_ids = {
        item for item in (
            [publication.get("itemId")] if isinstance(publication.get("itemId"), str) else []
        ) if item
    }
    item_ids.update(
        action.get("itemId") for action in publication.get("actions", [])
        if isinstance(action, dict) and isinstance(action.get("itemId"), str)
    )
    if item_ids:
        with _LOCK:
            conn = _connect()
            try:
                _expire_stale_intents(conn)
                conn.commit()
                placeholders = ",".join("?" for _ in item_ids)
                rows = conn.execute(
                    f"SELECT item_id, status, result, updated_at FROM action_intents "
                    f"WHERE widget_id = ? AND item_id IN ({placeholders}) ORDER BY updated_at DESC",
                    [widget_id, *item_ids],
                ).fetchall()
            finally:
                conn.close()
        latest: dict[str, dict] = {}
        for row in rows:
            latest.setdefault(str(row["item_id"]), {
                "status": row["status"], "result": row["result"], "updatedAt": row["updated_at"]
            })
        if latest:
            publication["actionStates"] = latest
    return publication


def record_publication_fetch(
    widget_id: str, device_id: str, revision: int, *, downloaded: bool = False
) -> None:
    """Record a successful authenticated metadata fetch for a device."""
    if not device_id:
        return
    revision_value = _as_int(revision, "publication revision")
    with _LOCK:
        conn = _connect()
        try:
            timestamp = _now()
            conn.execute(
                "INSERT INTO publication_fetches (widget_id, device_id, revision, fetched_at, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) DO UPDATE SET "
                "fetched_at=excluded.fetched_at, downloaded_at=COALESCE(publication_fetches.downloaded_at, excluded.downloaded_at)",
                (widget_id, device_id, revision_value, timestamp, timestamp if downloaded else None),
            )
            conn.commit()
        finally:
            conn.close()


def record_asset_download(widget_id: str, device_id: str, revision: int, asset_id: str) -> None:
    """Record a successful complete asset download after metadata validation."""
    publication = get_publication(widget_id)
    revision_value = _as_int(revision, "publication revision")
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    if not publication or revision_value != current_revision:
        raise RenderNotReady("publication revision is not current")
    if asset_id not in _publication_asset_ids(publication):
        raise AssetNotFound("asset does not belong to this publication")
    with _LOCK:
        conn = _connect()
        try:
            timestamp = _now()
            conn.execute(
                "INSERT INTO publication_fetches (widget_id, device_id, revision, fetched_at, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) DO UPDATE SET "
                "fetched_at=excluded.fetched_at, downloaded_at=excluded.downloaded_at",
                (widget_id, device_id, revision_value, timestamp, timestamp),
            )
            conn.commit()
        finally:
            conn.close()


def acknowledge_publication_render(
    widget_id: str, device_id: str, revision: int, width: int, height: int,
    *, status: str = "render_submitted",
) -> dict:
    """Record a render submission, never a claim that the user saw it."""
    if not device_id:
        raise RenderNotReady("a device token is required")
    if status not in {"render_submitted", "rendered"}:
        raise PublicationError("render status must be render_submitted or rendered")
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int):
        raise PublicationError("render dimensions must be integers")
    if not 1 <= width <= 10_000 or not 1 <= height <= 10_000:
        raise PublicationError("render dimensions are out of range")
    publication = get_publication(widget_id)
    revision_value = _as_int(revision, "publication revision")
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    if not publication or revision_value > current_revision:
        raise RenderNotReady("publication revision is not available")
    with _LOCK:
        conn = _connect()
        try:
            fetched = conn.execute(
                "SELECT downloaded_at FROM publication_fetches WHERE widget_id = ? AND device_id = ? AND revision = ?",
                (widget_id, device_id, revision_value),
            ).fetchone()
            if fetched is None or not fetched["downloaded_at"]:
                raise RenderNotReady("publication must be downloaded before render acknowledgement")
            timestamp = _now()
            conn.execute(
                "INSERT INTO publication_acks "
                "(widget_id, device_id, revision, rendered_at, width, height, render_confirmed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(widget_id, device_id, revision) DO UPDATE SET "
                "rendered_at=excluded.rendered_at, width=excluded.width, height=excluded.height, "
                "render_confirmed_at=COALESCE(excluded.render_confirmed_at, publication_acks.render_confirmed_at)",
                (widget_id, device_id, revision_value, timestamp, width, height,
                 timestamp if status == "rendered" else None),
            )
            conn.commit()
        finally:
            conn.close()
    return {
        "ok": True,
        "widgetId": widget_id,
        "revision": revision_value,
        "status": status,
        "renderedAt": timestamp,
        "width": width,
        "height": height,
    }






def publication_status(
    widget_id: str = DEFAULT_WIDGET_ID,
    *,
    consume_update_requests_now: bool = False,
    limit: int = 50,
    summary: bool = False,
) -> dict:
    try:
        from .delivery import (
            attention_summary, consume_update_requests,
            list_update_requests, list_widget_instances, rejection_summary,
        )
        from .actions import get_action_audit
    except ImportError:  # direct import from scripts/tests
        from delivery import (  # type: ignore
            attention_summary, consume_update_requests, list_update_requests, list_widget_instances, rejection_summary,
        )
        from actions import get_action_audit  # type: ignore
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50
    if summary:
        limit = min(limit, 10)
    newly_consumed = consume_update_requests(widget_id) if consume_update_requests_now else []
    publication = get_publication(widget_id)
    with _LOCK:
        conn = _connect()
        try:
            devices = conn.execute(
                "SELECT device_id, label, revoked FROM devices ORDER BY created_at"
            ).fetchall()
            fetches = {
                (row["device_id"], int(row["revision"])): row
                for row in conn.execute(
                    "SELECT device_id, revision, fetched_at, downloaded_at FROM publication_fetches "
                    "WHERE widget_id = ?",
                    (widget_id,),
                ).fetchall()
            }
            acks = {
                (row["device_id"], int(row["revision"])): row
                for row in conn.execute(
                    "SELECT device_id, revision, rendered_at, render_confirmed_at, width, height "
                    "FROM publication_acks "
                    "WHERE widget_id = ?",
                    (widget_id,),
                ).fetchall()
            }
            render_builds = {
                (row["device_id"], int(row["revision"])): {
                    "appVersion": row["app_version"],
                    "appBuildCode": row["app_build_code"],
                    "appBuildSha": row["app_build_sha"],
                    "instanceId": row["instance_id"],
                    "reportedAt": row["reported_at"],
                }
                for row in conn.execute(
                    "SELECT device_id, revision, app_version, app_build_code, app_build_sha, "
                    "instance_id, reported_at FROM publication_render_builds WHERE widget_id = ?",
                    (widget_id,),
                ).fetchall()
            }
            client_info = {
                row["device_id"]: {
                    "appVersion": row["app_version"],
                    "appBuildCode": row["app_build_code"],
                    "appBuildSha": row["app_build_sha"],
                    "osSdk": row["os_sdk"],
                    "firstReportedAt": row["first_reported_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in conn.execute(
                    "SELECT device_id, app_version, app_build_code, app_build_sha, os_sdk, "
                    "first_reported_at, updated_at FROM device_client_info"
                ).fetchall()
            }
            nudges = {
                (row["device_id"], int(row["revision"])): row
                for row in conn.execute(
                    "SELECT device_id, revision, status, attempted_at, sent_at, detail "
                    "FROM publication_nudges WHERE widget_id = ?", (widget_id,)
                ).fetchall()
            }
            priorities = {
                int(row["revision"]): {
                    "requested_priority": row["requested_priority"],
                    "effective_priority": row["effective_priority"],
                    "degraded_reason": row["degraded_reason"],
                }
                for row in conn.execute(
                    "SELECT revision, requested_priority, effective_priority, degraded_reason "
                    "FROM publication_priorities WHERE widget_id = ?", (widget_id,)
                ).fetchall()
            }
            intent_rows = conn.execute(
                "SELECT intent_id, item_id, action_class, status, source_revision, result, created_at, updated_at "
                "FROM action_intents WHERE widget_id = ? ORDER BY created_at DESC LIMIT 200", (widget_id,)
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT revision, published_at, expires_at, max_age_seconds, superseded_at, "
                "superseded_reason, kind, title FROM publication_revisions "
                "WHERE widget_id = ? ORDER BY revision DESC",
                (widget_id,),
            ).fetchall()
        finally:
            conn.close()
    current_revision = _as_int(publication.get("revision", 0), "publication revision") if publication else 0
    recorded_revisions = [
        _as_int(row["revision"], "publication revision") for row in revision_rows
    ]
    history_max = max(recorded_revisions, default=0)
    recorded_set = set(recorded_revisions)
    missing_history = (
        [revision for revision in range(1, current_revision + 1) if revision not in recorded_set]
        if 0 < current_revision <= 10_000
        else []
    )
    history_gap = bool(
        current_revision
        and (current_revision > history_max or len(recorded_set) < current_revision)
    )
    history_warnings: list[dict[str, Any]] = []
    if history_gap:
        history_warnings.append({
            "code": "revision_history_gap",
            "detail": (
                f"publications is at revision {current_revision}, but publication_revisions "
                f"only reaches {history_max} ({len(recorded_revisions)} rows); revision history "
                "may have been removed or truncated, so skipped revisions cannot be treated as complete"
            ),
            "currentRevision": current_revision,
            "maxRecordedRevision": history_max,
            "recordedCount": len(recorded_revisions),
            "missingRevisions": missing_history[:100],
            "missingRevisionCount": current_revision - len(recorded_set) if current_revision else 0,
        })
    revision_history = {
        "currentRevision": current_revision,
        "maxRecordedRevision": history_max,
        "recordedCount": len(recorded_revisions),
        "missingRevisions": missing_history[:100],
        "missingRevisionCount": current_revision - len(recorded_set) if current_revision else 0,
        "gap": history_gap,
    }
    stale = bool(publication) and _publication_stale(publication)
    delivery = []
    receipt_map: dict[tuple[str, int], list[dict[str, Any]]] = {}
    receipt_rank = {"nudge_sent": 0, "fetched": 1, "downloaded": 2, "render_submitted": 3, "rendered": 4}
    for (device_id, revision), row in fetches.items():
        key = (str(device_id), int(revision))
        events = receipt_map.setdefault(key, [])
        if row["fetched_at"]:
            events.append({"state": "fetched", "at": row["fetched_at"], "detail": None})
        if row["downloaded_at"]:
            events.append({"state": "downloaded", "at": row["downloaded_at"], "detail": None})
    for (device_id, revision), row in acks.items():
        key = (str(device_id), int(revision))
        events = receipt_map.setdefault(key, [])
        if row["rendered_at"]:
            events.append({"state": "render_submitted", "at": row["rendered_at"], "detail": None})
        if row["render_confirmed_at"]:
            events.append({"state": "rendered", "at": row["render_confirmed_at"], "detail": None})
    for (device_id, revision), row in nudges.items():
        if row["status"] == "sent" and row["sent_at"]:
            receipt_map.setdefault((str(device_id), int(revision)), []).append({
                "state": "nudge_sent", "at": row["sent_at"], "detail": row["detail"],
            })
    for items in receipt_map.values():
        items.sort(key=lambda item: (receipt_rank.get(item["state"], 99), item["at"]))
    for device in devices:
        device_id = str(device["device_id"])
        device_fetches = {
            revision: row for (owner, revision), row in fetches.items() if owner == device_id
        }
        current_fetch = device_fetches.get(current_revision)
        current_ack = acks.get((device_id, current_revision))
        current_nudge = nudges.get((device_id, current_revision))
        downloaded = bool(current_fetch and current_fetch["downloaded_at"])
        render_submitted = bool(current_ack)
        state = "render_submitted" if render_submitted else "downloaded" if downloaded else "not_downloaded"
        last_fetched_revision = max(device_fetches) if device_fetches else None
        last_poll_at = max(
            (row["fetched_at"] for row in device_fetches.values() if row["fetched_at"]),
            default=None,
        )
        # Revisions that were replaced before this device ever fetched them are
        # "skipped", not "not_downloaded": the publisher can tell they were lost.
        skipped_revisions = [
            _as_int(row["revision"], "publication revision")
            for row in revision_rows
            if row["superseded_at"]
            and _as_int(row["revision"], "publication revision") not in device_fetches
        ]
        delivery.append(
            {
                "deviceId": device_id,
                "label": device["label"],
                "revoked": bool(device["revoked"]),
                "state": state,
                "downloaded": downloaded,
                "downloadedAt": current_fetch["downloaded_at"] if current_fetch else None,
                "renderSubmitted": render_submitted,
                "renderSubmittedAt": current_ack["rendered_at"] if current_ack else None,
                "rendered": bool(current_ack and any(
                    receipt["state"] == "rendered"
                    for receipt in receipt_map.get((device_id, current_revision), [])
                )),
                "renderedAt": current_ack["rendered_at"] if current_ack and any(
                    receipt["state"] == "rendered"
                    for receipt in receipt_map.get((device_id, current_revision), [])
                ) else None,
                "renderedWidth": current_ack["width"] if current_ack else None,
                "renderedHeight": current_ack["height"] if current_ack else None,
                # Which build rendered this revision, and which build the phone is on now.
                # Both are null for an app that predates build reporting; neither is invented.
                "renderedBy": render_builds.get((device_id, current_revision)),
                "client": client_info.get(device_id),
                "nudgeStatus": current_nudge["status"] if current_nudge else "not_sent",
                "nudgeSentAt": current_nudge["sent_at"] if current_nudge else None,
                "fetchedAt": current_fetch["fetched_at"] if current_fetch else None,
                "receipts": receipt_map.get((device_id, current_revision), []),
                "lastFetchedRevision": last_fetched_revision,
                "lastPollAt": last_poll_at,
                "skippedRevisions": skipped_revisions,
            }
        )
    revisions = [
        {
            "revision": _as_int(row["revision"], "publication revision"),
            "publishedAt": row["published_at"],
            "expiresAt": row["expires_at"],
            "maxAgeSeconds": row["max_age_seconds"],
            "kind": row["kind"],
            "title": row["title"],
            "superseded": bool(row["superseded_at"]),
            "supersededAt": row["superseded_at"],
            "supersededReason": row["superseded_reason"],
            "priority": priorities.get(_as_int(row["revision"], "publication revision"), {}).get("effective_priority", "normal") if priorities else "normal",
            "requestedPriority": priorities.get(_as_int(row["revision"], "publication revision"), {}).get("requested_priority", "normal") if priorities else "normal",
            "priorityDegradedReason": priorities.get(_as_int(row["revision"], "publication revision"), {}).get("degraded_reason") if priorities else None,
        }
        for row in revision_rows
    ]
    anomalies = [
        {
            "type": "high_priority_skipped",
            "revision": revision,
            "detail": "high-priority revision was superseded before any device fetched it",
        }
        for revision, priority in priorities.items()
        if devices
        and priority.get("effective_priority") == "high"
        and any(
            int(row["revision"]) == revision and row["superseded_at"]
            for row in revision_rows
        )
        and not any(owner_revision[1] == revision for owner_revision in fetches)
    ]
    push_states = list_push_states()
    result = {
        "widgetId": widget_id,
        "state": (
            "empty"
            if not publication
            else "stale"
            if stale
            else "expired"
            if publication.get("expired")
            else "published"
        ),
        "stale": stale,
        "publication": publication,
        "revisions": revisions,
        "revisionHistory": revision_history,
        "warnings": history_warnings,
        "wake": {
            "devices": push_states,
            "registeredCount": sum(1 for item in push_states if item["registered"]),
        },
        "attention": attention_summary(widget_id),
        "updateRequests": list_update_requests(widget_id),
        "newlyConsumedUpdateRequests": newly_consumed,
        # Refused device requests: the answer when a tap produced no row.
        "rejections": rejection_summary(widget_id),
        "delivery": delivery,
        "inventory": list_widget_instances(widget_id),
        "anomalies": anomalies,
        "intents": [
            {
                "intentId": row["intent_id"],
                "itemId": row["item_id"],
                "actionClass": row["action_class"],
                "status": row["status"],
                "sourceRevision": row["source_revision"],
                "result": row["result"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in intent_rows
        ],
        "actionAudit": get_action_audit(widget_id),
        "questions": list_questions(widget_id, limit=50),
        "pollIntervalSeconds": _publication_capabilities().get("pollIntervalSeconds"),
        "capabilities": publication_capabilities(),
    }
    # Keep tool responses useful as the database grows. Preserve newest records
    # and report the omitted counts so callers can request a larger window.
    caps = {
        "revisions": limit,
        "delivery": limit,
        "inventory": limit,
        "actionAudit": limit,
        "questions": limit,
        "updateRequests": limit,
        "intents": limit,
    }
    totals = {
        "revisions": len(result["revisions"]),
        "delivery": len(result["delivery"]),
        "inventory": len(result["inventory"]),
        "actionAudit": len(result["actionAudit"]),
        "questions": len(result["questions"]),
        "updateRequests": len(result["updateRequests"]),
        "intents": len(result["intents"]),
        "wakeDevices": len(result["wake"]["devices"]),
    }
    truncated = {
        key: totals[key] > caps[key]
        for key in caps
    }
    truncated["wakeDevices"] = totals["wakeDevices"] > limit
    for key in caps:
        result[key] = result[key][:caps[key]]
    receipt_limit = 5 if summary else 20
    for device in result["delivery"]:
        receipts_for_device = device.get("receipts", [])
        if len(receipts_for_device) > receipt_limit:
            device["receiptsTruncated"] = len(receipts_for_device) - receipt_limit
            device["receipts"] = receipts_for_device[-receipt_limit:]
        skipped = device.get("skippedRevisions", [])
        if len(skipped) > limit:
            device["skippedRevisionsTruncated"] = len(skipped) - limit
            device["skippedRevisions"] = skipped[-limit:]
    result["wake"]["devices"] = result["wake"]["devices"][:limit]
    result["resultLimits"] = {
        "limit": limit,
        "summary": summary,
        "totals": totals,
        "truncated": truncated,
    }
    return result


def publication_history(widget_id: str, limit: int = 20) -> list[dict]:
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT revision, published_at, expires_at, kind, title, superseded_at, superseded_reason, payload_json "
            "FROM publication_revisions WHERE widget_id = ? ORDER BY revision DESC LIMIT ?",
            (widget_id, limit),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        result.append({
            "revision": row["revision"], "publishedAt": row["published_at"],
            "expiresAt": row["expires_at"], "kind": row["kind"], "title": row["title"],
            "superseded": bool(row["superseded_at"]), "supersededAt": row["superseded_at"],
            "supersededReason": row["superseded_reason"],
            "summary": payload.get("summary") if isinstance(payload, dict) else None,
        })
    return result


def publication_for_asset(asset_id: str) -> dict | None:
    """Find the current publication that references an immutable asset."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT payload_json FROM publications").fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            publication = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(publication, dict) and asset_id in _publication_asset_ids(publication):
            publication["expired"] = _publication_expired(publication)
            return publication
    return None


def _last_render_metrics() -> dict | None:
    """The most recent render acknowledgement's surface size, if any."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT width, height FROM publication_acks ORDER BY rendered_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    width = _as_int(row["width"], "rendered width")
    height = _as_int(row["height"], "rendered height")
    if width <= 0 or height <= 0:
        return None
    return {"width": width, "height": height, "aspectRatio": round(width / height, 4)}


def publication_capabilities() -> dict:
    try:
        from .delivery import list_widget_instances
    except ImportError:  # direct import from scripts/tests
        from delivery import list_widget_instances  # type: ignore
    caps = dict(_publication_capabilities())
    last = _last_render_metrics()
    render = dict(caps.get("render") or {})
    render["lastRendered"] = last
    render["recommendedAspectRatio"] = last["aspectRatio"] if last else None
    caps["render"] = render
    inventory = list_widget_instances()
    caps["inventory"] = {
        "sizes": inventory,
        "registeredCount": len(inventory),
        "endpoint": "PUT /v1/device/instances",
    }
    return caps


def list_widgets() -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT widget_id FROM publications "
            "UNION SELECT widget_id FROM widget_settings "
            "UNION SELECT widget_id FROM widget_instances ORDER BY widget_id"
        ).fetchall()
    finally:
        conn.close()
    return [row["widget_id"] for row in rows]


# ---------------------------------------------------------------------------
# Pairing and devices
# ---------------------------------------------------------------------------
