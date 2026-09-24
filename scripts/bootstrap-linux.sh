#!/usr/bin/env sh
# Persistent, capability-based Hermes widget installer for Linux/TrueNAS.
set -eu

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-}
if [ -z "$PYTHON" ]; then
    PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
fi
if [ -z "$PYTHON" ]; then
    echo "bootstrap failed: Python 3.11+ is required" >&2
    exit 1
fi

exec "$PYTHON" "$REPO/scripts/bootstrap.py" "$@"
