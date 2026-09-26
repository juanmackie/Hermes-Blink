# Hermes Widget — gate evidence

This is the current evidence record for the personal visual publication channel. A gate is not
complete merely because its code and unit tests exist; the target host and real phone evidence
must be recorded separately.

## Current verification — 2026-09-25

### Passing local checks

- `python -m unittest discover -s hermes-plugin/hermes-widget/tests -v` — **69/69 passed**
  on CPython 3.11.15 (Windows); the same suite is **69/69 passed** on CPython 3.13.14.
- `python -m compileall -q hermes-plugin/hermes-widget scripts` — **passed** on 3.11 and 3.13.
- `python scripts/check-contract-parity.py` — **passed**.
- `python scripts/verify-cli-local.py` — **0 failures** (CLI verbs, redaction,
  upgrade/rollback, contract parity).
- Android with JDK 21 (verified 2026-09-25 after the v3.0.2 source changes):

  ```sh
  cd android
  # Set JAVA_HOME to your JDK 21 installation before running Gradle.
  ./gradlew testDebugUnitTest lintDebug assembleDebug \
    --max-workers=1 -Dorg.gradle.jvmargs=-Xmx1536m
  ```

  **passed**: Kotlin unit tests (including `PairingLinkTest`), lint, and debug APK
  assembly. Rebuilt debug APK: 7,559,283 bytes, sha256
  `7ceac6b95fd6117553b42ded384f87b6b732dbc7adbd47eafe26911035d60527`.
- The current checkout contains a debug-signed APK at
  `android/app/build/outputs/apk/debug/app-debug.apk`.
- The Android package is installed on the attached test device as `com.you.hermeswidget`
  version `0.1.0`. This proves installation, not pairing or publication delivery.

### Host state at the time of this record

`hermes widget status` reported:

```text
State:        degraded
Server:       not listening on 127.0.0.1:8788
```

The plugin database, token, routine, and device records exist, but the live server must be
started or restored before the phone path can be accepted. Rerun the capability-based bootstrap
and verify `/v1/health` before pairing.

### Binding, restart, and pairing fixes — verified 2026-09-25

- 13 new regression tests in `hermes-plugin/hermes-widget/tests/test_binding.py` cover:
  omitted-flag preservation, independent partial overrides, first-install default,
  malformed `server.json` rejection without rewrite, saved executable/home preservation,
  agent-tool reruns, restart-required reporting, `status` configured-vs-probe output,
  `pair` HTTPS validation, and manual-only pairing (no QR/link payloads).
- The launcher test now patches `gateway_hook._is_windows` and asserts both the Windows
  launcher and POSIX `start_new_session` branches, without mutating Python's process-wide
  `os.name`.
- `.github/workflows/ci.yml` now runs the suite on Ubuntu 24.04 with Python 3.11 and 3.13,
  on `windows-latest` with Python 3.11, plus the secret scan and Android unit tests. The Android
  job no longer uses the deprecated `android-actions/setup-android@v3` (the cause of the three
  red runs reported on 2026-09-25); it installs `cmdline-tools` directly and caps Gradle
  workers/heap. CI was configured in this change but **not executed in this environment**.
- The container/TrueNAS binding and host-side port publish described in the guides were
  **not executed here**; `scripts/gate-*.sh` and the real container remain the evidence path.

### Delivery truthfulness and device identity — verified 2026-09-25

- 8 new regression tests in `hermes-plugin/hermes-widget/tests/test_delivery.py` cover:
  superseded-revision history and per-device `skippedRevisions`, `lastFetchedRevision`,
  `maxAgeSeconds` stale drop (`410 publication_stale`), `capabilities` SVG allowlist and
  render surface, the layout→publication pointer, device label round-trip plus `PATCH /v1/device`
  authorization, the pairing one-liner, and the layout TTL ceiling.
- The Kotlin `PairingLinkTest` deep-link parse/round-trip test passes in the local Android
  build (`testDebugUnitTest`), which also compiles the device-label, rename, deep-link, and
  tap-event changes.

### Verification caveats

- The old `pytest -q hermes-plugin/hermes-widget/tests` collection command is not supported in
  this checkout because the hyphenated plugin directory is not a pytest import root. Use the
  unittest discovery command above.
- A release APK has not been personally signed in this environment. The existing
  `app-release-unsigned.apk` must not be presented as the requested release artifact.
- The current phone is attached over USB, but no claim is made here that the complete
  pairing → publication → authenticated asset fetch → Android render → acknowledgement flow
  has passed on it.
- TrueNAS container recreation and a 72-hour phone/network soak remain pending.

### v3.2 local verification — 2026-09-25

- The host suite now includes seven feature-proposal regression tests covering content-free
  priority wakes and degradation, ordered delivery receipts, Pillow-only raster previews,
  the no-Cairo text path, registered-size PNG previews, cache-safe proactive guidance,
  durable idempotent action intents/audit, and revoked-device rejection: **77/77 passed** on
  CPython 3.11.
- `python scripts/check-contract-parity.py`, `python scripts/verify-cli-local.py`, and
  `pyflakes hermes-plugin/hermes-widget/*.py` passed.
- Android `testDebugUnitTest`, `lintDebug`, and `assembleDebug` passed with JDK 17 and the
  installed Android 35 SDK. The debug artifact is 7,158,980 bytes with SHA-256
  `dbf70243ba9fa90b8a0795b97d0c841bad7b9634529767e700d900d03acbb47f`.
- Real UnifiedPush distributor registration, Doze/exemption behavior, and a phone action
  round-trip remain device/host soak checks; no visibility claim is made from local tests.

## Gate status

| Gate | Status | Evidence or remaining action |
| --- | --- | --- |
| 1. Clean host setup | **PARTIAL** | Local capability/bootstrap tests pass; current live host is degraded. |
| 2. Phone onboarding | **NOT RUN** | Pair the attached phone with a short-lived code and verify the real widget. |
| 3. Persistence | **PARTIAL** | Windows gateway restart was previously tested; TrueNAS/container recreation remains pending. |
| 4. Security | **PARTIAL** | Local auth and prior Tailscale checks passed; phone and target-container end-to-end checks remain pending. |
| 5. Host/server recovery | **PARTIAL** | Local restart tests pass; current host server is not listening. |
| 6. Rendering | **PARTIAL** | Offline renderer/tests pass; real phone pixels, accessibility settings, and Android versions remain unverified. |
| 7. Offline/cache recovery | **PARTIAL** | Automated cache-poisoning and stale-state tests pass; real phone network transitions remain pending. |
| 8. Real-device soak | **NOT RUN** | Complete the 72-hour procedure in `docs/GATE_8_Soak.md`. |
| 9. Signed release | **NOT RUN** | Build and verify with the owner's existing personal signing identity. |

## Required acceptance evidence

The final acceptance record must include:

1. Two successful bootstrap runs on the real Hermes host with one server, routine, hook, and
   device set.
2. Gateway restart and real container recreation with persistent widget state.
3. Tailscale Serve health from the phone's network path.
4. Pairing with a short-lived code, without entering the operator token in the phone.
5. Text, SVG diagram/chart, and raster photograph publication, authenticated asset fetch,
   Android rendering, and render acknowledgement.
6. Resize, accessibility summary, malformed input, oversized input, interrupted download,
   revoked token, expiry, and offline recovery checks.
7. A signed release APK whose signer matches the existing personal identity.

Until those items are recorded, the implementation is usable for local testing but the plan is
not fully accepted for release.
