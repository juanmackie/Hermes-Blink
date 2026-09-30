"""Small clock, conversion, and hashing helpers shared by store domains."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from .errors import StoreError
except ImportError:  # direct import from scripts/tests
    from errors import StoreError  # type: ignore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_now() -> int:
    try:
        return int(time.time())
    except (OSError, OverflowError, ValueError) as exc:
        raise StoreError("system clock is unavailable") from exc


def _as_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise StoreError(f"{label} is invalid") from exc


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_timezone(value: Any) -> str:
    """Return a valid IANA timezone name, defaulting legacy settings to UTC."""
    if value is None:
        return "UTC"
    if not isinstance(value, str) or not value or len(value) > 128:
        raise StoreError("timezone must be a valid IANA timezone name")
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise StoreError("timezone must be a valid IANA timezone name") from exc
    return value


def _in_quiet_window(
    start: Any, end: Any, timezone_name: Any, current: datetime
) -> bool:
    """Check an HH:MM window in its named timezone, including overnight windows."""
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    try:
        start_time = datetime.strptime(start, "%H:%M").time()
        end_time = datetime.strptime(end, "%H:%M").time()
        zone = ZoneInfo(_validate_timezone(timezone_name))
    except (TypeError, ValueError, StoreError, ZoneInfoNotFoundError):
        return False
    if start_time == end_time:
        return False
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_time = current.astimezone(zone).timetz().replace(tzinfo=None)
    if start_time < end_time:
        return start_time <= local_time < end_time
    return local_time >= start_time or local_time < end_time
