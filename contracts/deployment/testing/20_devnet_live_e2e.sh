#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployment/devnet/.env.devnet}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

: "${KEYS_DIR:=$ROOT_DIR/keys}"
: "${DEPLOYER_KEYPAIR:=$KEYS_DIR/devnet-deployer.json}"
: "${PROGRAM_DIR:=$ROOT_DIR/skr_parimutuel_betting}"

if [[ -z "${BETTING_PROGRAM_ID:-}" || -z "${SKR_MINT:-}" ]]; then
  echo "BETTING_PROGRAM_ID and SKR_MINT are required"
  exit 1
fi

mkdir -p "$KEYS_DIR"

USER1_KEYPAIR="${USER1_KEYPAIR:-$KEYS_DIR/devnet-live-user1.json}"
USER2_KEYPAIR="${USER2_KEYPAIR:-$KEYS_DIR/devnet-live-user2.json}"

if [[ ! -f "$USER1_KEYPAIR" ]]; then
  solana-keygen new --silent --no-bip39-passphrase --outfile "$USER1_KEYPAIR" --force >/dev/null
fi
if [[ ! -f "$USER2_KEYPAIR" ]]; then
  solana-keygen new --silent --no-bip39-passphrase --outfile "$USER2_KEYPAIR" --force >/dev/null
fi

USER1_PUB="$(solana-keygen pubkey "$USER1_KEYPAIR")"
USER2_PUB="$(solana-keygen pubkey "$USER2_KEYPAIR")"

echo "[info] user1: $USER1_PUB"
echo "[info] user2: $USER2_PUB"

# Fund SOL for transaction fees (try faucet first, fallback to deployer transfer).
solana airdrop 0.5 "$USER1_PUB" --url devnet >/dev/null 2>&1 || true
solana airdrop 0.5 "$USER2_PUB" --url devnet >/dev/null 2>&1 || true

user1_sol="$(solana balance -u devnet "$USER1_PUB" | awk '{print $1}')"
user2_sol="$(solana balance -u devnet "$USER2_PUB" | awk '{print $1}')"

if python3 - <<PY
u1=float("$user1_sol")
u2=float("$user2_sol")
import sys
sys.exit(0 if (u1 >= 0.02 and u2 >= 0.02) else 1)
PY
then
  echo "[info] users have sufficient SOL"
else
  echo "[info] faucet low; topping up from deployer"
  solana transfer --url devnet --allow-unfunded-recipient --from "$DEPLOYER_KEYPAIR" --fee-payer "$DEPLOYER_KEYPAIR" "$USER1_PUB" 0.03 >/dev/null
  solana transfer --url devnet --allow-unfunded-recipient --from "$DEPLOYER_KEYPAIR" --fee-payer "$DEPLOYER_KEYPAIR" "$USER2_PUB" 0.03 >/dev/null
fi

: "${USER1_SKR_TOKENS:=5}"
: "${USER2_SKR_TOKENS:=5}"

echo "[info] sending SKR to users"
spl-token transfer "$SKR_MINT" "$USER1_SKR_TOKENS" "$USER1_PUB" \
  --owner "$DEPLOYER_KEYPAIR" \
  --fee-payer "$DEPLOYER_KEYPAIR" \
  --fund-recipient \
  --allow-unfunded-recipient \
  --url devnet >/dev/null

spl-token transfer "$SKR_MINT" "$USER2_SKR_TOKENS" "$USER2_PUB" \
  --owner "$DEPLOYER_KEYPAIR" \
  --fee-payer "$DEPLOYER_KEYPAIR" \
  --fund-recipient \
  --allow-unfunded-recipient \
  --url devnet >/dev/null

PY_BIN="python3"
if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
then
  PY_BIN="/home/ubuntu/imk/.venv/bin/python"
fi

export USER1_KEYPAIR USER2_KEYPAIR
"$PY_BIN" "$ROOT_DIR/deployment/testing/20_devnet_live_e2e.py"

