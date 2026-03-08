#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <PROGRAM_ID>"
  exit 1
fi

PROGRAM_ID="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROGRAM_DIR="$ROOT_DIR/skr_parimutuel_betting"
LIB_RS="$PROGRAM_DIR/programs/skr_parimutuel_betting/src/lib.rs"
ANCHOR_TOML="$PROGRAM_DIR/Anchor.toml"

if [[ ! "$PROGRAM_ID" =~ ^[1-9A-HJ-NP-Za-km-z]{32,44}$ ]]; then
  echo "invalid program id format"
  exit 1
fi

cp "$LIB_RS" "$LIB_RS.bak"
cp "$ANCHOR_TOML" "$ANCHOR_TOML.bak"

sed -i -E "s|declare_id!\(\"[1-9A-HJ-NP-Za-km-z]{32,44}\"\);|declare_id!(\"$PROGRAM_ID\");|" "$LIB_RS"
sed -i -E "s|^skr_parimutuel_betting = \"[1-9A-HJ-NP-Za-km-z]{32,44}\"$|skr_parimutuel_betting = \"$PROGRAM_ID\"|" "$ANCHOR_TOML"

echo "[done] synced program id: $PROGRAM_ID"
echo "backup files:"
echo "  $LIB_RS.bak"
echo "  $ANCHOR_TOML.bak"
