# Attack-State Verification Report (GameShark Offsets)

Date: 2026-03-06
Savestates:
- p1p2state.st (controlled P1 input)
- arcade_training_scorpion.st (CPU-driven P2 offense correlation)

## Address map used (GameShark base + offset)
- P1 base `0x800FE000`
  - `P1_ACTION_ST   = +0x08C = 0x800FE08C`
  - `P1_ATTACK_TYPE = +0x090 = 0x800FE090`
  - `P1_HITSTUN_C   = +0x308 = 0x800FE308`
  - `P1_HITSTUN_B   = +0x30C = 0x800FE30C`
  - `P1_HITSTUN_A   = +0x310 = 0x800FE310`
- P2 base `0x80126E00`
  - `P2_ACTION_ST   = +0x0C0 = 0x80126EC0`
  - `P2_ATTACK_TYPE = +0x094 = 0x80126E94`
  - `P2_HITSTUN     = +0x19C = 0x80126F9C`

## Slow permutation matrix (p1p2state.st)
Runs:
- slowA: hold=8, release=35, repeats=4, walk=180
- slowB: hold=12, release=50, repeats=4, walk=220
- slowC: hold=16, release=70, repeats=3, walk=260

Across all three runs:
- `P1_ACTION_ST (0x800FE08C)`
  - baseline `0x00000000`
  - during attacks repeatedly `0x00000002`
  - verdict: **good dynamic attack/activity signal**
- `P1_ATTACK_TYPE (0x800FE090)`
  - baseline `0x00000000`
  - changes to multiple non-zero values over action sequences
  - verdict: **good attack-type class signal**
- `P1_HITSTUN_C (0x800FE308)`
  - baseline non-zero drift possible (`0xFFFFF856` observed)
  - still changes strongly during attack windows and transitions
  - verdict: **usable as dynamic signal, but noisy baseline**
- `P1_HITSTUN_A/B (0x800FE310/0x800FE30C)`
  - high intra-sequence movement, but summary end-state often returns 0
  - verdict: **window-sensitive; usable only if frame-level sampled**
- `P2_HITSTUN (0x80126F9C)`
  - constant `0x00000002` in every slow run
  - verdict: **bad for attack detection (effectively constant)**
- `P2_ACTION_ST (0x80126EC0)`
  - toggles among `0`, `0x3FA90`, `0x3FAB1`
  - verdict: **dynamic, but not fully isolated to true attack frames**
- `P2_ATTACK_TYPE (0x80126E94)`
  - changes from 0 to stable non-zero signed values during combat windows
  - verdict: **promising dynamic signal**

Health check in all slow runs:
- P2 health consistently dropped from full (`0x10000`) after P1 action sequences.

## CPU-offense correlation (arcade_training_scorpion.st)
File: `verify_p2_cpu_correlation_20260306_034301.json`
Scenarios: neutral, stand_block, crouch_block (1200 frames each)

Observed:
- P1 damage events occurred in all scenarios (2–3 events each).
- Around these events:
  - `P2_HITSTUN (0x80126F9C)` stayed constant at `2` always.
  - `P2_ACTION_ST (0x80126EC0)` varied among `{0, 260752, 260785}`.
  - `P2_ATTACK_TYPE (0x80126E94)` varied among `{0, 4294887867, 4294826919}` depending on scenario.

Verdict:
- `P2_HITSTUN` = **reject** for P2 attack state.
- `P2_ACTION_ST` + `P2_ATTACK_TYPE` = **keep as candidate P2 attack/activity pair**.

## Confidence classification
- High confidence (good):
  - `0x800FE08C` (P1 action)
  - `0x800FE090` (P1 attack type)
- Medium confidence (usable with caution):
  - `0x800FE308` (P1 hit-window dynamic)
  - `0x80126EC0` (P2 activity/anim state)
  - `0x80126E94` (P2 attack type)
- Low confidence / reject:
  - `0x80126F9C` (P2 hitstun constant)

## Raw logs
- `verify_gameshark_offsets_20260306_034016.txt`
- `verify_gameshark_slowA_20260306_034202.txt`
- `verify_gameshark_slowB_20260306_034133.txt`
- `verify_gameshark_slowC_20260306_034147.txt`
- `verify_p2_cpu_correlation_20260306_034301.json`
