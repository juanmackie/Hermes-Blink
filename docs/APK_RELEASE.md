# APK signing and release

The Android app is under `android/`. A debug APK is available for local sideloading and
hardware testing. The requested personal release APK is not complete until it is built with the
owner's existing signing identity and verified. Never commit a keystore, signing passwords, or
`android/signing.properties`.

## Current local artifact

The debug APK was rebuilt and tested on 2026-09-25 with JDK 21:

| Field | Value |
| --- | --- |
| Artifact | `android/app/build/outputs/apk/debug/app-debug.apk` |
| Size | 7,093,127 bytes |
| SHA-256 | `27856713ed871754f19cde157bf946780bd441713a5cbeed98135956431aa768` |
| Package | `com.you.hermeswidget` |
| Version | `0.1.0` (`versionCode=1`) |
| SDK range | minSdk 26, targetSdk 35 |
| Signer | Android debug certificate; local testing only |

Rebuild and re-record the checksum after any Android source or Gradle change:

```sh
# Set JAVA_HOME to your JDK 21 installation before running Gradle.
cd android
./gradlew clean testDebugUnitTest lintDebug assembleDebug
cd ..
python - <<'PY'
from pathlib import Path
import hashlib
p = Path("android/app/build/outputs/apk/debug/app-debug.apk")
print(p.stat().st_size)
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
```

The release task intentionally fails when signing values are incomplete rather than leaving an
unsigned artifact that could be mistaken for the personal release.

## Build the personal release

Provide all four values through Gradle properties or matching environment variables:

```sh
cd android
./gradlew assembleRelease \
  -Phermes.signing.store.file=<personal-keystore> \
  -Phermes.signing.store.password=<password> \
  -Phermes.signing.key.alias=<alias> \
  -Phermes.signing.key.password=<password>
```

The equivalent environment variables are:

- `HERMES_ANDROID_STORE_FILE`
- `HERMES_ANDROID_STORE_PASSWORD`
- `HERMES_ANDROID_KEY_ALIAS`
- `HERMES_ANDROID_KEY_PASSWORD`

Do not put secrets in `gradle.properties`, shell history, CI logs, documentation, or the
repository. Do not generate a new identity for an update: Android upgrades must retain the
existing signer certificate.

Verify the resulting release artifact:

```sh
apksigner verify --verbose --print-certs \
  app/build/outputs/apk/release/app-release.apk
aapt2 dump badging \
  app/build/outputs/apk/release/app-release.apk
```

The release is complete only when the signature, package/version, checksum, and signer identity
are recorded. The phone can use the debug APK for testing before that point, but a debug-signed
APK is not the requested personal release artifact.

## Release checklist

- [ ] Existing personal keystore is available outside the repository.
- [ ] `assembleRelease` succeeds without warnings that affect packaging.
- [ ] `apksigner verify` passes and the signer matches the existing identity.
- [ ] Package name and version are recorded.
- [ ] SHA-256 is recorded beside the artifact.
- [ ] The APK is installed or upgraded on the target phone.
- [ ] Pairing and publication delivery are tested over Tailscale.
- [ ] The signed artifact is kept private; no public distribution infrastructure is implied.
