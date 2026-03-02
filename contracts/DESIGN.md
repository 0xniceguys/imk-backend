# Solana Betting Contract — Interface Design

## Overview

Parimutuel betting pool on-chain for IMK matches. The backend acts as the
**authority** (creates matches, settles outcomes). Users place bets via the
Flutter app which signs transactions through Privy embedded wallets.

## Program Accounts

### `BettingPool` (PDA per match)
Seeds: `["pool", match_id_bytes]`

```
match_id:        [u8; 16]    // UUID bytes of the match
fighter1:        Pubkey       // not a wallet — just an identifier for the side
fighter2:        Pubkey       // same — could be derived from a hash of fighter slug
authority:       Pubkey       // backend signer that can settle/cancel
status:          u8           // 0=open, 1=locked, 2=settled, 3=cancelled
winner:          Option<Pubkey>
total_pool:      u64          // lamports
fighter1_pool:   u64          // lamports bet on fighter1
fighter2_pool:   u64          // lamports bet on fighter2
created_at:      i64          // unix timestamp
settled_at:      Option<i64>
bump:            u8
```

### `BetSlip` (PDA per bet)
Seeds: `["bet", pool_pubkey_bytes, user_pubkey_bytes, bet_index_bytes]`

```
pool:            Pubkey       // parent BettingPool
user:            Pubkey       // bettor's wallet
fighter:         Pubkey       // which side they bet on
amount:          u64          // lamports deposited
odds_snapshot:   u64          // pool ratio * 1e6 at time of bet (for display only)
status:          u8           // 0=active, 1=won, 2=lost, 3=cancelled
payout:          u64          // filled on settlement
placed_at:       i64
settled_at:      Option<i64>
bump:            u8
```

### `HouseConfig` (single global PDA)
Seeds: `["house"]`

```
authority:       Pubkey       // admin/backend signer
fee_bps:         u16          // house fee in basis points (e.g., 250 = 2.5%)
treasury:        Pubkey       // SOL vault for collected fees
paused:          bool         // emergency pause
bump:            u8
```

## Instructions

### 1. `create_pool`
**Signer**: authority (backend)
**Accounts**: authority, pool (init PDA), system_program
**Args**: match_id (16 bytes), fighter1 (Pubkey), fighter2 (Pubkey)
**Effect**: Creates a new BettingPool with status=open
**When**: Backend creates a match (admin "Create Match" form)

### 2. `place_bet`
**Signer**: user (via Privy embedded wallet)
**Accounts**: user, pool (mut), bet_slip (init PDA), house_config, system_program
**Args**: fighter (Pubkey — which side), amount (u64 — lamports)
**Validation**:
- pool.status == open
- fighter == pool.fighter1 OR pool.fighter2
- amount > 0
**Effect**:
- Transfers `amount` lamports from user → pool PDA
- Creates BetSlip
- Increments pool.total_pool and corresponding fighter pool
**When**: User taps "Place Bet" in Flutter app

### 3. `lock_pool`
**Signer**: authority
**Accounts**: authority, pool (mut)
**Effect**: Sets pool.status = locked (no more bets)
**When**: Backend starts the match (status → LIVE)

### 4. `settle_pool`
**Signer**: authority
**Accounts**: authority, pool (mut), house_config, treasury (mut)
**Args**: winner (Pubkey)
**Validation**:
- pool.status == locked
- winner == pool.fighter1 OR pool.fighter2
**Effect**:
- Sets pool.status = settled, pool.winner, pool.settled_at
- Calculates house fee: `total_pool * fee_bps / 10000`
- Transfers fee to treasury
- Does NOT distribute to bettors yet (see `claim`)
**When**: Match ends + winner determined (auto or manual settle in admin)

### 5. `claim`
**Signer**: user
**Accounts**: user (mut), pool, bet_slip (mut)
**Validation**:
- pool.status == settled
- bet_slip.status == active
- bet_slip.fighter == pool.winner
**Effect**:
- Calculates payout: `bet_amount * (total_pool - fee) / winner_pool`
- Transfers payout from pool PDA → user
- Sets bet_slip.status = won, bet_slip.payout
**When**: User opens post-match screen or taps "Claim"
**Note**: Losers' bet_slips stay active; a separate view instruction or
client-side logic marks them as lost.

### 6. `cancel_pool`
**Signer**: authority
**Accounts**: authority, pool (mut)
**Validation**: pool.status in (open, locked)
**Effect**: Sets pool.status = cancelled
**When**: Admin cancels match

### 7. `refund`
**Signer**: user
**Accounts**: user (mut), pool, bet_slip (mut)
**Validation**:
- pool.status == cancelled
- bet_slip.status == active
**Effect**:
- Transfers bet_slip.amount from pool PDA → user
- Sets bet_slip.status = cancelled
**When**: User claims refund after match cancellation

## Backend Integration Points

### On Match Creation (admin_views.py → match_new_submit)
```python
# After creating Match + Fighter records in DB:
tx = create_pool_ix(match_id=match.id, fighter1=f1.slug, fighter2=f2.slug)
# Backend signs + sends tx with authority keypair
```

### On Match Start (admin_views.py → match_start)
```python
# After setting match.status = LIVE:
tx = lock_pool_ix(pool_pda=derive_pool_pda(match.id))
# Backend signs + sends
```

### On Place Bet (Flutter → Privy embedded wallet)
```dart
// Build transaction client-side:
final tx = PlaceBetTransaction(
  pool: poolPda,            // derived from match_id
  fighter: selectedFighter,  // derived from fighter slug
  amount: amountLamports,
);
// Sign + send via Privy:
final sig = await ref.read(walletProvider.notifier)
    .signAndSendTransaction(tx.serialize());
// POST sig to backend for DB record
```

### On Match Settle (admin_views.py → match_settle)
```python
# After determining winner:
tx = settle_pool_ix(pool_pda=..., winner=winner_fighter_pubkey)
# Backend signs + sends
# Then update DB bets status
```

### On Claim (Flutter → post-match screen)
```dart
// User taps "Claim Winnings":
final tx = ClaimTransaction(pool: poolPda, betSlip: betSlipPda);
final sig = await ref.read(walletProvider.notifier)
    .signAndSendTransaction(tx.serialize());
```

## PDA Derivation

```
pool_pda       = findProgramAddress(["pool", match_id_bytes], PROGRAM_ID)
bet_slip_pda   = findProgramAddress(["bet", pool_pda, user_pubkey, index_le_bytes], PROGRAM_ID)
house_pda      = findProgramAddress(["house"], PROGRAM_ID)
fighter_pubkey  = hash(fighter_slug) → use as identifier, NOT a real wallet
```

## Fee Structure

- Default: 2.5% (250 bps) of total pool on settlement
- Configurable via `HouseConfig.fee_bps` (authority-only update)
- Fee goes to `treasury` wallet

## Token Support

Phase 1: SOL only (native lamports)
Phase 2: SPL tokens (USDC) — add token accounts to pool + bet_slip

## Transaction Sizes

All instructions fit in a single Solana transaction. No need for
lookup tables or versioned transactions at this scale.

## Security Considerations

- Authority keypair should be stored in backend env, never exposed
- Pool PDA holds all funds — no intermediary token accounts needed for SOL
- Claim is permissionless (user-initiated) — no rug risk
- Cancel + refund path ensures funds are recoverable
- `house_config.paused` for emergency stop
