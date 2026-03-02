#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROM_PATH="${ROM_PATH:-"$ROOT_DIR/Mortal Kombat 4 (USA).z64"}"

if [[ ! -f "$ROM_PATH" ]]; then
  echo "ROM not found: $ROM_PATH" >&2
  exit 1
fi

BREW_PREFIX="${BREW_PREFIX:-$(brew --prefix mupen64plus)}"

BASE_DIR="${M64P_BASE_DIR:-"$ROOT_DIR/.m64p"}"
if [[ -n "${M64P_INSTANCE_ID:-}" ]]; then
  BASE_DIR="$BASE_DIR/instances/$M64P_INSTANCE_ID"
fi

CFG_DIR="${M64P_CONFIG_DIR:-"$BASE_DIR/config"}"
DATA_DIR="${M64P_DATA_DIR:-"$BASE_DIR/data"}"
STATE_DIR="${M64P_STATE_DIR:-"$DATA_DIR/savestates"}"
SRAM_DIR="${M64P_SRAM_DIR:-"$DATA_DIR/sram"}"
SHOT_DIR="${M64P_SHOT_DIR:-"$DATA_DIR/screenshots"}"

mkdir -p "$CFG_DIR" "$STATE_DIR" "$SRAM_DIR" "$SHOT_DIR"

CFG_FILE="$CFG_DIR/mupen64plus.cfg"
BASE_CFG_SOURCE="${M64P_PROFILE_BASE_CFG:-"$ROOT_DIR/.m64p/config/mupen64plus.cfg"}"

if [[ -n "${M64P_PROFILE_NAME:-}" ]]; then
  if [[ ! -f "$BASE_CFG_SOURCE" ]]; then
    echo "Base Mupen config not found for profile apply: $BASE_CFG_SOURCE" >&2
    exit 1
  fi
  python3 "$ROOT_DIR/training/scripts/apply_m64p_profile.py" \
    --profile "$M64P_PROFILE_NAME" \
    --base-cfg "$BASE_CFG_SOURCE" \
    --out-cfg "$CFG_FILE" \
    >/dev/null
elif [[ ! -f "$CFG_FILE" && -f "$BASE_CFG_SOURCE" ]]; then
  cp "$BASE_CFG_SOURCE" "$CFG_FILE"
fi

ARGS=(
  --configdir "$CFG_DIR"
  --datadir "$BREW_PREFIX/share/mupen64plus"
  --plugindir "$BREW_PREFIX/lib/mupen64plus"
  --sshotdir "$SHOT_DIR"
  --set "Core[SaveStatePath]=$STATE_DIR"
  --set "Core[SaveSRAMPath]=$SRAM_DIR"
)

if [[ "${M64P_WINDOW_MODE:-windowed}" == "windowed" ]]; then
  ARGS+=(--windowed)
elif [[ "${M64P_WINDOW_MODE:-windowed}" == "fullscreen" ]]; then
  ARGS+=(--fullscreen)
fi

if [[ -n "${M64P_RESOLUTION:-}" ]]; then
  ARGS+=(--resolution "$M64P_RESOLUTION")
fi

if [[ "${M64P_NOSPEEDLIMIT:-0}" == "1" ]]; then
  ARGS+=(--nospeedlimit)
fi

if [[ -n "${M64P_GFX_PLUGIN:-}" ]]; then
  ARGS+=(--gfx "$M64P_GFX_PLUGIN")
fi

if [[ -n "${M64P_AUDIO_PLUGIN:-}" ]]; then
  ARGS+=(--audio "$M64P_AUDIO_PLUGIN")
fi

if [[ -n "${M64P_INPUT_PLUGIN:-}" ]]; then
  ARGS+=(--input "$M64P_INPUT_PLUGIN")
fi

if [[ -n "${M64P_RSP_PLUGIN:-}" ]]; then
  ARGS+=(--rsp "$M64P_RSP_PLUGIN")
fi

exec mupen64plus \
  "${ARGS[@]}" \
  "$@" \
  "$ROM_PATH"
