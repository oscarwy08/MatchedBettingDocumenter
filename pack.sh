#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="dist"
ZIP_NAME="MatchedBettingDocumenter.zip"
STAGING="$OUT_DIR/MatchedBettingDocumenter"

rm -rf "$OUT_DIR"
mkdir -p "$STAGING"

# Copy the app, skip local venv, git, caches, and personal data files.
rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'dist/' \
  --exclude 'data/*.db' \
  --exclude 'data/*.xlsx' \
  --exclude '.env' \
  ./ "$STAGING/"

mkdir -p "$STAGING/data"
chmod +x "$STAGING/start.sh" "$STAGING/pack.sh" "$STAGING/update.sh" "$STAGING/update.py" 2>/dev/null || true

(
  cd "$OUT_DIR"
  rm -f "$ZIP_NAME"
  python3 -m zipfile -c "$ZIP_NAME" MatchedBettingDocumenter
)

rm -rf "$STAGING"
echo "Share this file: $(pwd)/$OUT_DIR/$ZIP_NAME"
