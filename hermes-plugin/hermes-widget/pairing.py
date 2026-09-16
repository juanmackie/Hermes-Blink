#!/usr/bin/env python3
"""Hermes Widget pairing automation.

Generates single-use pairing codes with QR fallback and same-phone pairing link.
Used by the bootstrap installer and the agent pairing flow.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

try:
    from hermes_widget import store
except ImportError:  # pragma: no cover
    import store  # type: ignore

PAIRING_TTL_MINUTES = 10


def mint_pairing_code() -> dict[str, Any]:
    """Mint a single-use pairing code (existing store function)."""
    return store.mint_pairing_code(ttl_minutes=PAIRING_TTL_MINUTES)


def generate_qr_payload(server_url: str, code: str) -> str:
    """Generate the QR payload containing server URL and pairing code."""
    payload = {"url": server_url, "code": code, "ttl": PAIRING_TTL_MINUTES * 60}
    return json.dumps(payload, separators=(",", ":"))


def register_device(code: str, label: str = "unknown") -> dict[str, Any] | None:
    """Redeem a pairing code. Returns device info or None if invalid/expired."""
    return store.register_device(code, label)


def complete_onboarding(
    base_url: str, device_token: str, device_id: str, widget_id: str = "hermes-brief"
) -> dict[str, Any]:
    """Complete onboarding with an authenticated fetch.

    Verifies the device token works by fetching the widget layout.
    Returns the layout or raises on failure.
    """
    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/v1/widgets/{widget_id}",
            headers={"Authorization": f"Bearer {device_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            layout = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "layout": layout, "deviceId": device_id}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"auth_failed: {exc.code}", "deviceId": device_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "deviceId": device_id}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hermes Widget pairing automation.")
    parser.add_argument("--server-url", default="http://127.0.0.1:8788")
    parser.add_argument("--code", default=None, help="Use existing pairing code")
    parser.add_argument("--label", default="unknown", help="Device label")
    parser.add_argument("--onboard", action="store_true", help="Complete onboarding with auth fetch")
    args = parser.parse_args()

    if args.code:
        pairing = {"code": args.code}
    else:
        pairing = mint_pairing_code()

    print(f"Pairing code: {pairing['code']}")
    print(f"Expires:      {pairing['expiresAt']}")
    print(f"QR payload:   {generate_qr_payload(args.server_url, pairing['code'])}")
    print(f"Same-phone:   {args.server_url}/v1/pair?code={pairing['code']}")

    if args.onboard:
        result = complete_onboarding(args.server_url, "", args.label)
        print(f"Onboarding:   {result}")
