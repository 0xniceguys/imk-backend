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
FORCE="${FORCE:-0}"

mkdir -p "$KEYS_DIR"
chmod 700 "$KEYS_DIR"

gen_with_solana() {
  local out="$1"
  solana-keygen new --silent --no-bip39-passphrase --outfile "$out" >/dev/null
}

gen_with_python() {
  local out="$1"
  local py="python3"
  if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
  then
    if [[ -x /home/ubuntu/imk/.venv/bin/python ]]; then
      py="/home/ubuntu/imk/.venv/bin/python"
    fi
  fi

  "$py" - <<PY
import json
from solders.keypair import Keypair
kp = Keypair()
with open("$out", "w", encoding="utf-8") as f:
    json.dump(list(bytes(kp)), f)
    f.write("\n")
PY
}

pubkey_from_file() {
  local file="$1"
  if command -v solana-keygen >/dev/null 2>&1; then
    solana-keygen pubkey "$file"
    return
  fi

  local py="python3"
  if ! python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
  then
    py="/home/ubuntu/imk/.venv/bin/python"
  fi

  "$py" - <<PY
import json
from solders.keypair import Keypair
with open("$file", "r", encoding="utf-8") as f:
    arr = json.load(f)
kp = Keypair.from_bytes(bytes(arr))
print(str(kp.pubkey()))
PY
}

maybe_generate() {
  local target="$1"
  if [[ -f "$target" && "$FORCE" != "1" ]]; then
    echo "[skip] key exists: $target"
    return
  fi

  if command -v solana-keygen >/dev/null 2>&1; then
    gen_with_solana "$target"
  else
    gen_with_python "$target"
  fi
  chmod 600 "$target"
  echo "[ok] generated: $target"
}

maybe_generate "$DEPLOYER_KEYPAIR"
maybe_generate "$ADMIN_KEYPAIR"
maybe_generate "$TREASURY_KEYPAIR"

DEPLOYER_PUBKEY="$(pubkey_from_file "$DEPLOYER_KEYPAIR")"
ADMIN_PUBKEY="$(pubkey_from_file "$ADMIN_KEYPAIR")"
TREASURY_PUBKEY="$(pubkey_from_file "$TREASURY_KEYPAIR")"

PUBKEYS_ENV="$KEYS_DIR/devnet-pubkeys.env"
cat > "$PUBKEYS_ENV" <<ENV
DEPLOYER_PUBKEY=$DEPLOYER_PUBKEY
ADMIN_PUBKEY=$ADMIN_PUBKEY
TREASURY_PUBKEY=$TREASURY_PUBKEY
ENV
chmod 600 "$PUBKEYS_ENV"

echo "[done] keys ready"
echo "  deployer: $DEPLOYER_PUBKEY"
echo "  admin:    $ADMIN_PUBKEY"
echo "  treasury: $TREASURY_PUBKEY"
echo "  pubkeys:  $PUBKEYS_ENV"
