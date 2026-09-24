# Hermes Widget - Gate evidence checklist (transport v1, layout contract v2)

Each gate must pass before release. Evidence required per gate:

## Current v3 verification (2026-09-24)

- `HERMES_WIDGET_FORCE_ENV=supported python -m unittest discover -s hermes-plugin/hermes-widget/tests -v` — **47/47 passed**.
- `python -m compileall -q hermes-plugin/hermes-widget scripts` and `python scripts/check-contract-parity.py` — **passed**.
- `bash scripts/verify-host-local.sh` — **PASS**, including 17/17 local HTTPS/auth checks, CLI/upgrade/rollback, contract parity, and the Android parser gate.
- Real Hermes `v0.18.2` at `<Hermes install>\venv\Scripts\hermes.exe` reports plugin `3.0.0` enabled. Direct registry inspection found `widget_publish`, all eight widget tools, the `pre_llm_call` reminder, the widget command/skill, and exactly one `hermes-widget-refresh` job using `widget_publish`.
- Private Tailscale Serve `https://afs-166.tailc4690.ts.net` returned authenticated health/capabilities and rejected unauthenticated capabilities with `401`.
- A real Windows gateway restart test passed: after stopping the widget server, `hermes gateway restart` loaded the startup hook, started the server outside the gateway process tree, and returned healthy; a repeated hook invocation preserved the server PID.
- `cd android && JAVA_HOME='JDK21' PATH="$JAVA_HOME/bin:$PATH" ./gradlew testDebugUnitTest lintDebug assembleDebug --rerun-tasks` — **passed**. The debug APK verifies with APK Signature Scheme v2; `assembleRelease` intentionally refuses to create an unsigned artifact without all personal signing values.
- No Android device is attached (`adb devices` is empty; wireless ADB to `pixel-10-pro-xl` at `100.82.165.37:5555` was refused). TrueNAS `truenas-admin` is reachable over Tailscale, but SSH public-key authentication was denied; no local Docker/Podman runtime is available. These are external acceptance blockers, not inferred passes.

## Historical v2.1 snapshot (superseded by the current evidence above)

**Snapshot: 2026-09-17, plugin v2.1.0.** The counts below are a point-in-time record, not a
standing claim — the commands in the right-hand column are the reproducible part. Re-run them
after any change; if a count moved and this table did not, trust the command.

These are the parts of gates 1/2/4/6/7 that do **not** need a phone, Tailscale, or a reboot. Re-run with
`bash scripts/verify-host-local.sh` (includes `scripts/verify-auth-local.sh`).

| Check | Result | How it was verified (reproducible) |
| --- | --- | --- |
| Transport + authorization over HTTPS | PASS | `bash scripts/verify-auth-local.sh` — 17/17: health 200; tokenless GET/POST 401; agent GET 200; bad version 426; bogus bearer 401; pairing 200; consumed + expired code 400; device GET 200 but PUT/events-feed/mint-code 401; device interaction POST 200; revoked device 401 |
| CLI / tools / diagnostics | PASS | `scripts/verify-cli-local.py` — `doctor` exits 0 and reports `transport_version=v1`, `layout_contract=v2`, server 1.0.0; agent + device tokens redacted from output; `status/code/devices/pair/publish` all exit 0; 7 agent tools present; `hermes widget preview` wired into the dispatcher; exactly one skill doc |
| Idempotent `up` | PASS | `bash scripts/verify-host-local.sh` (gate 1) — second `up --json` returns `idempotent: true` with identical `{service,cron,devices,credentials,pairing_codes}` counts |
| Upgrade/rollback keeps pairing | PASS | `scripts/verify-cli-local.py` — device id + token stayed valid across `upgrade` and `rollback`; `upgrade` writes `widget.db.bak.*` and `rollback` restores the newest of them |
| Layout validation (unit) | PASS | `verify-host-local.sh` — 2 valid fixtures accepted, 5 invalid rejected (chart/image/toggle/url-action/calendar-month); 37 Python + 28 Kotlin tests pass |
| Layout rendering (style/typography) | PASS (offline) | `TypoTest`/`TimeFormatTest` + `hermes widget preview`; on-device pixels not verified — see Gate 6 |
| Contract parity | PASS | `python scripts/check-contract-parity.py` — schema, validator, Android parser, Typo tokens, skill and docs agree |
| APK built + checksummed | PASS | `app-debug.apk` sha256 `387c2dc1…9e3aa` (6,951,623 bytes after the v2.1.1 dependency cut), debug-signed, `sha256sum -c checksums.txt` OK (docs/APK_RELEASE.md) |

Still user-run (real hardware / network / time): **gates 2, 3, 4 and 8**. Their scripts print the exact
steps; the agent cannot produce this evidence.

## Gate 1 - Clean setup
- Fresh supported host detected by capability checks (not assumed Ubuntu/WSL) with working Hermes
- One agent instruction reaches pairing
- No source edits, no user-run server commands
- Evidence: `hermes widget up --json` output showing state=awaiting_pairing

## Gate 2 - Phone onboarding
**Status: NOT RUN — needs a real phone (user-run).**
- Signed APK install → QR pairing → pin → real initial brief
- Expired-code flow fails gracefully
- Same-phone pairing flow works
- Evidence: paired device shows real brief, not sample content

## Gate 3 - Persistence
**Status: PARTIAL — the local Windows gateway restart path passed; the real TrueNAS container/reboot path is still user-run.**
- Closing terminals, restarting services, rebooting Linux, logging back into Windows restores operation
- No duplicate jobs, services, devices, credentials
- Local evidence: after stopping the server and restarting the actual gateway, the hook restored `/v1/health`; a second hook call did not create another server. TrueNAS/container recreation remains unverified.

## Gate 4 - Security
**Status: PARTIAL — the private Tailscale Serve URL passed authenticated health/capability checks and unauthenticated rejection; phone/TrueNAS end-to-end security remains user-run.**
- Tokenless requests fail through HTTPS proxy (verified: loopback requires bearer)
- Device credentials cannot perform operator actions
- Consumed/expired codes fail
- Revoked devices lose access
- Secrets stay out of logs and ordinary preferences (verified: SecureStore only)
- Evidence: 401 on tokenless loopback, device token on operator route rejected, consumed code rejected, revoked device 401

## Gate 5 - Interaction
- Refresh, dismiss, review survive duplicate requests, network interruption, process restart, slow model responses
- Evidence: duplicate event deduped, refresh completes after newer valid layout stored

## Gate 6 - Rendering
**Status: PARTIAL — publication rendering, fit/zoom behavior, and Android checks pass offline; on-device pixels are NOT RUN (needs a phone).**
- Every retained component renders correctly across widget sizes, large text, supported Android versions
- Removed types cannot be authored as supported content
- Evidence (reproducible, no phone):
  - `cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug` — 33 JVM tests plus lint and debug assembly pass, including `TypoTest` (the four
    `style` steps map to four distinct `TextStyle`s, colour overrides win, 8-digit hex is
    ignored, alignment/weights map) and `TimeFormatTest` (ISO datetimes render as `HH:mm`).
  - `hermes widget preview fixtures/brief-v2.json` — renders every widget shape to HTML with
    the widget bounds outlined; inspected for `title` 18sp/bold, `caption` 11sp/secondary,
    delta up/down colours, badge ink flip, accent progress fill, and clipping at each shape.
  - `python scripts/check-contract-parity.py` — every field the schema declares is applied by
    the renderer and documented in the skill (this is what caught the v2 no-op fields).
  - Publication tests cover text/SVG/PNG/JPEG/WebP, malformed media, unsafe SVG, size limits, ETag/304, asset integrity, revoked credentials, and render acknowledgements.
- **Not verified:** actual pixels on a real device, large-text/accessibility settings, and
  Android-version differences. No device was attached during this change, so the visual result
  on hardware is an open item for gate 2.
- Evidence (validation): layout validation rejects chart/image/icon/toggle/calendar-month

## Gate 7 - Recovery
**Status: rollback/invalid-layout sub-checks PASS locally (see table above); the offline-phone stale-content check is user-run.**
- Offline phones retain visibly stale content
- Invalid layouts cannot poison the cache
- Upgrade, backup restoration, rollback preserve pairing
- (There is no v1→v2 migration to test: v2 is the only accepted contract.)
- Evidence: stale content shown, invalid layout rejected, pairing preserved after rollback

## Gate 8 - Real-device soak
**Status: NOT RUN — needs 72 h on a real device (user-run).**
- 72-hour run exercises scheduled briefs, Android battery restrictions, network changes, host restarts, repeated taps without silent stalls
- Evidence: 72-hour log showing no stalls, scheduled briefs generated, taps processed
