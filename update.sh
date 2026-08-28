#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 update.py "$@"
chmod +x start.sh pack.sh update.sh 2>/dev/null || true
