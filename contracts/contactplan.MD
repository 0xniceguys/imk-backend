## v4 Final Spec — SKR Parimutuel Betting (Solana)

> **Changelog from v1 → v2 → v3 → v4:** See bottom of document.

Constraints (pinned):

* Token: **SKR SPL**, **decimals = 6**
* **Multiple concurrent OPEN matches** supported (Admin can open many markets at once)
* Users: **1 bet per wallet per match**, **no cancellations on placed bets**
* Admin: **single key** resolves; all config values updatable via `update_config`
* Fee: **5% (500 bps) default**, updatable, taken **once at resolve**
* Claims: users call `claim` themselves
* Auto-close: on **last winning claim** (or immediately at resolve if `winning_total == 0`)
* Rent reclaim: **close UserBet after successful claim**; **close Match + MatchVault on auto-close**; rent to **admin keypair**
* Cancel: admin can **cancel OPEN or LOCKED** match; bettors call `refund_bet` per-user
* Pause: admin can **pause `place_bet`** via `set_paused`

---

# 0) Definitions

### Units

* All token math uses **base units**: `1 SKR = 1_000_000 base units`.

### Outcomes

* `Side = A | B`

### Parimutuel payout

Let:

* `total_a`, `total_b` = total staked on each side
* `pool = total_a + total_b`
* `fee = floor(pool * fee_bps / 10_000)`
* `payout_pool = pool - fee`
* `winning_total = total_a if winner==A else total_b`

For a winning user with `bet_amount`:

* `payout = floor(payout_pool * bet_amount / winning_total)`

**Rounding dust** (from floor division of payout) stays in the vault and is swept to treasury at final auto-close.

### Winning-side-zero case

If `winning_total == 0` (no one bet on the winning side), there are no claimants. `resolve_match` handles this inline:
* If `pool == 0`: close vault and match immediately, no transfers.
* If `pool > 0 && winning_total == 0`: transfer `fee` to treasury, sweep `payout_pool` to treasury, close vault and match immediately. No `claim` calls will ever be made for this match.

---

# 1) Accounts

## 1.1 Config (PDA)

Global settings + pointer to the current OPEN match.

**Seeds**

* `config_pda = PDA("config")`

**Fields**

* `admin: Pubkey`
* `skr_mint: Pubkey`
* `treasury_wallet: Pubkey` (System account/wallet that owns the treasury)
* `fee_bps: u16` (default 500; max 1_000 enforced at update)
* `min_bet: u64` (base units)
* `max_bet: u64` (base units)
* `match_counter: u64` (monotonic; first match gets id=0)
* `paused: bool` (blocks `place_bet` when true)

**Space:** `8 + 32 + 32 + 32 + 2 + 8 + 8 + 8 + 1 + 7 (padding) = 140 bytes` → allocate **168 bytes**

**Invariants**

* `fee_bps <= 1_000`
* `min_bet <= max_bet`
* `skr_mint` is immutable after `init_config`

---

## 1.2 Match (PDA)

Represents one market.

**Seeds**

* `match_pda = PDA("match", match.id.to_le_bytes())`

**Fields**

* `id: u64`
* `status: MatchStatus` enum:
  * `0 OPEN`
  * `1 LOCKED`
  * `2 RESOLVED`
  * `3 CANCELLED`
* `model_a_hash: [u8; 32]`
* `model_b_hash: [u8; 32]`
* `total_a: u64`
* `total_b: u64`
* `winner: WinnerSide` enum:
  * `0 NONE`
  * `1 A`
  * `2 B`
* `fee_amount: u64`
* `payout_pool: u64`
* `winning_total: u64`
* `claimed_winning_total: u64` (sum of bet amounts of winners who have claimed)
* `refunded_total: u64` (sum of bet amounts refunded on CANCELLED; used for auto-close)
* `vault_authority: Pubkey` (stored for client convenience; always verified against derived PDA)
* `vault_ata: Pubkey` (stored for client convenience; always verified against derived ATA)
* `created_at: i64` (unix timestamp; `0` = not meaningful, but set at creation so this will always be a real timestamp)
* `locked_at: i64` (`i64::MIN` = not locked yet)
* `resolved_at: i64` (`i64::MIN` = not resolved yet)

**Space:** `8 + 8 + 1 + 32 + 32 + 8 + 8 + 1 + 8 + 8 + 8 + 8 + 8 + 32 + 32 + 8 + 8 + 8 + 7 (padding) = 244 bytes` → allocate **264 bytes**

**Invariants**

* `match_counter` is strictly monotonic; each `match_pda` is unique forever (safe for UserBet seeds even after closure)
* If `status == RESOLVED`:
  * `winner != NONE`
  * `fee_amount`, `payout_pool`, `winning_total` are set
* `claimed_winning_total` increases only from winner claims
* `refunded_total` increases only from `refund_bet` calls on CANCELLED match
* Auto-close condition (normal): `claimed_winning_total == winning_total`
* Auto-close condition (cancelled): `refunded_total == total_a + total_b`
* `vault_authority` and `vault_ata` stored values MUST equal on-chain derived values — enforced in all instructions

---

## 1.3 Vault Authority (PDA)

Authority that owns the match vault token account.

**Seeds**

* `vault_auth = PDA("vault_auth", match_pda.key())`

Not stored as a standalone account. Derived on-the-fly in every instruction and asserted against `match.vault_authority`.

---

## 1.4 Match Vault Token Account (ATA)

SPL token account holding SKR in escrow for a match.

**Derivation**

* `vault_ata = ATA(vault_auth, skr_mint)`

**Authority**

* `owner/authority = vault_auth` (PDA)

**Note on external deposits:** Anyone can transfer SKR into the vault externally. The contract ignores these; all math is based on on-chain tracked totals (`total_a + total_b`). External tokens are swept to treasury as dust at auto-close. This is expected behavior.

---

## 1.5 UserBet (PDA per user per match)

Enforces one bet per match and stores bet info.

**Seeds**

* `user_bet_pda = PDA("bet", match_pda.key(), user_pubkey)`

**Fields**

* `match: Pubkey`
* `user: Pubkey`
* `side: Side` (1=A, 2=B)
* `amount: u64`

**Space:** `8 + 32 + 32 + 1 + 8 + 7 (padding) = 88 bytes` → allocate **96 bytes**

**Invariants**

* Exists at most once per (match, user). Anchor `init` enforces this.
* Account is closed on successful `claim` or `refund_bet`. Its existence is the "unclaimed" flag.
* Losers' UserBets persist until `close_losing_bet` is called (permissionless after RESOLVED).

---

# 2) Instruction Set

## 2.1 `init_config`

One-time initialization. Anchor `init` constraint prevents re-calling.

**Signer:** admin
**Creates:** Config PDA

**Args**

* `skr_mint: Pubkey`
* `treasury_ata: Pubkey`
* `min_bet: u64`
* `max_bet: u64`

**Checks**

* `min_bet <= max_bet`
* `treasury_ata.mint == skr_mint`
* `treasury_ata` must be the canonical ATA of the treasury owner for `skr_mint` — use `get_associated_token_address(treasury_owner, skr_mint)` to derive and assert. This prevents accidentally passing a random token account as treasury.

**Writes**

* `admin = signer`
* `skr_mint = arg`
* `treasury_wallet = arg`
* `fee_bps = 500`
* `min_bet = arg`, `max_bet = arg`
* `match_counter = 0`
* `paused = false`

**SKR movement:** none

---

## 2.2 `update_config`

Update any/all mutable config values. `skr_mint` is permanently immutable.

**Signer:** admin
**Args** (all optional / pass current value to leave unchanged)

* `new_admin: Option<Pubkey>`
* `new_treasury_wallet: Option<Pubkey>`
* `new_fee_bps: Option<u16>`
* `new_min_bet: Option<u64>`
* `new_max_bet: Option<u64>`

**Checks**

* `signer == config.admin`
* If `new_fee_bps`: `new_fee_bps <= 1_000`
* If `new_min_bet` and `new_max_bet`: `new_min_bet <= new_max_bet`
* If only one of min/max provided: check against the existing other value

**Writes**

* Apply whichever fields were provided

**Note:** If `new_admin` is set, the new admin takes effect immediately. Single-step transfer — admin is responsible for not bricking access.

**SKR movement:** none

---

## 2.3 `set_paused`

Toggle the global pause flag.

**Signer:** admin
**Args:** `paused: bool`

**Checks**

* `signer == config.admin`

**Writes**

* `config.paused = arg`

**Effect:** When `paused = true`, `place_bet` is blocked. All admin instructions and `claim` remain unaffected.

**SKR movement:** none

---

## 2.4 `create_match`

Creates the new OPEN match plus its vault.

**Signer:** admin
**Creates:** Match PDA, vault_auth PDA, vault ATA

**Args**

* `model_a_hash: [u8; 32]`
* `model_b_hash: [u8; 32]`

**Checks**

* `signer == config.admin`
* `!config.paused` (optional: you may allow match creation while paused)

**Writes**

* `match.id = config.match_counter` (use current value; first match gets id=0)
* `config.match_counter += 1`
* Initialize match:
  * `status = OPEN`
  * `total_a = 0`, `total_b = 0`
  * `winner = NONE`
  * `fee_amount = 0`, `payout_pool = 0`, `winning_total = 0`
  * `claimed_winning_total = 0`, `refunded_total = 0`
  * `vault_authority = PDA("vault_auth", match_pda.key())`
  * `vault_ata = ATA(vault_authority, skr_mint)`
  * `created_at = Clock::unix_timestamp`
  * `locked_at = i64::MIN`, `resolved_at = i64::MIN`

**SKR movement:** none

---

## 2.5 `place_bet`

Transfers SKR into vault and creates UserBet.

**Signer:** user (bettor)
**Creates:** UserBet PDA
**Token movement:** user ATA → vault ATA

**Args**

* `side: Side` (1=A, 2=B)
* `amount: u64`

**Accounts**

* `config`
* `match` (can be any OPEN match)
* `user_bet_pda` (init)
* `user_skr_ata`
* `vault_ata`
* `token_program`, `associated_token_program`, `system_program`

**Checks**

* `!config.paused` — error: `SystemPaused`
* `match.status == OPEN` — error: `MatchNotOpen`
* `amount >= config.min_bet && amount <= config.max_bet` — error: `BetOutOfRange`
* `side in {A, B}` — error: `InvalidSide`
* `user_skr_ata.mint == config.skr_mint` — error: `TokenMintMismatch`
* `user_skr_ata.owner (authority) == signer` — error: `Unauthorized`
* `vault_ata.key() == match.vault_ata` — error: `VaultAuthorityMismatch`
* `vault_ata.mint == config.skr_mint` — error: `TokenMintMismatch`
* Derive `vault_auth = PDA("vault_auth", match.key())`, assert `vault_auth == match.vault_authority`
* UserBet PDA does not exist (Anchor `init` enforces, error: `AlreadyBet`)

**Writes**

* Transfer `amount` SKR: user ATA → vault ATA
* Initialize `user_bet`:
  * `match = match.key()`
  * `user = signer`
  * `side = arg`
  * `amount = arg`
* If side A: `match.total_a += amount`
* If side B: `match.total_b += amount`

---

## 2.6 `lock_match`

Stops betting for a specific match.

**Signer:** admin
**Args:** none (match is passed as an account)

**Checks**

* `signer == config.admin`
* `match.status == OPEN` — error: `MatchNotOpen`

**Note:** No enforcement of `total_a > 0 || total_b > 0`. Admin may lock with 0 bets on either or both sides. The zero-total case is handled in `resolve_match`.

**Writes**

* `match.status = LOCKED`
* `match.locked_at = Clock::unix_timestamp`

**Concurrency note:** Admin can have 10, 50, or 100 matches OPEN at once. Locking just advances the targeted match out of the betting phase. Users continue to bet on any remaining OPEN matches seamlessly.

**SKR movement:** none

---

## 2.7 `resolve_match`

Computes fee, transfers fee to treasury, finalizes payout pool, sets winner. Handles all `winning_total` cases (including zero).

**Signer:** admin
**Args**

* `winner_side: Side` (1=A, 2=B)

**Accounts**

* `config`
* `match`
* `vault_ata`
* `vault_auth` (PDA, for CPI signing)
* `treasury_ata` (derived off-chain, checked on-chain against `config.treasury_wallet`)
* `token_program`
* `system_program` (if auto-closing)
* `admin` (rent receiver if auto-closing)

**Checks**

* `signer == config.admin`
* `match.status == LOCKED` — error: `MatchNotLocked`
* `winner_side in {A, B}` — error: `InvalidSide`
* Derive canonical ATA for `config.treasury_wallet` and `config.skr_mint`; must equal `treasury_ata.key()` — error: `Unauthorized`
* `vault_ata.key() == match.vault_ata` — error: `VaultAuthorityMismatch`
* Derive `vault_auth = PDA("vault_auth", match.key())`, assert equals `match.vault_authority`

**Compute**

```
pool = match.total_a + match.total_b
fee = floor(pool * config.fee_bps / 10_000)
payout_pool = pool - fee
winning_total = if winner_side == A { match.total_a } else { match.total_b }
```

**Write match fields** (before any auto-close branch):

```
match.winner = winner_side
match.fee_amount = fee
match.payout_pool = payout_pool
match.winning_total = winning_total
match.resolved_at = Clock::unix_timestamp
match.status = RESOLVED
```

**Auto-close branches** (all transfers use `vault_auth` PDA seeds as CPI signer):

```
BRANCH A — pool == 0 (no bets at all):
  // No transfers needed; vault is empty
  close vault_ata → rent to admin
  close match → rent to admin

BRANCH B — pool > 0 && winning_total == 0 (all bets on losing side):
  transfer fee → treasury_ata (using vault_auth CPI signer)
  transfer payout_pool → treasury_ata (all goes to treasury as dust; no claimants)
  close vault_ata → rent to admin
  close match → rent to admin

BRANCH C — winning_total > 0 (normal case):
  transfer fee → treasury_ata (using vault_auth CPI signer)
  // Winners claim individually; match stays open until all winners claim
  // (or stays RESOLVED forever if winners never claim — accepted trust assumption)
```

---

## 2.8 `claim` (winner-only + auto-close + rent reclaim)

Pays the winning bettor, closes their UserBet, and (if last claim) sweeps dust + closes vault + closes match.

**Signer:** user
**Args:** none

**Accounts**

* `config` (for mint/treasury checks)
* `match`
* `user_bet_pda` (`close = user`)
* `user_skr_ata`
* `vault_ata`
* `vault_auth` (PDA)
* `treasury_ata` (derived off-chain, checked on-chain against `config.treasury_wallet`)
* `token_program`
* `system_program`
* `admin` (rent receiver for vault/match close, if last claim)

**Checks**

* `match.status == RESOLVED` — error: `MatchNotResolved`
* `user_bet.user == signer` — error: `Unauthorized`
* `user_bet.match == match.key()` — error: `Unauthorized`
* `user_bet.side == match.winner` — error: `NotWinner`
* `match.winning_total > 0` — error: `WinningTotalZero` (should be unreachable if BRANCH C; guards against logic bugs)
* Derive canonical ATA for `config.treasury_wallet` and `config.skr_mint`; must equal `treasury_ata.key()` — error: `Unauthorized`
* `user_skr_ata.mint == config.skr_mint` — error: `TokenMintMismatch`
* `user_skr_ata.owner == signer` — error: `Unauthorized`
* `vault_ata.key() == match.vault_ata` — error: `VaultAuthorityMismatch`
* Derive `vault_auth = PDA("vault_auth", match.key())`, assert equals `match.vault_authority`

**Compute payout**

```
payout = floor(match.payout_pool * user_bet.amount / match.winning_total)
```

Note: `payout` can theoretically be `0` if `bet_amount` is very small relative to `winning_total`. `min_bet` and the 6 decimal precision of SKR reduce this risk. No explicit revert on `payout == 0`; the claim proceeds silently (user gets rent back at minimum).

**Effects (ordered)**

1. Transfer `payout` SKR: `vault_ata` → `user_skr_ata` (using `vault_auth` PDA CPI signer)
2. `match.claimed_winning_total += user_bet.amount`
3. Close `user_bet_pda` → rent to **user** (Anchor: `close = user`)
4. Auto-close check:
   ```
   if match.claimed_winning_total == match.winning_total:
     remaining = vault_ata.amount  // dust from floor rounding
     if remaining > 0:
       transfer remaining SKR: vault_ata → treasury_ata (vault_auth CPI signer)
     close vault_ata → SOL rent to admin
     close match → SOL rent to admin
   ```

---

## 2.9 `close_losing_bet`

Returns rent to the **admin** by closing a UserBet. Permissionless — anyone can call on behalf of any loser after match is RESOLVED.

*(Note on Solana limits: We cannot loop through all losing bets automatically during `resolve_match` because of compute + memory limits. A match with 10k losers would break the block limit. Therefore, `close_losing_bet` must be called individually per losing bet, usually by an admin bot).*

**Signer:** any (payer for the transaction)
**Args:** none

**Accounts**

* `config`
* `match`
* `user_bet_pda` (`close = config.admin`)
* `admin` (the config.admin, receives the SOL rent from the closed account)
* `system_program`

**Checks**

* `match.status == RESOLVED` — error: `MatchNotResolved`
* `user_bet.match == match.key()` — error: `Unauthorized`
* `user_bet.side != match.winner` — error: `NotLoser` (winners must use `claim`)

**Effects**

1. Close `user_bet_pda` → rent sent to `config.admin`

**SKR movement:** none (loser's SKR tokens are already in the payout pool / fee, now resolved)

---

## 2.10 `cancel_match`

Admin cancels an OPEN or LOCKED match. Bettors call `refund_bet` individually to recover SKR. If match has 0 bets, vault and match are closed immediately.

**Signer:** admin
**Args:** none

**Accounts**

* `config`
* `match`
* `vault_ata` (needed for immediate-close case)
* `vault_auth` (PDA)
* `token_program`
* `system_program`
* `admin` (rent receiver)

**Checks**

* `signer == config.admin`
* `match.status == OPEN || match.status == LOCKED` — error: `MatchNotCancellable`
**Effects**

1. `match.status = CANCELLED`
3. Immediate-close check (no refunds needed):
   ```
   if match.total_a + match.total_b == 0:
     close vault_ata → rent to admin
     close match → rent to admin
   ```
   Otherwise: match stays on-chain as CANCELLED for `refund_bet` calls.

**SKR movement:** none (unless immediate-close; vault is already empty)

---

## 2.11 `refund_bet`

Returns a bettor's SKR on a CANCELLED match. Auto-closes vault and match when all bets are refunded.

**Signer:** user (bettor whose bet is being refunded)
**Args:** none

**Accounts**

* `config`
* `match`
* `user_bet_pda` (`close = user`)
* `user_skr_ata`
* `vault_ata`
* `vault_auth` (PDA)
* `token_program`
* `system_program`
* `admin` (rent receiver for vault/match close, if last refund)

**Checks**

* `match.status == CANCELLED` — error: `MatchNotCancelled`
* `user_bet.user == signer` — error: `Unauthorized`
* `user_bet.match == match.key()` — error: `Unauthorized`
* `user_skr_ata.mint == config.skr_mint` — error: `TokenMintMismatch`
* `user_skr_ata.owner == signer` — error: `Unauthorized`
* `vault_ata.key() == match.vault_ata` — error: `VaultAuthorityMismatch`
* Derive `vault_auth = PDA("vault_auth", match.key())`, assert equals `match.vault_authority`

**Effects (ordered)**

1. Transfer `user_bet.amount` SKR: `vault_ata` → `user_skr_ata` (using `vault_auth` PDA CPI signer)
2. `match.refunded_total += user_bet.amount`
3. Close `user_bet_pda` → rent to **user** (Anchor: `close = user`)
4. Auto-close check:
   ```
   if match.refunded_total == match.total_a + match.total_b:
     close vault_ata → rent to admin
     close match → rent to admin
   ```

---

# 3) Close Receivers (SOL Rent Reclaim Policy)

| Account Closed | Rent Receiver |
|---|---|
| `UserBet` (claim) | **Bettor (user)** |
| `UserBet` (close_losing_bet) | **Admin (`config.admin`)** |
| `UserBet` (refund_bet) | **Bettor (user)** |
| `MatchVault ATA` (auto-close) | **Admin keypair** |
| `Match` (auto-close) | **Admin keypair** |
| `MatchVault ATA` (cancel, immediate) | **Admin keypair** |
| `Match` (cancel, immediate) | **Admin keypair** |

---

# 4) Trust Assumptions

These scenarios have no on-chain rescue path. They are accepted design trade-offs:

1. **Admin liveness:** If admin loses their key after `lock_match` but before `resolve_match`, the vault funds are permanently stuck. Mitigation is operational (admin key security, hardware wallet, backup).
2. **Winner abandonment:** If `winning_total > 0` but all winners never call `claim`, the vault stays RESOLVED forever. SKR and SOL rent are permanently stuck. No admin rescue path for RESOLVED matches.
3. **No timeout:** OPEN and LOCKED matches have no expiry. Admin MUST be live to progress the match lifecycle.

---

# 5) Storage / Compute Considerations

* One `UserBet` PDA per bettor per match.
* Claims, refunds, and close_losing_bet are all O(1).
* No iteration over bettors on-chain anywhere.
* `match_counter` is strictly monotonic (u64; never decremented). Each `match_pda` is unique forever — safe for UserBet seeds even after match account closure.

---

# 6) Security Checks — Must Not Skip

For every token transfer from vault:
* Derive `vault_auth = PDA("vault_auth", match.key())`
* Assert derived == `match.vault_authority` (stored)
* Assert `vault_ata.key() == match.vault_ata` (stored)
* Use `vault_auth` PDA seeds as CPI signer for all `vault_ata` transfers
* Verify `treasury_ata` equals `get_associated_token_address(config.treasury_wallet, config.skr_mint)`
* Verify `user_skr_ata.owner == signer` in `place_bet` and `claim`/`refund_bet`
* Verify all token account mints == `config.skr_mint`

For match state machine:
* `place_bet` requires `OPEN` status
* Only `lock_match` can transition `OPEN → LOCKED`
* Only `resolve_match` can transition `LOCKED → RESOLVED`
* Only `cancel_match` can transition `OPEN/LOCKED → CANCELLED`
* `create_match` can be spammed unbounded to open as many concurrent matches as desired

For external deposits:
* Anyone can transfer SKR into `vault_ata` externally. The contract ignores external deposits in all math (only `total_a + total_b` tracked values are used). Extra tokens accumulate in the vault and are swept to treasury as dust at auto-close. This is expected and harmless behavior.

For pausing:
* `place_bet` checks `!config.paused` first

---

# 7) Account Space Summary

| Account | Exact Bytes | Allocate |
|---|---|---|
| `Config` | 132 + 1 (paused) + padding | **168** |
| `Match` | 244 | **264** |
| `UserBet` | 81 + padding | **96** |

---

# 8) Anchor Account Annotations Reference

### Config
```rust
#[account(
  init,
  seeds = [b"config"],
  bump,
  payer = admin,
  space = 168
)]
```

### Match
```rust
#[account(
  init,
  seeds = [b"match", &match_id.to_le_bytes()],
  bump,
  payer = admin,
  space = 264
)]
```

### Vault Authority (no account; just PDA)
```rust
// Derived inline wherever needed:
let (vault_auth, bump) = Pubkey::find_program_address(
    &[b"vault_auth", match_account.key().as_ref()],
    program_id,
);
assert_eq!(vault_auth, match_account.vault_authority);
```

### Vault ATA
```rust
#[account(
  associated_token::authority = vault_auth,
  associated_token::mint = skr_mint,
)]
```
Also assert: `vault_ata.key() == match_account.vault_ata`

### UserBet (init)
```rust
#[account(
  init,
  seeds = [b"bet", match_account.key().as_ref(), user.key().as_ref()],
  bump,
  payer = user,
  space = 96
)]
```

### UserBet (claim / refund_bet)
```rust
#[account(mut, close = user)]
```

### UserBet (close_losing_bet)
```rust
#[account(mut, close = admin)]
```
For `close_losing_bet`, the Solana rent from the `UserBet` account is sent to the `config.admin` wallet.

### Closing Vault ATA
SPL token `CloseAccount` instruction with `vault_auth` PDA as authority/signer.

### Closing Match
```rust
#[account(mut, close = admin)]
```

---

# 9) Error Codes

```
Unauthorized             — signer mismatch or account ownership mismatch
SystemPaused             — place_bet called while paused
MatchNotOpen             — match.status != OPEN
MatchNotLocked           — match.status != LOCKED
MatchNotResolved         — match.status != RESOLVED
MatchNotCancelled        — match.status != CANCELLED
MatchNotCancellable      — cancel_match called on RESOLVED or already CANCELLED match
InvalidSide              — side not in {A, B}
BetOutOfRange            — amount < min_bet or amount > max_bet
AlreadyBet               — UserBet PDA already exists (Anchor init collision)
NotWinner                — bet.side != match.winner (claim)
NotLoser                 — bet.side == match.winner (close_losing_bet)
WinningTotalZero         — guard in claim; should be unreachable in normal flow
TokenMintMismatch        — token account mint != config.skr_mint
VaultAuthorityMismatch   — vault_ata != match.vault_ata or derived vault_auth mismatch
FeeBpsOutOfRange         — fee_bps > 1_000 in update_config
InvalidBetRange          — min_bet > max_bet in update_config
```

---

# 10) Operational Lifecycle

## Concurrent Live Matches

The platform supports an **entirely unbounded concurrent pipeline**. The Admin can create 1, 10, or 100 matches at once. Users can bet on any match as long as it is OPEN.

```
Time →

[Match N]     create → OPEN (betting) → ... ... ... → lock → resolve
[Match N+1]   create → OPEN (betting) → lock → resolve
[Match N+2]                                        create → OPEN (betting)
[Match N+3]                     create → OPEN (betting) → ... ... → cancel
```

**Admin controls (fully flexible):**
* `create_match` opens a new market on-chain instantly. Admin can spam this to open a whole season of matches in advance.
* `lock_match(match_id)` targets a specific match and freezes betting for it.
* `resolve_match(match_id, winner)` processes the payout for a locked match.
* `cancel_match(match_id)` kills a broken match at any time.

Users merely pass the `match_pubkey` they want to bet on to `place_bet`. The contract only checks `status == OPEN`.

---

## User Flows

### Winner
1. Call `place_bet(side, amount)` on the current OPEN match (one bet per wallet)
2. Wait for match to be resolved
3. Call `claim` → receive SKR payout; UserBet account closes (rent returned)
   - Last winner to claim triggers auto-close of vault + match (dust swept to treasury)

### Loser
* UserBet account sits on-chain after RESOLVED
* Call `close_losing_bet` (or anyone can call it) to recover UserBet rent
* No SKR recovered — losing bets fund the payout pool

### Refund (cancelled match)
1. Admin calls `cancel_match` → status: CANCELLED
2. Each bettor calls `refund_bet` → full bet amount returned; UserBet closes
   - Last refund triggers auto-close of vault + match

---

## One-Time Setup (first deployment)
1. `init_config(skr_mint, treasury_ata, min_bet, max_bet)`
2. Optionally: `update_config(...)` to tune params before going live
3. Begin match pipeline with `create_match`

---

# 11) Changelog

## v1 → v2

| Area | v1 | v2 |
|---|---|---|
| Match ID counter | Ambiguous pre/post-increment | **Pinned:** `match.id = match_counter` then `match_counter++`; first match id=0 |
| Lock enforcement | total_a/b > 0 "recommended" | **Removed.** Zero bets on either side is valid. |
| winning_total = 0 | Rejected with error | **Handled gracefully:** auto-close with treasury sweep in `resolve_match` |
| pool = 0 | Undefined | **Handled:** immediate vault+match close, no transfers |
| Model labels | Optional `[u8; 32]` fields | **Removed.** Frontend resolves from hash. |
| `claimed` field on UserBet | Present (contradictory) | **Removed.** Account existence is the flag. |
| close_losing_bet | Optional appendix mention | **Required instruction, permissionless** |
| Admin key rotation | No instruction | **`update_config`** covers admin + all config fields |
| min_bet / max_bet update | Immutable | **Updatable via `update_config`** |
| treasury_ata update | Immutable | **Updatable via `update_config`** |
| fee_bps | Hardcoded 500, immutable | **Updatable via `update_config`** (max 1_000 bps) |
| Cancel mechanism | None | **`cancel_match` + `refund_bet`** instructions added |
| Pause mechanism | None | **`set_paused` + `config.paused` flag** added |
| CLOSED status (enum) | Present (contradictory) | **Removed.** Physical close only. |
| CANCELLED status | None | **Added (status = 3)** |
| Rent receivers | Undecided | **Pinned:** UserBet → bettor; vault + match → admin |
| vault_auth/vault_ata trust | Store only | **Store + always derive and verify** |
| treasury_ata verification | Mint only | **Key + mint both checked in every instruction** |
| user_skr_ata ownership | Not explicitly checked | **Explicit: owner == signer** |
| vault_ata key check | Not explicit | **Explicit: vault_ata.key() == match.vault_ata** |
| PDA signer for fee transfer | Not mentioned in resolve_match | **Explicitly required in §2.7** |
| match_counter byte order | "le_bytes" (implicit) | **Explicit: `match_id.to_le_bytes()`** |
| Space calculations | Placeholder `...` | **Exact byte counts provided** |
| Status/winner types | u8 raw ints | **Named Anchor enums (MatchStatus, WinnerSide)** |
| Space for paused field | N/A | **Included in Config space** |

## v2 → v3

| Area | v2 | v3 |
|---|---|---|
| `locked_at` / `resolved_at` sentinel | `0` (collides with Unix epoch) | **`i64::MIN`** — unambiguous "not set" value |
| `created_at` init in create_match | `0` listed alongside others | Correctly initialized to `Clock::unix_timestamp` (non-sentinel) |
| `treasury_ata` validation at init | Mint check + "valid SPL token account" | **ATA derivation check** — must be canonical ATA of treasury owner for `skr_mint` |
| Async match pipeline | Documented as "valid and normal" | **Mandatory design** — admin must create next match immediately after lock |
| `create_match` blocking rules | Unclear | **Explicit:** only blocked by existing OPEN match; LOCKED/RESOLVED/CANCELLED never block |
| External deposits to vault | Mentioned in vault account section | **Explicit rule in §6 Security** — ignored in math, swept as dust |
| pool=0 branch in resolve_match | Handled (close vault+match) | Confirmed: no transfers, vault is empty, just close for rent |
| winning_total=0 branch | fee + payout_pool swept to treasury | Confirmed: full `fee + payout_pool` to treasury |
| Section 10 Lifecycle | "Recommended" ordered steps | **Rewritten as async pipeline diagram** with mandatory sequencing rules |

## v3 → v4 (Concurrent Model Upgrade)

| Area | v3 | v4 |
|---|---|---|
| Open Matches | Limited to 1 at a time (`current_open_match` gating) | **Unbounded.** Admin can create 100 matches at once, users can bet on any. |
| `Config` Account | Stored `current_open_match: Pubkey` (200 bytes) | **Removed.** Match state is autonomous. Reduced to 168 bytes. |
| Match validation | `place_bet` enforced match equals `current_open_match` | **Removed.** Just checks `status == OPEN`. |
| Admin restrictions | `create_match` blocked if open exists | **Removed.** Admin creates arbitrarily. |
| Error Codes | Removed `OpenMatchExists` & `NoOpenMatch` | **Deleted.** No longer possible. |
