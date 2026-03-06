#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployment/devnet/.env.devnet}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

: "${PROGRAM_DIR:=$ROOT_DIR/skr_parimutuel_betting}"
: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${ADMIN_KEYPAIR:=$KEYS_DIR/devnet-admin.json}"

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

"$PY_BIN" "$ROOT_DIR/deployment/devnet/scripts/31_read_config.py"
