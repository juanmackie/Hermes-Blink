#!/usr/bin/env sh
# Backward-compatible entry point. Prefer scripts/bootstrap-linux.sh.
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$REPO/scripts/bootstrap-linux.sh" "$@"
