#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ then run this again."
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

echo
echo "Matched Betting Documenter is starting."
echo "This computer:  http://127.0.0.1:5050"
echo "Other devices on Wi‑Fi can use this PC’s IP on port 5050 (see Devices)."
echo "Leave this window open. Press Ctrl+C to stop."
echo
exec .venv/bin/python run.py
