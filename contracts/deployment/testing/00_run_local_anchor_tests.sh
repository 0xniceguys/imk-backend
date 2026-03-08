#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROGRAM_DIR="$ROOT_DIR/skr_parimutuel_betting"

command -v anchor >/dev/null 2>&1 || { echo "anchor CLI is required"; exit 1; }
command -v yarn >/dev/null 2>&1 || { echo "yarn is required"; exit 1; }

cd "$PROGRAM_DIR"
yarn install --frozen-lockfile
anchor test

echo "[done] local Anchor test suite completed"
