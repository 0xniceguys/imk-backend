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

: "${SOLANA_URL:=https://api.devnet.solana.com}"
: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${DEPLOYER_KEYPAIR:=$KEYS_DIR/devnet-deployer.json}"
: "${ADMIN_KEYPAIR:=$KEYS_DIR/devnet-admin.json}"
: "${TREASURY_KEYPAIR:=$KEYS_DIR/devnet-treasury.json}"

command -v solana >/dev/null 2>&1 || { echo "solana CLI is required"; exit 1; }

SOL_PER_KEY="${SOL_PER_KEY:-2}"

for key in "$DEPLOYER_KEYPAIR" "$ADMIN_KEYPAIR" "$TREASURY_KEYPAIR"; do
  echo "[airdrop] $(basename "$key") -> ${SOL_PER_KEY} SOL"
  solana airdrop "$SOL_PER_KEY" --url "$SOLANA_URL" --keypair "$key"
  solana balance --url "$SOLANA_URL" --keypair "$key"
  echo

done

echo "[done] airdrops attempted"
