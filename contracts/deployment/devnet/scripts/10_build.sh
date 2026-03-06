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

PROGRAM_DIR="${PROGRAM_DIR:-$ROOT_DIR/skr_parimutuel_betting}"
: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${DEPLOYER_KEYPAIR:=$KEYS_DIR/devnet-deployer.json}"

command -v anchor >/dev/null 2>&1 || { echo "anchor CLI is required"; exit 1; }
command -v yarn >/dev/null 2>&1 || { echo "yarn is required"; exit 1; }

export ANCHOR_PROVIDER_URL="${ANCHOR_PROVIDER_URL:-https://api.devnet.solana.com}"
export ANCHOR_WALLET="$DEPLOYER_KEYPAIR"

cd "$PROGRAM_DIR"
yarn install --frozen-lockfile
anchor build --no-idl

echo "[done] build complete"
