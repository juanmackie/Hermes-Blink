# APK signing and coordinated release pipeline

This gap requires a build environment with Android SDK, Gradle wrapper, and protected signing credentials.

Requirements for verification:
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
