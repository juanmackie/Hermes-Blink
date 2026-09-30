# ruff: noqa: F401,F403 -- this module intentionally re-exports the historic store surface.
"""SQLite-backed widget store shared by the agent tools and the plugin HTTP server.

Everything the widget owns lives in one SQLite file under the Hermes home so the
agent process (tools) and the serving process (hermes widget serve) see the same
state with no extra service. Device tokens are only ever persisted as sha256
hashes.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import string
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # normal plugin package imports
    from .db import (
        _LOCK, _connect, _hermes_home, agent_token_path, asset_path, backup_db, db, restore_db,  # noqa: F401 - compatibility export
        assets_dir, data_dir, db_path,
    )
except ImportError:  # direct import from scripts/tests
    from db import (
        _LOCK, _connect, _hermes_home, agent_token_path, asset_path, backup_db, db, restore_db,  # noqa: F401 - compatibility export
        assets_dir, data_dir, db_path,
    )  # type: ignore

try:  # normal plugin package imports
    from .errors import (
        ActionIntentError, AssetNotFound, AssetUnavailable, PairingError,
        PublicationError, PublicationTooLarge, RateLimitError, RenderNotReady, StoreError,
    )
    from .schema import SCHEMA_VERSION, ensure_schema as _ensure_schema  # noqa: F401 - compatibility exports
    from .timeutil import _as_int, _epoch_now, _hash_token, _in_quiet_window, _iso_from_epoch, _now, _validate_timezone
except ImportError:  # direct import from scripts/tests
    from errors import (
        ActionIntentError, AssetNotFound, AssetUnavailable, PairingError,
        PublicationError, PublicationTooLarge, RateLimitError, RenderNotReady, StoreError,
    )  # type: ignore
    from schema import SCHEMA_VERSION, ensure_schema as _ensure_schema  # type: ignore  # noqa: F401 - compatibility exports
    from timeutil import _as_int, _epoch_now, _hash_token, _in_quiet_window, _iso_from_epoch, _now, _validate_timezone  # type: ignore

try:  # normal path: imported as part of the hermes-widget plugin package
    from . import push as _push
    from .publication import (
        ACTION_CLASSES,
        ACTION_KINDS,
        MAX_ACTION_PAYLOAD_BYTES,
        PRIORITY_HIGH_MAX_PER_DAY,
        PRIORITY_HIGH_MAX_PER_HOUR,
        SENSITIVE_ACTION_CLASSES,
        PublicationInputError,
        PreparedRegion,
        prepare_region,
        prepare_publication,
    )
    from .publication import PublicationTooLarge as _PublicationInputTooLarge
    from .publication import capabilities as _publication_capabilities
    from .bands import BAND_BODY_LINES as _BAND_BODY_LINES
    from .bands import chars_per_line as _chars_per_line
    from .bands import size_band as _size_band
except ImportError:  # pragma: no cover - direct import from tests/scripts
    import push as _push  # type: ignore
    from publication import (  # type: ignore
        ACTION_CLASSES,
        ACTION_KINDS,
        MAX_ACTION_PAYLOAD_BYTES,
        PRIORITY_HIGH_MAX_PER_DAY,
        PRIORITY_HIGH_MAX_PER_HOUR,
        SENSITIVE_ACTION_CLASSES,
        PublicationInputError,
        PreparedRegion,
        prepare_region,
        prepare_publication,
    )
    from publication import (  # type: ignore
        PublicationTooLarge as _PublicationInputTooLarge,
    )
    from publication import capabilities as _publication_capabilities  # type: ignore
    from bands import BAND_BODY_LINES as _BAND_BODY_LINES  # type: ignore
    from bands import chars_per_line as _chars_per_line  # type: ignore
    from bands import size_band as _size_band  # type: ignore

try:
    from .store_utils import _short_str
except ImportError:  # direct import from scripts/tests
    from store_utils import _short_str  # type: ignore

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
def init_db() -> None:
    """Create the schema if needed. Safe to call repeatedly."""
    with _LOCK:
        conn = _connect()
        try:
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------



# Domain implementations are imported here to preserve the original store API.
try:
    from .actions import (
        ACTION_INTENT_TTL_SECONDS, ACTION_MAX_PER_WINDOW, ACTION_RATE_WINDOW_SECONDS,
        _expire_stale_intents, _intent_dict, _json_object, confirm_action_intent,
        get_action_audit, get_intents, post_action_event, resolve_intent,
    )
    from .telemetry import get_events, post_event
except ImportError:  # direct import from scripts/tests
    from actions import (  # type: ignore
        ACTION_INTENT_TTL_SECONDS, ACTION_MAX_PER_WINDOW, ACTION_RATE_WINDOW_SECONDS,
        _expire_stale_intents, _intent_dict, _json_object, confirm_action_intent,
        get_action_audit, get_intents, post_action_event, resolve_intent,
    )
    from telemetry import get_events, post_event  # type: ignore


# Domain implementations are imported here to preserve the original store API.
try:
    from .auth import (
        AGENT_TOKEN_ENV, DEVICE_TOKEN_PREFIX, PAIRING_TTL_MINUTES,
        device_for_token, get_agent_token, get_or_mint_pairing_code,
        get_valid_pairing_code, list_devices, mint_pairing_code,
        pairing_code_count, prune_expired_pairing_codes, register_device,
        revoke_device, update_device_label, verify_agent_token,
    )
    from .push_state import (
        DEFAULT_WIDGET_ID, PUSH_MAX_PER_WINDOW, PUSH_STATES, PUSH_WINDOW_SECONDS,
        check_push_rate, get_device_push_state, list_push_states,
        set_device_push_endpoint, set_device_push_state, wake_test,
    )
except ImportError:  # direct import from scripts/tests
    from auth import (  # type: ignore
        AGENT_TOKEN_ENV, DEVICE_TOKEN_PREFIX, PAIRING_TTL_MINUTES,
        device_for_token, get_agent_token, get_or_mint_pairing_code,
        get_valid_pairing_code, list_devices, mint_pairing_code,
        pairing_code_count, prune_expired_pairing_codes, register_device,
        revoke_device, update_device_label, verify_agent_token,
    )
    from push_state import (  # type: ignore
        DEFAULT_WIDGET_ID, PUSH_MAX_PER_WINDOW, PUSH_STATES, PUSH_WINDOW_SECONDS,
        check_push_rate, get_device_push_state, list_push_states,
        set_device_push_endpoint, set_device_push_state, wake_test,
    )


try:
    from .assets import (
        _publication_asset_ids, _write_immutable_asset, get_asset, read_asset,
    )
except ImportError:  # direct import from scripts/tests
    from assets import (  # type: ignore
        _publication_asset_ids, _write_immutable_asset, get_asset, read_asset,
    )

# Publication implementations are imported here to preserve the original store API.
try:
    from .publications import *  # noqa: F401,F403
    from .publications import (
        _capacity_warnings_for_publication, _dispatch_priority_nudges, _last_render_metrics,
        _legacy_region, _open_question_for_publication, _priority_counts, _priority_effective,
        _publication_expired, _publication_regions_for_device, _publication_semantics,
        _publication_stale, _record_nudge, _region_expired, _region_payload, _region_specs,
    )
except ImportError:  # direct import from scripts/tests
    from publications import *  # type: ignore # noqa: F401,F403
    from publications import (  # type: ignore
        _capacity_warnings_for_publication, _dispatch_priority_nudges, _last_render_metrics,
        _legacy_region, _open_question_for_publication, _priority_counts, _priority_effective,
        _publication_expired, _publication_regions_for_device, _publication_semantics,
        _publication_stale, _record_nudge, _region_expired, _region_payload, _region_specs,
    )

# Delivery, device inventory, and support-report implementations preserve the store API.
try:
    from .delivery import *  # noqa: F401,F403
    from .delivery import (
        _clean_app_build_code, _clean_app_build_sha, _clean_app_version, _clean_os_sdk,
        _instance_int, _size_class, _update_request_row,
    )
except ImportError:  # direct import from scripts/tests
    from delivery import *  # type: ignore # noqa: F401,F403
    from delivery import (  # type: ignore
        _clean_app_build_code, _clean_app_build_sha, _clean_app_version, _clean_os_sdk,
        _instance_int, _size_class, _update_request_row,
    )
