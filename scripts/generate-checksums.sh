#!/usr/bin/env bash
# Generate SHA256 checksums for release APKs
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../android/app/build/outputs/apk" 2>/dev/null && pwd || echo android/app/build/outputs/apk)"
OUT="$(cd "$(dirname "$0")/.." && pwd)/checksums.txt"
echo "# SHA256 checksums — $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT"
find "$DIR" -type f -name "*.apk" -printf '%P\n' 2>/dev/null | while read -r rel; do
  # repo-relative path so the checksum is verifiable on any host
  printf '%s  %s\n' "$(sha256sum "$DIR/$rel" | cut -d' ' -f1)" "android/app/build/outputs/apk/$rel"
done >> "$OUT" 2>/dev/null || echo "# no APKs built yet; run ./gradlew assembleRelease" >> "$OUT"
cat "$OUT"
