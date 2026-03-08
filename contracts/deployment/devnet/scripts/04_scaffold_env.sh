#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
EXAMPLE="$ROOT_DIR/deployment/devnet/.env.devnet.example"
TARGET="${ENV_FILE:-$ROOT_DIR/deployment/devnet/.env.devnet}"

if [[ ! -f "$TARGET" ]]; then
  cp "$EXAMPLE" "$TARGET"
  echo "[ok] created env file: $TARGET"
else
  echo "[skip] env file already exists: $TARGET"
fi

FORCE=0 bash "$ROOT_DIR/deployment/devnet/scripts/01_generate_keys.sh" >/dev/null

KEYS_DIR="${KEYS_DIR:-$ROOT_DIR/keys}"
if [[ -f "$KEYS_DIR/devnet-pubkeys.env" ]]; then
  set -a
  source "$KEYS_DIR/devnet-pubkeys.env"
  set +a
fi

if [[ -n "${TREASURY_PUBKEY:-}" ]]; then
  if rg -n '^TREASURY_WALLET=' "$TARGET" >/dev/null 2>&1; then
    sed -i -E "s|^TREASURY_WALLET=.*$|TREASURY_WALLET=$TREASURY_PUBKEY|" "$TARGET"
  else
    echo "TREASURY_WALLET=$TREASURY_PUBKEY" >> "$TARGET"
  fi
  echo "[ok] set TREASURY_WALLET=$TREASURY_PUBKEY"
fi

echo "[done] fill remaining values manually in: $TARGET"
