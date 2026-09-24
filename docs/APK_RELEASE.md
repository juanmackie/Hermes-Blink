# APK signing and coordinated release pipeline

This gap requires a build environment with Android SDK, Gradle wrapper, and protected signing credentials.

## Built artifact (verified locally — 2026-09-17, after the dependency cut)

The debug APK is built and verified on this host. It is debug-signed (`CN=Android Debug`), which is
what Gate 2 installs; a **release** APK still needs the user's keystore (see below).

| Field | Value |
| --- | --- |
| Artifact | `android/app/build/outputs/apk/debug/app-debug.apk` |
| Size | 7,092,491 bytes |
| SHA-256 | `464ca2b1d9e5b84a2b34a41a9913aa4d26e4434ea1b8dd7b6124cfbd412f0865` |
| Package / version | `com.you.hermeswidget` versionName `0.1.0`, versionCode `1` |
| SDK range | minSdk 26 (Android 8.0), targetSdk 35 |
| Label | Hermes Widget |
| Signer | `C=US, O=Android, CN=Android Debug` (SHA-256 `2f042e9d…3ce69a`) |

Current artifact supersedes every earlier recorded value: 10,681,585 / `229df3cf…` (pre-renderer)
and 10,730,520 / `af5145c5…` (renderer, before the dependency cut). The 35% drop is the four
unused dependencies removed in v2.1.1 — see RELEASE.md. Rebuild and re-record after any change
under `android/app/src/` or `android/app/build.gradle.kts`.

Reproduce / verify:

```bash
export JAVA_HOME="${JAVA_HOME:-JDK21}"   # Gradle 8.10.2 + AGP need JDK 17+
cd android && ./gradlew clean testDebugUnitTest assembleDebug
cd .. && bash scripts/generate-checksums.sh && sha256sum -c checksums.txt
```

Evidence collected: `apksigner verify --print-certs` (debug cert), `aapt2 dump badging`
(package/version/SDK/label above), Gradle `testDebugUnitTest`, `lintDebug`, and `assembleDebug`
passing, plus `sha256sum` above. The release task intentionally fails without complete personal
signing credentials rather than leaving an unsigned APK that could be mistaken for a release.
`checksums.txt` is generated at the repo root and is gitignored (release artifact, not source).

## Release signing — user action required

No personal release keystore is present in this checkout, and none may be committed:
`*.keystore`/`*.jks` and `android/signing.properties` are ignored. The Android build accepts
the existing personal identity through Gradle properties or matching environment variables;
it refuses a partial set rather than silently producing an unsigned or mis-signed release.

```bash
cd android
./gradlew assembleRelease \\
  -Phermes.signing.store.file=<personal-keystore> \\
  -Phermes.signing.store.password=<password> \\
  -Phermes.signing.key.alias=<alias> \\
  -Phermes.signing.key.password=<password>
```

For a secret manager, use `HERMES_ANDROID_STORE_FILE`, `HERMES_ANDROID_STORE_PASSWORD`,
`HERMES_ANDROID_KEY_ALIAS`, and `HERMES_ANDROID_KEY_PASSWORD` instead. Do not put those values
in `gradle.properties`, shell history, CI logs, or the repository. The build config never logs
them.

Status: **release signing NOT RUN in this environment** — only the Android debug keystore was
found. The smallest next action is for the owner to run the command above with their existing
personal identity, then verify the resulting APK:

```bash
apksigner.bat verify --verbose --print-certs \\
  app/build/outputs/apk/release/app-release.apk
aapt2.exe dump badging \\
  app/build/outputs/apk/release/app-release.apk
```

Do not substitute a newly generated identity: Android updates must retain the existing signer
certificate. The debug APK remains useful for local smoke testing, but it is not the requested
personal release artifact.

## Remaining requirements for verification
- Gradle wrapper (`gradlew`) installed and working
- Protected Android signing credentials configured (not in source; excluded from commits)
- Clean checkout CI that builds and verifies APK
- Coordinated APK/plugin releases with checksums and compatibility metadata
- Stable setup/download page for user installation
- Release artifact (signed APK) published with checksum verification

Verification steps:
1. `./gradlew assembleRelease` produces signed APK
2. `jarsigner -verify` confirms APK signature
3. Plugin version matches APK version in compatibility metadata
4. Setup/download page references pinned release artifact with checksum
5. Release notes (`RELEASE.md`) updated with version, changes, and upgrade instructions
6. Upgrade supports health-checked rollback (`hermes widget rollback`) preserving devices and data

Evidence: APK build output, checksum file, CI build log, release page URL, rollback test result.
