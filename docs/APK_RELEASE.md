# APK signing and release

The Android app is under `android/`. A debug APK is available for local sideloading and
hardware testing. The requested personal release APK is not complete until it is built with the
owner's existing signing identity and verified. Never commit a keystore, signing passwords, or
`android/signing.properties`.

## Current local artifact

Regenerated from the tree, not carried forward: the numbers below are produced by
`scripts/release-evidence.py` and verified in CI, because a release document that describes
an earlier commit is worse than no document at all — it looks current.

| Field | Value |
| --- | --- |
| Artifact | `android/app/build/outputs/apk/debug/app-debug.apk` |
| Size | 7,269,419 bytes |
| SHA-256 | `b27fd03d060dec5317c3efb0ef72c96f6de97d9fc6816029517fcb18179c4fc4` |
| Package | `com.you.hermeswidget` |
| Version | `0.4.5` (`versionCode=9`) |
| Build commit | `822437668205` (also sent by the app as `X-Hermes-App-Sha`) |
| SDK range | minSdk 26, targetSdk 35 |
| Signer | Android debug certificate; local testing only |

### Release evidence

| Version | Commit | Size | SHA-256 |
| --- | --- | --- | --- |
| `0.4.5` (versionCode 9) | `822437668205` | 7,269,419 bytes | `b27fd03d060dec5317c3efb0ef72c96f6de97d9fc6816029517fcb18179c4fc4` |
| `0.4.4` (versionCode 8) | `01b5006` | 7,269,591 bytes | `1aacd52f103cd623999999999999999999999999999999999999999999999999` |
| `0.3.0` (versionCode 3) | `e1a9cf8` | 7,229,478 bytes | `7663fa98a56f006e23e2219811b99fb46ecde948fa53550e878e44be9f455b79` |
| `0.2.0` (versionCode 2) | `7abc096` | 7,182,371 bytes | `895452e10b6e836982f58a051ecb5ce95c0e424a6a808a2647d2c6b56fccd0d4` |

Three rules this table now enforces, after four different APKs shipped as `versionCode=2`:

1. **Every shipped-app change bumps `versionCode`.** `scripts/check-version-bump.py` fails
   the build when `android/app/src/main/**` changes without one. `app_build_code = 2`
   cannot answer "which build is on this phone?".
2. **The commit travels with the app.** `BuildConfig.COMMIT_SHA` is stamped at build time and
   reported as `X-Hermes-App-Sha`; Diagnostics shows it, `widget_status` stores it, and
   `delivery[].renderedBy.appBuildSha` names the build that drew a revision.
3. **Sizes and digests are provenance, never gates.** A clean debug build is not
   byte-reproducible across toolchains. Observed drift across 0.3.0-0.4.2 was -4, +8, -12 and
   +16 bytes, in both directions, on clean builds of the same source. So
   `scripts/release-evidence.py` checks only what cannot drift silently — **which commit**
   (HEAD or its parent, since recording the numbers is itself a commit) and **which
   versionCode** — and *reports* the size and digest of whatever it just built. A
   comparison there would be a red run every time, and a red run nobody reads is worse than
   no gate. Build with `clean` before recording either number, so the figure means
   something.

```sh
# Regenerate this section (and the row above) from a clean build:
cd android && ./gradlew clean assembleDebug && cd ..
python3 scripts/release-evidence.py --apk android/app/build/outputs/apk/debug/app-debug.apk
# What CI runs:
python3 scripts/check-version-bump.py
python3 scripts/release-evidence.py --check docs/APK_RELEASE.md
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
