"""Host-local CLI/diagnostics and upgrade/rollback verification.

Covers the checklist items that need no phone, Tailscale, or Hermes install:
  * every CLI verb returns success and `doctor` reports protocol metadata
  * diagnostics redact the agent and device tokens
  * `upgrade` and `rollback` keep paired devices and their tokens valid

Usage: python3 scripts/verify-cli-local.py <scratch-dir>
Exit code 0 on success; raises AssertionError with details otherwise.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir())
try:
    SCRATCH = Path(tempfile.mkdtemp(prefix="hermes-cli-local-", dir=SCRATCH_ROOT))
except OSError as exc:
    raise SystemExit(f"cannot create verification scratch directory: {exc}") from exc

sys.path.insert(0, str(REPO / "hermes-plugin" / "hermes-widget"))
os.environ["HERMES_WIDGET_FORCE_ENV"] = "supported"
os.environ["HERMES_WIDGET_DIR"] = str(SCRATCH / "data")
os.environ["HERMES_HOME"] = str(SCRATCH / "home")

cli = importlib.import_module("cli")
store = importlib.import_module("store")

WIDGET = "hermes-brief"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(name)
    print(f"{name:<28} {'PASS' if ok else 'FAIL'} {detail[:90]}", flush=True)


def run(fn, ns) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(ns)
    return rc, buf.getvalue()


# --- diagnostics: protocol metadata + redaction ---------------------------
store.put_widget(WIDGET, {"version": 2, "widgetId": WIDGET, "title": "T",
                          "root": {"type": "column", "children": [{"type": "text", "value": "hi"}]}})
agent_token = store.get_agent_token()
code = store.mint_pairing_code()["code"]
device_token = store.register_device(code, "pixel-cli")["token"]

rc, out = run(cli._doctor, SimpleNamespace(json=True))
try:
    doctor = json.loads(out[out.index("{"):])
except (IndexError, TypeError, ValueError) as exc:
    doctor = {}
    print(f"doctor JSON parse failed: {exc}")
check("doctor_exit_0", rc == 0)
check("doctor_protocol", doctor["protocol"] == {"transport_version": "v1", "layout_contract": "v2",
                                                "server_version": doctor["protocol"]["server_version"]},
      json.dumps(doctor["protocol"]))
check("doctor_redacts_secrets", agent_token not in out and device_token not in out
      and doctor["token-preview"] == "***REDACTED***")

redacted = cli._redact(f"Authorization: Bearer {agent_token} device {device_token} token='{agent_token}'")
check("redactor_scrubs_both", agent_token not in redacted and device_token not in redacted, redacted)

# --- CLI verbs reach their handlers --------------------------------------
verb_cases = [
    ("status", cli._status, SimpleNamespace(json=True, host="127.0.0.1", port=9)),
    ("code", cli._code, SimpleNamespace()),
    ("devices", cli._devices, SimpleNamespace(revoke=None)),
    ("pair", cli._pair, SimpleNamespace(server_url="https://widget.example.ts.net:8788", label="t", json=True)),
    ("publish", cli._publish, SimpleNamespace(widget_id=WIDGET, layout_json=None, layout_file=None, json=True)),
]
for name, fn, ns in verb_cases:
    rc, _ = run(fn, ns)
    check(f"verb_{name}", rc == 0)

rc, _ = run(cli._publish, SimpleNamespace(
    widget_id=WIDGET, publication_file=None, layout_file=None, layout_json=None,
    title="CLI priority", summary="CLI priority summary", text="hello", svg=None,
    file_path=None, priority="high", max_age_seconds=None, item_id=None, actions=None,
    json=True,
))
check("verb_publish_priority", rc == 0 and store.get_publication(WIDGET).get("priority") == "high")

# --- upgrade/rollback preserve pairing -----------------------------------
device_id = store.device_for_token(device_token)["deviceId"]
devices_before = len(store.list_devices())
rc, _ = run(cli._upgrade, SimpleNamespace())
check("upgrade_exit_0", rc == 0)
check("upgrade_keeps_device", store.device_for_token(device_token) is not None
      and len(store.list_devices()) == devices_before)

# rollback restores the newest widget.db.bak.* — produced by _upgrade just above, so this is a
# real end-to-end check of the recovery guarantee, not a fixture we planted ourselves.
rc, out = run(cli._rollback, SimpleNamespace())
check("rollback_exit_0", rc == 0 and "Rolled back" in out, out.strip().splitlines()[0] if out.strip() else "")
check("rollback_keeps_device", store.device_for_token(device_token) is not None
      and len(store.list_devices()) == devices_before)

# --- agent tools exist; exactly one skill document ------------------------
tools_src = (REPO / "hermes-plugin" / "hermes-widget" / "tools.py").read_text(encoding="utf-8")
expected_tools = ["widget_update", "widget_validate", "widget_list", "widget_read_events",
                  "widget_mint_pairing_code", "widget_setup", "widget_publish", "widget_preview",
                  "widget_read_intents", "widget_resolve_intent", "widget_wake_test",
                  "widget_set_quiet_hours",
                  "widget_status"]
missing = [t for t in expected_tools if f"def {t}" not in tools_src]
check("agent_tools_present", not missing,
      ("missing: " + ", ".join(missing)) if missing else f"{len(expected_tools)}/{len(expected_tools)}")
skills = list(REPO.glob("hermes-plugin/hermes-widget/skills/*/SKILL.md"))
check("single_skill_doc", len(skills) == 1, ", ".join(str(p.relative_to(REPO)) for p in skills))

# --- offline preview + contract parity -------------------------------------
preview_src = (REPO / "hermes-plugin" / "hermes-widget" / "preview.py").read_text(encoding="utf-8")
check("preview_verb_present", "def preview_file" in preview_src and "def render_html" in preview_src,
      "preview.py exposes render_html/preview_file")
cli_src = (REPO / "hermes-plugin" / "hermes-widget" / "cli.py").read_text(encoding="utf-8")
check("preview_registered", '"preview": _preview' in cli_src and "def _preview" in cli_src,
      "hermes widget preview is wired into the dispatcher")
check("wake_test_registered", '"wake-test": _wake_test' in cli_src and "def _wake_test" in cli_src,
      "hermes widget wake-test is wired into the dispatcher")
parity = subprocess.run([sys.executable, str(REPO / "scripts" / "check-contract-parity.py")],
                        capture_output=True, text=True)
check("contract_parity", parity.returncode == 0,
      parity.stdout.strip().splitlines()[0] if parity.stdout.strip() else parity.stderr.strip()[-120:])

try:
    shutil.rmtree(SCRATCH)
except OSError as exc:
    print(f"verification scratch cleanup failed: {exc}")

print(f"\nCLI/diagnostics/upgrade-rollback: {len(FAILURES)} failure(s)")
sys.exit(1 if FAILURES else 0)
