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

KEYS_DIR="${KEYS_DIR:-$ROOT_DIR/keys}"
DEPLOYER_KEYPAIR="${DEPLOYER_KEYPAIR:-$KEYS_DIR/devnet-deployer.json}"
ADMIN_KEYPAIR="${ADMIN_KEYPAIR:-$KEYS_DIR/devnet-admin.json}"
TREASURY_KEYPAIR="${TREASURY_KEYPAIR:-$KEYS_DIR/devnet-treasury.json}"

FORCE=0 bash "$ROOT_DIR/deployment/devnet/scripts/01_generate_keys.sh" >/dev/null

for f in "$DEPLOYER_KEYPAIR" "$ADMIN_KEYPAIR" "$TREASURY_KEYPAIR"; do
  [[ -f "$f" ]] || { echo "missing key: $f"; exit 1; }
done

cat "$KEYS_DIR/devnet-pubkeys.env"
