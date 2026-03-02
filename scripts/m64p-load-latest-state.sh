#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="${M64P_BASE_DIR:-"$ROOT_DIR/.m64p"}"
if [[ -n "${M64P_INSTANCE_ID:-}" ]]; then
  BASE_DIR="$BASE_DIR/instances/$M64P_INSTANCE_ID"
fi

STATE_DIR="${M64P_STATE_DIR:-"$BASE_DIR/data/savestates"}"

mkdir -p "$STATE_DIR"

latest_name="$(ls -1t "$STATE_DIR" 2>/dev/null | head -n 1 || true)"

if [[ -z "$latest_name" ]]; then
  echo "No savestate files found in $STATE_DIR. Starting normal boot." >&2
  exec "$ROOT_DIR/scripts/m64p-mk4.sh" "$@"
fi

latest_path="$STATE_DIR/$latest_name"
echo "Loading savestate: $latest_path"
exec "$ROOT_DIR/scripts/m64p-mk4.sh" --savestate "$latest_path" "$@"
