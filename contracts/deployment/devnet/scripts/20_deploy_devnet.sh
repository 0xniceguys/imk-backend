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

: "${DEPLOY_CONFIRM:=NO}"
if [[ "$DEPLOY_CONFIRM" != "YES" ]]; then
  echo "refusing to deploy: set DEPLOY_CONFIRM=YES in .env.devnet"
  exit 1
fi

PROGRAM_DIR="${PROGRAM_DIR:-$ROOT_DIR/skr_parimutuel_betting}"
: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${DEPLOYER_KEYPAIR:=$KEYS_DIR/devnet-deployer.json}"
: "${PROGRAM_NAME:=skr_parimutuel_betting}"

command -v anchor >/dev/null 2>&1 || { echo "anchor CLI is required"; exit 1; }
command -v solana >/dev/null 2>&1 || { echo "solana CLI is required"; exit 1; }
command -v yarn >/dev/null 2>&1 || { echo "yarn is required"; exit 1; }

export ANCHOR_PROVIDER_URL="${ANCHOR_PROVIDER_URL:-https://api.devnet.solana.com}"
export ANCHOR_WALLET="$DEPLOYER_KEYPAIR"

cd "$PROGRAM_DIR"
yarn install --frozen-lockfile
anchor build --no-idl
anchor deploy --provider.cluster devnet --provider.wallet "$DEPLOYER_KEYPAIR"

PROGRAM_KEYPAIR="$PROGRAM_DIR/target/deploy/${PROGRAM_NAME}-keypair.json"
if [[ -f "$PROGRAM_KEYPAIR" ]]; then
  DEPLOYED_PROGRAM_ID="$(solana address -k "$PROGRAM_KEYPAIR")"
  echo "[done] deployed program id: $DEPLOYED_PROGRAM_ID"
  echo "Set BETTING_PROGRAM_ID=$DEPLOYED_PROGRAM_ID in .env.devnet and backend envs"
else
  echo "[warn] could not find program keypair: $PROGRAM_KEYPAIR"
fi
