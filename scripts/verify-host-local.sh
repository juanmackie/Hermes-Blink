#!/usr/bin/env bash
# Host-local launch-gate verification (gates 1,4,5,6,7 — everything needing no phone/Tailscale/reboot)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export REPO
echo "=== Host-local gate verification ==="
echo "Repo: $REPO"
echo

echo "[Gate 1] Clean setup — hermes widget up --json idempotency"
TMPDIR=$(mktemp -d)
HERMES_WIDGET_FORCE_ENV=supported HERMES_WIDGET_DIR="$TMPDIR" python -c "
import sys, json, os, pathlib
repo = os.environ['REPO']
sys.path.insert(0, os.path.join(repo, 'hermes-plugin/hermes-widget'))
import cli, store
class A: json=True; host='127.0.0.1'; port=19880; widget_id='hermes-brief'; schedule='every 6h'
import io, contextlib
buf1 = io.StringIO()
with contextlib.redirect_stdout(buf1):
    cli._up(A())
j1 = json.loads(buf1.getvalue())
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    cli._up(A())
j2 = json.loads(buf2.getvalue())
assert j1['counts'] == j2['counts'], f\"idempotent counts differ: {j1['counts']} vs {j2['counts']}\"
assert j1['state'] in ('awaiting_pairing','needs_user_action','ready','degraded'), j1['state']
print('Gate 1: PASS — up --json idempotent, state', j1['state'])
"
rm -rf "$TMPDIR"
echo

echo "[Gate 5/6/7] Python validator + fixture contract + auth boundaries"
HERMES_WIDGET_FORCE_ENV=supported python -m unittest discover -s "$REPO/hermes-plugin/hermes-widget/tests" -v
echo

echo "[Gate 6] Android parser contract (shared fixtures)"
(cd "$REPO/android" && ./gradlew testDebugUnitTest --rerun-tasks 2>&1 | tail -20)
echo

echo "[Gate 4] Transport + authorization over HTTPS (local subset)"
bash "$REPO/scripts/verify-auth-local.sh"
echo

echo "[Gate 7] CLI verbs, diagnostics redaction, upgrade/rollback pairing retention"
TMPCLI=$(mktemp -d)
python "$REPO/scripts/verify-cli-local.py" "$TMPCLI"
rm -rf "$TMPCLI"
echo

echo "[Gate 7] Secret scan + wrapper check"
if git -C "$REPO" ls-files | grep -E '\.db$|widget\.db|agent_token|\.keystore|\.jks' >/dev/null; then echo "FAIL: tracked secrets"; exit 1; else echo "PASS: no tracked secrets"; fi
test -f "$REPO/android/gradlew" && echo "PASS: Gradle wrapper present" || (echo "FAIL: wrapper missing"; exit 1)
if grep -R "C:/Users" "$REPO/android/gradle.properties" "$REPO/hermes-plugin/hermes-widget/cli.py" 2>/dev/null; then echo "FAIL: machine-specific paths"; exit 1; else echo "PASS: no machine-specific paths"; fi
echo

echo "Host-local gates: PASS. Gates 2,3,4,8 require user device/Tailscale/reboot and are NOT RUN (see scripts/gate-*.sh)."
echo "After the Tailscale proxy is up, gate 4's remaining part is scripts/gate-4-tailscale.sh."
