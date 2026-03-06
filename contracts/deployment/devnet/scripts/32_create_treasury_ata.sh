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
: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${DEPLOYER_KEYPAIR:=$KEYS_DIR/devnet-deployer.json}"

if [[ -z "${SKR_MINT:-}" ]]; then
  echo "SKR_MINT is required"
  exit 1
fi

if [[ -z "${TREASURY_WALLET:-}" ]]; then
  if command -v solana-keygen >/dev/null 2>&1; then
    TREASURY_WALLET="$(solana-keygen pubkey "$TREASURY_KEYPAIR")"
  else
    echo "TREASURY_WALLET is required when solana-keygen is unavailable"
    exit 1
  fi
fi

command -v spl-token >/dev/null 2>&1 || { echo "spl-token CLI is required"; exit 1; }

ATA="$(
  spl-token --url "$SOLANA_URL" address \
    --owner "$TREASURY_WALLET" \
    --token "$SKR_MINT" \
    --verbose \
  | awk -F': ' '/Associated token address/ {print $2}' \
  | tail -n1
)"
echo "treasury ATA (expected): $ATA"

spl-token --url "$SOLANA_URL" create-account "$SKR_MINT" \
  --owner "$TREASURY_WALLET" \
  --fee-payer "$DEPLOYER_KEYPAIR" || true

spl-token --url "$SOLANA_URL" account-info --address "$ATA"

echo "[done] treasury ATA should exist: $ATA"
