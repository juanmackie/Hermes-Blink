"""Content-free UnifiedPush wakeups.

The widget server never sends publication text to a push distributor.  A wake is
just a small ``fetch`` message sent to a device-registered UnifiedPush endpoint;
the phone then performs the normal authenticated pull over its private HTTPS
connection.  The endpoint is treated as a secret and is never returned by the
status API.
"""
from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)

MAX_ENDPOINT_BYTES = 2048
MAX_RESPONSE_BYTES = 4096
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 8


class PushError(RuntimeError):
    """A wake could not be delivered; the publication remains valid."""


def validate_endpoint(endpoint: Any) -> str:
    """Validate a UnifiedPush endpoint without retaining or logging it."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("push endpoint must be a non-empty string")
    value = endpoint.strip()
    if len(value.encode("utf-8")) > MAX_ENDPOINT_BYTES:
        raise ValueError("push endpoint is too long")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("push endpoint must be an HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("push endpoint must not contain credentials or a fragment")
    return value


def wake(endpoint: str, *, opener: Any | None = None) -> None:
    """POST the content-free wake message to one UnifiedPush endpoint.

    ``opener`` is injectable for tests and for a host that wants a proxy-aware
    opener.  No title, summary, widget id, revision, or publication content is
    included in the request body or headers.
    """
    endpoint = validate_endpoint(endpoint)
    body = json.dumps({"message": "fetch"}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Hermes-Widget/1",
        },
    )
    try:
        if opener is not None:
            response = opener(request, timeout=READ_TIMEOUT_SECONDS)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            try:
                response.read(MAX_RESPONSE_BYTES)
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        else:
            with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
                status = response.status
                response.read(MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise PushError("push endpoint was unreachable") from exc
    if status < 200 or status >= 300:
        raise PushError(f"push endpoint returned HTTP {status}")


__all__ = ["PushError", "validate_endpoint", "wake"]
