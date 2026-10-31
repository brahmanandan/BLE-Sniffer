#!/usr/bin/env bash
# Render doc/*.svg to doc/*.png at 2x with headless Chrome (exact text layout, no extra deps).
#   doc/src/render.sh               # all diagrams
#   doc/src/render.sh doc/x.svg     # one file
set -euo pipefail
cd "$(dirname "$0")/.."
CHROME=${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser)}
PROFILE=$(mktemp -d)
trap 'rm -rf "$PROFILE"' EXIT
files=("$@")
[ ${#files[@]} -eq 0 ] && files=(*.svg)
for svg in "${files[@]}"; do
  svg=$(basename "$svg")
  # viewBox="0 0 W H"
  read -r W H < <(grep -o 'viewBox="0 0 [0-9]* [0-9]*"' "$svg" | head -1 | tr -dc '0-9 ' | awk '{print $3, $4}')
  "$CHROME" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --user-data-dir="$PROFILE" \
    --force-device-scale-factor=2 --window-size="$W,$H" --screenshot="$PWD/${svg%.svg}.png" \
    "file://$PWD/$svg" >/dev/null 2>&1
  echo "rendered ${svg%.svg}.png (${W}x${H} @2x)"
done
