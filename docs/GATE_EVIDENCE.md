# Hermes Widget — gate evidence

This is the current evidence record for the personal visual publication channel. A gate is not
complete merely because its code and unit tests exist; the target host and real phone evidence
must be recorded separately.

## Current verification — 2026-09-25

### Passing local checks

- `python -m unittest discover -s hermes-plugin/hermes-widget/tests -v` — **48/48 passed**.
- `python -m compileall -q hermes-plugin/hermes-widget scripts` — **passed**.
- `python scripts/check-contract-parity.py` — **passed**.
- Android with JDK 21:

  ```sh
  cd android
  # Set JAVA_HOME to your JDK 21 installation before running Gradle.
  ./gradlew testDebugUnitTest lintDebug assembleDebug --rerun-tasks
  ```

  **passed**: Kotlin unit tests, lint, and debug APK assembly.
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
