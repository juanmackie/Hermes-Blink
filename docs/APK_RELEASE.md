# APK signing and release

The Android app is under `android/`. A debug APK is available for local sideloading and
hardware testing. The requested personal release APK is not complete until it is built with the
owner's existing signing identity and verified. Never commit a keystore, signing passwords, or
`android/signing.properties`.

## Current local artifact

The debug APK was rebuilt and tested on 2026-09-25 with JDK 17 (the project also supports JDK 21):

| Field | Value |
| --- | --- |
| Artifact | `android/app/build/outputs/apk/debug/app-debug.apk` |
| Size | 7,176,596 bytes |
| SHA-256 | `d1180d76133f7ee8cc22d3b1fc70db557bea10a919e362d6cf3f584319386496` |
| Package | `com.you.hermeswidget` |
| Version | `0.2.0` (`versionCode=2`) |
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

## Build without Android Studio

A headless host only needs Temurin JDK 21 and the Android command-line tools:

```sh
SDK="$HOME/android-sdk"
mkdir -p "$SDK/cmdline-tools"
curl -fsSL -o /tmp/cmdline-tools.zip \
  https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
# `unzip` may be missing on minimal hosts; Python's zipfile works.
SDK="$SDK" python - <<'PY'
import glob, os, stat, zipfile
sdk = os.environ["SDK"]
with zipfile.ZipFile("/tmp/cmdline-tools.zip") as archive:
    archive.extractall(f"{sdk}/cmdline-tools")
# zipfile drops exec bits; without this sdkmanager silently fails to run.
for path in glob.glob(f"{sdk}/cmdline-tools/**/bin/*", recursive=True):
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
PY
mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest" 2>/dev/null || true
chmod +x "$SDK/cmdline-tools/latest/bin/"*
yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" \
  "platform-tools" "platforms;android-35" "build-tools;35.0.0"
export ANDROID_SDK_ROOT="$SDK" ANDROID_HOME="$SDK"
cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug
```

On a constrained container (for example `pids.max=256`), Gradle's default parallelism can
exhaust the process limit and `:app:testDebugUnitTest` dies with `OutOfMemoryError: unable to
create native thread` while `assembleDebug` succeeds. Serialise and cap the heap:

```sh
./gradlew testDebugUnitTest lintDebug assembleDebug \
  --max-workers=1 -Dorg.gradle.jvmargs=-Xmx1536m
```

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
