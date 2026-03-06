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

if [[ -z "${BETTING_PROGRAM_ID:-}" || -z "${SKR_MINT:-}" ]]; then
  echo "BETTING_PROGRAM_ID and SKR_MINT are required"
  exit 1
fi

mkdir -p "$KEYS_DIR"

U1="${U1_KEYPAIR:-$KEYS_DIR/devnet-full-u1.json}"
U2="${U2_KEYPAIR:-$KEYS_DIR/devnet-full-u2.json}"
U3="${U3_KEYPAIR:-$KEYS_DIR/devnet-full-u3.json}"
INTRUDER="${INTRUDER_KEYPAIR:-$KEYS_DIR/devnet-full-intruder.json}"
PAYER="${PAYER_KEYPAIR:-$KEYS_DIR/devnet-full-payer.json}"

for kp in "$U1" "$U2" "$U3" "$INTRUDER" "$PAYER"; do
  if [[ ! -f "$kp" ]]; then
    solana-keygen new --silent --no-bip39-passphrase --outfile "$kp" --force >/dev/null
  fi
done

for kp in "$U1" "$U2" "$U3" "$INTRUDER" "$PAYER"; do
  pub="$(solana-keygen pubkey "$kp")"
  solana airdrop 0.5 "$pub" --url devnet >/dev/null 2>&1 || true
done

# Fallback top-up from deployer if faucet is constrained.
for kp in "$U1" "$U2" "$U3" "$INTRUDER" "$PAYER"; do
  pub="$(solana-keygen pubkey "$kp")"
  bal="$(solana balance -u devnet "$pub" | awk '{print $1}')"
  if ! python3 - <<PY
b=float("$bal")
import sys
sys.exit(0 if b >= 0.02 else 1)
PY
  then
    solana transfer --url devnet --allow-unfunded-recipient --from "$DEPLOYER_KEYPAIR" --fee-payer "$DEPLOYER_KEYPAIR" "$pub" 0.03 >/dev/null
  fi
done

: "${U1_SKR_TOKENS:=12}"
: "${U2_SKR_TOKENS:=12}"
: "${U3_SKR_TOKENS:=12}"

for pair in "$U1:$U1_SKR_TOKENS" "$U2:$U2_SKR_TOKENS" "$U3:$U3_SKR_TOKENS"; do
  kp="${pair%%:*}"
  amount="${pair##*:}"
  pub="$(solana-keygen pubkey "$kp")"
  spl-token transfer "$SKR_MINT" "$amount" "$pub" \
    --owner "$DEPLOYER_KEYPAIR" \
    --fee-payer "$DEPLOYER_KEYPAIR" \
    --fund-recipient \
    --allow-unfunded-recipient \
    --url devnet >/dev/null
done

export U1_KEYPAIR="$U1"
export U2_KEYPAIR="$U2"
export U3_KEYPAIR="$U3"
export INTRUDER_KEYPAIR="$INTRUDER"
export PAYER_KEYPAIR="$PAYER"

PY_BIN="python3"
if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
then
  PY_BIN="/home/ubuntu/imk/.venv/bin/python"
fi

"$PY_BIN" "$ROOT_DIR/deployment/testing/30_devnet_full_contract_test.py"

