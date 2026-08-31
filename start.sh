#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export MBD_ROOT="$(pwd)"
export MBD_LAUNCHER=1

pick_app() {
  if [ -f "$MBD_ROOT/program/run.py" ]; then
    printf '%s\n' "$MBD_ROOT/program"
  else
    printf '%s\n' "$MBD_ROOT"
  fi
}

hide_other_os() {
  rm -f start.bat Start.bat Start.command allow-firewall.bat allow-firewall.sh
  if [ -f start.sh ] && [ -f Start.sh ]; then
    rm -f Start.sh
  fi
  if [ -f program/run.py ]; then
    if [ "$(uname -s)" = Darwin ]; then
      chflags hidden program 2>/dev/null || true
    else
      printf 'program\n' > .hidden
    fi
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ then run this again."
  exit 1
fi

hide_other_os
APP_DIR="$(pick_app)"
cd "$APP_DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "Checking for updates…"
.venv/bin/python update.py --auto || true

while true; do
  cd "$MBD_ROOT"
  APP_DIR="$(pick_app)"
  hide_other_os
  cd "$APP_DIR"
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install -q -r requirements.txt

  echo
  echo "Matched Betting Documenter is starting."
  echo "Leave this window open. Press Ctrl+C to stop."
  echo
  set +e
  .venv/bin/python run.py
  code=$?
  set -e
  if [ "$code" -eq 42 ]; then
    continue
  fi
  exit "$code"
done
