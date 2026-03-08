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

: "${PROGRAM_DIR:=$ROOT_DIR/skr_parimutuel_betting}"
: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${ADMIN_KEYPAIR:=$KEYS_DIR/devnet-admin.json}"

if [[ -z "${SKR_MINT:-}" || -z "${MIN_BET_BASE_UNITS:-}" || -z "${MAX_BET_BASE_UNITS:-}" ]]; then
  echo "SKR_MINT, MIN_BET_BASE_UNITS, MAX_BET_BASE_UNITS are required"
  exit 1
fi

if [[ -z "${TREASURY_WALLET:-}" ]]; then
  if command -v solana-keygen >/dev/null 2>&1; then
    export TREASURY_WALLET="$(solana-keygen pubkey "$TREASURY_KEYPAIR")"
  else
    PY_BIN="python3"
    if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
    then
      PY_BIN="/home/ubuntu/imk/.venv/bin/python"
    fi
    export TREASURY_WALLET="$("$PY_BIN" - <<PY
import json
from solders.keypair import Keypair
with open('$TREASURY_KEYPAIR', 'r', encoding='utf-8') as f:
    arr = json.load(f)
print(str(Keypair.from_bytes(bytes(arr)).pubkey()))
PY
)"
  fi
fi

export ANCHOR_PROVIDER_URL="${ANCHOR_PROVIDER_URL:-https://api.devnet.solana.com}"
export ANCHOR_WALLET="$ADMIN_KEYPAIR"

PY_BIN="python3"
if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
then
  PY_BIN="/home/ubuntu/imk/.venv/bin/python"
fi

"$PY_BIN" "$ROOT_DIR/deployment/devnet/scripts/30_init_config.py"
