"""Host-local transport + authorization verification over HTTPS (gates 2/4 subset).

Starts the real widget server with a throwaway self-signed certificate and asserts
the full auth matrix. Needs no phone, no Tailscale, and no Hermes installation.

Usage: python3 scripts/verify-auth-local.py <dir-with-cert.pem-and-key.pem>
Exit code 0 on success, 1 with the failed checks printed otherwise.
"""
from __future__ import annotations

import json
import os
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hermes-plugin" / "hermes-widget"))

TMPD = sys.argv[1] if len(sys.argv) > 1 else str(Path(os.environ.get("TMPDIR", "/tmp")) / "hermes-auth")
os.environ.setdefault("HERMES_WIDGET_FORCE_ENV", "supported")  # host distro is irrelevant here
os.environ["HERMES_WIDGET_DIR"] = str(Path(TMPD) / "data")
os.environ["HERMES_HOME"] = str(Path(TMPD) / "home")

import server  # noqa: E402
import store  # noqa: E402

WIDGET = "hermes-brief"

store.put_widget(WIDGET, {
    "version": 2,
    "widgetId": WIDGET,
    "title": "T",
    "root": {"type": "column", "children": [{"type": "text", "value": "hi"}]},
})
agent_token = store.get_agent_token()

httpd = server.make_server("127.0.0.1", 0, certfile=str(Path(TMPD) / "cert.pem"), keyfile=str(Path(TMPD) / "key.pem"))
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
time.sleep(0.5)
TLS_CTX = ssl._create_unverified_context()  # self-signed test certificate
CHECKS: list[tuple[str, bool]] = []


def req(path: str, method: str = "GET", token: str | None = None, ver: str | None = None,
        body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"https://127.0.0.1:{port}{path}", data=data, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if ver:
        r.add_header("X-Hermes-Widget-Version", ver)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, context=TLS_CTX, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except Exception as exc:  # connection refused, TLS failure, ...
        return -1, repr(exc)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok))
    print(f"{name:<26} {'PASS' if ok else 'FAIL'} {detail[:80].replace(chr(10), ' ')}", flush=True)


# --- transport: HTTPS serves, tokenless is refused ------------------------
s, b = req("/v1/health")
check("https_health_200", s == 200 and '"status":"ok"' in b, b)
s, b = req(f"/v1/widgets/{WIDGET}")
check("tokenless_401", s == 401, b)
s, b = req(f"/v1/widgets/{WIDGET}", token=agent_token)
check("agent_widget_200", s == 200, b)
s, b = req(f"/v1/widgets/{WIDGET}", token=agent_token, ver="9.9.9")
check("bad_version_426", s == 426 and "upgrade_required" in b, b)
s, b = req(f"/v1/widgets/{WIDGET}", token="bogus")
check("bogus_bearer_401", s == 401, b)  # no loopback upgrade to agent trust
s, b = req(f"/v1/widgets/{WIDGET}/events", method="POST", body={"event": "refresh"})
check("tokenless_post_401", s == 401, b)

# --- pairing: consumed and expired codes are refused ----------------------
code = store.mint_pairing_code()["code"]
s, b = req("/v1/pair", method="POST", body={"code": code, "deviceLabel": "pixel-live"})
device = json.loads(b) if s == 200 else {}
device_token = device.get("token", "")
check("pair_200", s == 200 and device_token.startswith("dvc_"), f"device={str(device.get('deviceId', ''))[:8]}")
s, b = req("/v1/pair", method="POST", body={"code": code, "deviceLabel": "replay"})
check("consumed_code_400", s == 400 and "invalid_or_expired_code" in b, b)

expired = store.mint_pairing_code(ttl_minutes=1)["code"]
conn = sqlite3.connect(str(store.db_path()))
conn.execute("UPDATE pairing_codes SET expires_at = 1 WHERE code = ?", (expired,))
conn.commit()
conn.close()
s, b = req("/v1/pair", method="POST", body={"code": expired, "deviceLabel": "late"})
check("expired_code_400", s == 400 and "invalid_or_expired_code" in b, b)

# --- device role: read + interactions yes, operator actions no ------------
s, b = req(f"/v1/widgets/{WIDGET}", token=device_token)
check("device_widget_200", s == 200, b)
s, b = req(f"/v1/widgets/{WIDGET}", method="PUT", token=device_token,
           body={"version": 2, "widgetId": WIDGET, "root": {"type": "column", "children": []}})
check("device_put_401", s == 401, b)
s, b = req("/v1/events", token=device_token)
check("device_events_401", s == 401, b)
s, b = req("/v1/pairing-codes", method="POST", token=device_token)
check("device_mint_code_401", s == 401, b)
s, b = req(f"/v1/widgets/{WIDGET}/events", method="POST", token=device_token,
           body={"event": "refresh", "payload": {"tapId": "t1"}})
check("device_post_event_200", s == 200, b)

# --- revocation takes effect ---------------------------------------------
store.revoke_device(device["deviceId"])
s, b = req(f"/v1/widgets/{WIDGET}", token=device_token)
check("revoked_device_401", s == 401, b)
s, b = req(f"/v1/widgets/{WIDGET}/events", method="POST", token=device_token, body={"event": "refresh"})
check("revoked_post_401", s == 401, b)
s, b = req("/v1/widgets", token=agent_token)
check("agent_widgets_200", s == 200, b)

httpd.shutdown()
httpd.server_close()

passed = sum(1 for _, ok in CHECKS if ok)
print(f"\nTransport + authorization over HTTPS: {passed}/{len(CHECKS)} checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
