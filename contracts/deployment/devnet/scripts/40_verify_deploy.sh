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

: "${SOLANA_URL:=https://api.devnet.solana.com}"

if [[ -z "${BETTING_PROGRAM_ID:-}" ]]; then
  echo "BETTING_PROGRAM_ID is required"
  exit 1
fi

if [[ -z "${ANCHOR_PROVIDER_URL:-}" ]]; then
  export ANCHOR_PROVIDER_URL="$SOLANA_URL"
fi

echo "[check] program account"
curl -s "$SOLANA_URL" -H 'Content-Type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getAccountInfo\",\"params\":[\"$BETTING_PROGRAM_ID\",{\"encoding\":\"base64\"}]}" | jq '{owner: .result.value.owner, executable: .result.value.executable, lamports: .result.value.lamports}'

echo "[check] config account via ts reader"
bash "$ROOT_DIR/deployment/devnet/scripts/31_read_config.sh"

echo "[done] verification complete"
