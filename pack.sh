#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="dist"
ZIP_NAME="MatchedBettingDocumenter.zip"
STAGING="$OUT_DIR/MatchedBettingDocumenter"
PROG="$STAGING/program"

rm -rf "$OUT_DIR"
mkdir -p "$PROG" "$STAGING/data"

# Runtime only — launchers stay at the zip root; code goes in program/.
rsync -a \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  app "$PROG/"

cp run.py update.py requirements.txt "$PROG/"
cp start.bat "$STAGING/start.bat"
cp start.sh "$STAGING/start.sh"
cp share/README.txt "$STAGING/README.txt"

chmod +x "$STAGING/start.sh" "$PROG/update.py" 2>/dev/null || true

(
  cd "$OUT_DIR"
  rm -f "$ZIP_NAME"
  python3 -m zipfile -c "$ZIP_NAME" MatchedBettingDocumenter
)

rm -rf "$STAGING"
echo "Share this file: $(pwd)/$OUT_DIR/$ZIP_NAME"
