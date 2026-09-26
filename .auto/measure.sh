#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 .auto/audit_widget_quality.py
