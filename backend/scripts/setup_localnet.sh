#!/usr/bin/env bash
# =============================================================================
# setup_localnet.sh
# Sets up a complete Solana localnet environment for IMK betting integration
# testing — no Privy account needed.
#
# What this does:
#   1. Installs Solana CLI (if missing)
#   2. Installs Anchor CLI (if missing)
#   3. Generates admin + test-user keypairs (skips if already exist)
#   4. Starts solana-test-validator with those keypairs pre-funded
#   5. Builds + deploys the betting contract to localnet
#   6. Creates a mock SKR token mint
#   7. Creates ATA accounts for admin + test user
#   8. Mints SKR to the test user
#   9. Writes .env.localnet with all required env vars for the backend
#
# Usage:
#   bash scripts/setup_localnet.sh
#
# After setup, start the backend with:
#   export $(cat scripts/.env.localnet | xargs) && uvicorn app.main:app --reload
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
KEYS_DIR="$SCRIPT_DIR/localnet_keys"
CONTRACT_DIR="$REPO_ROOT/contracts/skr_parimutuel_betting"
ENV_FILE="$SCRIPT_DIR/.env.localnet"

LOCALNET_RPC="http://127.0.0.1:8899"
SKR_DECIMALS=6
INITIAL_SKR_AMOUNT=100000  # 100,000 SKR to test user

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── 1. Install Solana CLI ─────────────────────────────────────────────────────
if ! command -v solana &>/dev/null; then
    info "Installing Solana CLI..."
    sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
    export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"
    info "Solana CLI installed: $(solana --version)"
else
    info "Solana CLI already installed: $(solana --version)"
fi

# ── 2. Install Anchor CLI ─────────────────────────────────────────────────────
if ! command -v anchor &>/dev/null; then
    info "Installing Anchor CLI via avm..."
    cargo install --git https://github.com/coral-xyz/anchor avm --locked --force
    avm install latest
    avm use latest
    info "Anchor CLI installed: $(anchor --version)"
else
    info "Anchor CLI already installed: $(anchor --version)"
fi

# ── 3. Generate keypairs ──────────────────────────────────────────────────────
mkdir -p "$KEYS_DIR"

if [ ! -f "$KEYS_DIR/admin.json" ]; then
    info "Generating admin keypair..."
    solana-keygen new --no-bip39-passphrase -o "$KEYS_DIR/admin.json" --force
else
    info "Admin keypair already exists: $KEYS_DIR/admin.json"
fi

if [ ! -f "$KEYS_DIR/test_user.json" ]; then
    info "Generating test user keypair..."
    solana-keygen new --no-bip39-passphrase -o "$KEYS_DIR/test_user.json" --force
else
    info "Test user keypair already exists: $KEYS_DIR/test_user.json"
fi

if [ ! -f "$KEYS_DIR/mint_authority.json" ]; then
    info "Generating mint authority keypair..."
    solana-keygen new --no-bip39-passphrase -o "$KEYS_DIR/mint_authority.json" --force
fi

ADMIN_PUBKEY=$(solana-keygen pubkey "$KEYS_DIR/admin.json")
TEST_USER_PUBKEY=$(solana-keygen pubkey "$KEYS_DIR/test_user.json")
info "Admin pubkey:     $ADMIN_PUBKEY"
info "Test user pubkey: $TEST_USER_PUBKEY"

# ── 4. Start solana-test-validator ────────────────────────────────────────────
info "Checking if solana-test-validator is already running..."
if pgrep -f "solana-test-validator" &>/dev/null; then
    warn "solana-test-validator already running — skipping start."
else
    info "Starting solana-test-validator..."
    solana-test-validator \
        --reset \
        --ledger /tmp/imk-localnet-ledger \
        --bind-address 127.0.0.1 \
        --rpc-port 8899 \
        --quiet &
    VALIDATOR_PID=$!
    echo "$VALIDATOR_PID" > /tmp/imk-localnet-validator.pid
    info "Validator PID: $VALIDATOR_PID — waiting for it to start..."
    sleep 5
fi

solana config set --url "$LOCALNET_RPC"

# ── 5. Fund keypairs ──────────────────────────────────────────────────────────
info "Airdropping SOL to admin and test user..."
solana airdrop 100 "$ADMIN_PUBKEY" --url "$LOCALNET_RPC" || true
solana airdrop 10  "$TEST_USER_PUBKEY" --url "$LOCALNET_RPC" || true
sleep 2

# ── 6. Build + deploy the contract ───────────────────────────────────────────
info "Building Anchor contract..."
cd "$CONTRACT_DIR"
anchor build

PROGRAM_KEYPAIR="$CONTRACT_DIR/target/deploy/skr_parimutuel_betting-keypair.json"
PROGRAM_ID=$(solana-keygen pubkey "$PROGRAM_KEYPAIR")
info "Program ID: $PROGRAM_ID"

info "Deploying contract to localnet..."
anchor deploy \
    --provider.cluster localnet \
    --provider.wallet "$KEYS_DIR/admin.json"

# ── 7. Create mock SKR mint ───────────────────────────────────────────────────
info "Creating mock SKR mint (6 decimals)..."
SKR_MINT=$(spl-token create-token \
    --mint-authority "$KEYS_DIR/mint_authority.json" \
    --url "$LOCALNET_RPC" \
    --fee-payer "$KEYS_DIR/admin.json" \
    --decimals "$SKR_DECIMALS" \
    "$KEYS_DIR/mint_authority.json" \
    2>&1 | grep "Creating token" | awk '{print $3}')

info "SKR Mint address: $SKR_MINT"

# ── 8. Create ATAs + mint SKR ─────────────────────────────────────────────────
info "Creating ATA for test user..."
spl-token create-account "$SKR_MINT" \
    --owner "$TEST_USER_PUBKEY" \
    --url "$LOCALNET_RPC" \
    --fee-payer "$KEYS_DIR/admin.json" || true

info "Creating ATA for admin (treasury)..."
spl-token create-account "$SKR_MINT" \
    --owner "$ADMIN_PUBKEY" \
    --url "$LOCALNET_RPC" \
    --fee-payer "$KEYS_DIR/admin.json" || true

info "Minting ${INITIAL_SKR_AMOUNT} SKR to test user..."
spl-token mint "$SKR_MINT" "$INITIAL_SKR_AMOUNT" \
    --mint-authority "$KEYS_DIR/mint_authority.json" \
    --recipient-owner "$TEST_USER_PUBKEY" \
    --url "$LOCALNET_RPC" \
    --fee-payer "$KEYS_DIR/admin.json"

info "Minting 10000 SKR to admin (for treasury)..."
spl-token mint "$SKR_MINT" 10000 \
    --mint-authority "$KEYS_DIR/mint_authority.json" \
    --recipient-owner "$ADMIN_PUBKEY" \
    --url "$LOCALNET_RPC" \
    --fee-payer "$KEYS_DIR/admin.json"

# ── 9. Extract private keys in base58 ────────────────────────────────────────
# Solana keypair JSON is a byte array — convert to base58 for backend env vars.
ADMIN_PRIVKEY_B58=$(python3 -c "
import json, base58
kp = json.load(open('$KEYS_DIR/admin.json'))
print(base58.b58encode(bytes(kp)).decode())
")

TEST_USER_PRIVKEY_B58=$(python3 -c "
import json, base58
kp = json.load(open('$KEYS_DIR/test_user.json'))
print(base58.b58encode(bytes(kp)).decode())
")

# ── 10. Write .env.localnet ───────────────────────────────────────────────────
info "Writing $ENV_FILE..."
cat > "$ENV_FILE" <<EOF
# =============================================================
# IMK Localnet Testing Environment
# Generated by setup_localnet.sh — DO NOT COMMIT
# Use: export \$(cat scripts/.env.localnet | xargs) && uvicorn app.main:app --reload
# =============================================================

# Solana
USE_DEVNET=false
LOCAL_RPC_URL=http://127.0.0.1:8899
BETTING_PROGRAM_ID=$PROGRAM_ID
SKR_MINT=$SKR_MINT
TREASURY_WALLET=$ADMIN_PUBKEY

# Admin keypair (fee payer + admin ops)
ADMIN_KEYPAIR_B58=$ADMIN_PRIVKEY_B58

# Test user bypass — skips Privy JWT validation and signing
TEST_USER_WALLET=$TEST_USER_PUBKEY
TEST_USER_PRIVKEY_B58=$TEST_USER_PRIVKEY_B58

# Admin bypass for admin endpoints
DEV_ADMIN_BYPASS=true

# DB (adjust as needed)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/imk_localnet

# Privy (not called in test mode — dummy values)
PRIVY_APP_ID=localnet-test
PRIVY_APP_SECRET=localnet-test-secret
PRIVY_VERIFICATION_KEY=localnet-test-key
EOF

info "Done! Summary:"
echo ""
echo "  Program ID:       $PROGRAM_ID"
echo "  SKR Mint:         $SKR_MINT"
echo "  Admin wallet:     $ADMIN_PUBKEY"
echo "  Test user wallet: $TEST_USER_PUBKEY"
echo "  Test user SKR:    ${INITIAL_SKR_AMOUNT}"
echo ""
echo "Next steps:"
echo "  1. createdb imk_localnet  (if DB doesn't exist)"
echo "  2. export \$(cat scripts/.env.localnet | xargs)"
echo "  3. alembic upgrade head"
echo "  4. uvicorn app.main:app --reload"
echo "  5. python scripts/test_betting_flow.py"
