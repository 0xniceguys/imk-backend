#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployment/devnet/.env.devnet}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${ADMIN_KEYPAIR:=$KEYS_DIR/devnet-admin.json}"

# Fallback for convenience: if NEW_* are not set, use init-style env names.
if [[ -z "${NEW_MIN_BET_BASE_UNITS:-}" && -n "${MIN_BET_BASE_UNITS:-}" ]]; then
  export NEW_MIN_BET_BASE_UNITS="$MIN_BET_BASE_UNITS"
fi
if [[ -z "${NEW_MAX_BET_BASE_UNITS:-}" && -n "${MAX_BET_BASE_UNITS:-}" ]]; then
  export NEW_MAX_BET_BASE_UNITS="$MAX_BET_BASE_UNITS"
fi

if [[ -z "${BETTING_PROGRAM_ID:-}" ]]; then
  echo "BETTING_PROGRAM_ID is required"
  exit 1
fi

if [[ -z "${NEW_ADMIN:-}" && -z "${NEW_TREASURY_WALLET:-}" && -z "${NEW_TREASURY:-}" && -z "${NEW_FEE_BPS:-}" && -z "${NEW_MIN_BET_BASE_UNITS:-}" && -z "${NEW_MAX_BET_BASE_UNITS:-}" ]]; then
  echo "No update values provided."
  echo "Set one or more of: NEW_ADMIN, NEW_TREASURY_WALLET, NEW_FEE_BPS, NEW_MIN_BET_BASE_UNITS, NEW_MAX_BET_BASE_UNITS"
  exit 1
fi

PY_BIN="python3"
if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
then
  PY_BIN="/home/ubuntu/imk/.venv/bin/python"
fi

"$PY_BIN" "$ROOT_DIR/deployment/devnet/scripts/33_update_config.py"

