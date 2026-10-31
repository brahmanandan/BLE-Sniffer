#!/usr/bin/env bash
# Build xiao_test_tool and package UF2s.
#
#   firmware/build.sh            -> firmware/xiao_test_tool/build/xiao_test_tool.uf2        (app only)
#                                   firmware/xiao_test_tool/build/xiao_test_tool_full.uf2   (S140 + app)
#
# Uses arduino-cli from .toolchain/ if present (see firmware/xiao_test_tool/README.md),
# otherwise the one on PATH with its default config.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKETCH="$ROOT/firmware/xiao_test_tool"
OUT="$SKETCH/build"
FQBN="${FQBN:-Seeeduino:nrf52:xiaonRF52840}"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PATH="$(dirname "$PY"):$PATH"   # adafruit-nrfutil is needed by the core's zip recipe

if [ -x "$ROOT/.toolchain/bin/arduino-cli" ]; then
  ACLI=("$ROOT/.toolchain/bin/arduino-cli" --config-file "$ROOT/.toolchain/arduino-cli.yaml")
  DATA="$ROOT/.toolchain/data"
else
  ACLI=(arduino-cli)
  DATA="$HOME/.arduino15"
fi

"$PY" "$ROOT/backend/hid_usage.py" --emit-c > "$SKETCH/usage_names.h"
"${ACLI[@]}" compile -b "$FQBN" --warnings default --output-dir "$OUT" "$SKETCH"

SD_HEX="$(ls "$DATA"/packages/Seeeduino/hardware/nrf52/*/bootloader/Seeed_XIAO_nRF52840/*s140_7.3.0.hex | tail -1)"
"$PY" "$ROOT/firmware/make_uf2.py" "$OUT/xiao_test_tool.ino.hex" "$OUT/xiao_test_tool.uf2"
"$PY" "$ROOT/firmware/make_uf2.py" "$OUT/xiao_test_tool.ino.hex" "$OUT/xiao_test_tool_full.uf2" --softdevice "$SD_HEX"
