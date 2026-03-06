# MK4 Isolated Action Sweep Report

Generated from isolated one-action runs (no shared emulator instance).
Each run used `mk4_probe_*` with `--boot --test addr_scan` and wrote artifacts
to its own run directory under `training/data/reverse/probe_runs/`.

## Health mapping used (verified)
- `P1_HP = 0x800FE0D8`
- `P2_HP = 0x80126F54`

## P1 actions (scan range `0x800FE080-0x800FE340`)

### `lp` (`sweep_lp_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_lp_addr`
- `0x800FE08C: 0x00000000 -> 0x00000002`
- `0x800FE118: 0x0003F9E2 -> 0x0003F9E3`
- `0x800FE228: 0x00008A40 -> 0x00014A06`
- `0x800FE25C: 0x0003F9E2 -> 0x0003F9E3`
- `0x800FE2A8: 0x00000000 -> 0x000003E6`
- `0x800FE308: 0xFFFFFFF5 -> 0xFFFFFDF0`
- `0x800FE30C: 0x00000000 -> 0x000000D6`
- `0x800FE310: 0x00000000 -> 0x00000152`

### `hp` (`sweep_hp_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_hp_addr`
- `0x800FE08C: 0x00000000 -> 0x00000003`
- `0x800FE090: 0x00000000 -> 0x00044873`
- `0x800FE118: 0x0003F9E2 -> 0x0003F9E3`
- `0x800FE174: 0x00000000 -> 0x00044873`
- `0x800FE1AC: 0x00000000 -> 0x00051C1B`
- `0x800FE254: 0x00000000 -> 0x00000BC0`
- `0x800FE25C: 0x0003F9E2 -> 0x0003F9E3`
- `0x800FE280: 0x0003FA90 -> 0x0003FD45`
- `0x800FE28C: 0x00000005 -> 0x00000007`
- `0x800FE320: 0x00000000 -> 0x000511E7`
- `P2_HP: 0x00010000 -> 0x0000F5C3`

### `lk` (`sweep_lk_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_lk_addr`
- `0x800FE118: 0x0003F9E2 -> 0x0003F9E4`
- `0x800FE228: 0x00008A40 -> 0x00011AD9`
- `0x800FE25C: 0x0003F9E2 -> 0x0003F9E4`
- `0x800FE2A8: 0x00000000 -> 0x000006B6`
- `0x800FE308: 0x00000013 -> 0x0000018B`
- `0x800FE30C: 0x00000000 -> 0xFFFFFCD7`
- `0x800FE310: 0x00000000 -> 0x00000151`

### `hk` (`sweep_hk_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_hk_addr`
- `0x800FE090: 0x00000000 -> 0x000457CF`
- `0x800FE118: 0x0003F9E2 -> 0x0003F9E3`
- `0x800FE138: 0x00047097 -> 0x0004879E`
- `0x800FE174: 0x00000000 -> 0x000457CF`
- `0x800FE1AC: 0x00000000 -> 0x000516FC`
- `0x800FE228: 0x00008A40 -> 0x0000B84E`
- `0x800FE25C: 0x0003F9E2 -> 0x0003F9E3`
- `0x800FE308: 0xFFFFFFF5 -> 0xFFFFFFA4`
- `0x800FE320: 0x00000000 -> 0x00052143`
- `P2_HP: 0x00010000 -> 0x0000E667`

## P2 actions (scan range `0x80126E80-0x80126FA0`)

### `p2_lp` (`sweep_p2_lp_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_p2_lp_addr`
- `0x80126E80: 0x00000000 -> 0x00000032`
- `0x80126EC0: 0x0003FAB1 -> 0x00000000`
- `0x80126EC4: 0x0003FA90 -> 0x00000000`
- `0x80126ECC: 0x00000002 -> 0x00000000`
- `0x80126ED0: 0x00000016 -> 0x00000017`
- `0x80126F30: 0x00000B93 -> 0x00000BA3`

### `p2_hp` (`sweep_p2_hp_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_p2_hp_addr`
- `0x80126E94: 0x00000000 -> 0xFFFE4C8C`
- `0x80126EC0: 0x0003FAB1 -> 0x00000000`
- `0x80126EC4: 0x0003FA90 -> 0x00000000`
- `0x80126ECC: 0x00000002 -> 0x00000000`
- `0x80126ED0: 0x00000016 -> 0x00000018`
- `0x80126F30: 0x00000B93 -> 0x00000BA3`
- `P1_HP: 0x00010000 -> 0x0000F5C3`

### `p2_lk` (`sweep_p2_lk_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_p2_lk_addr`
- `0x80126E80: 0x00000000 -> 0x00000002`
- `0x80126EC0: 0x0003FAB1 -> 0x00000000`
- `0x80126EC4: 0x0003FA90 -> 0x00000000`
- `0x80126ECC: 0x00000002 -> 0x00000000`
- `0x80126ED0: 0x00000016 -> 0x00000017`
- `0x80126F30: 0x00000B94 -> 0x00000BA3`
- `0x80126F34: 0x00000000 -> 0x00000B93`

### `p2_hk` (`sweep_p2_hk_addr`)
Run dir: `training/data/reverse/probe_runs/sweep_p2_hk_addr`
- `0x80126E94: 0x00000000 -> 0xFFFEB2B1`
- `0x80126EC0: 0x0003FAB1 -> 0x00000000`
- `0x80126EC4: 0x0003FA90 -> 0x00000000`
- `0x80126ECC: 0x00000002 -> 0x00000000`
- `0x80126ED0: 0x00000016 -> 0x00000019`
- `0x80126F30: 0x00000B93 -> 0x00000BA3`
- `P1_HP: 0x00010000 -> 0x0000E667`

## Notes
- No training or probe emulator processes were left running after the sweep.
- The per-action wrapper scripts make one-action capture reproducible:
  - `mk4_probe_lp.py`, `mk4_probe_hp.py`, `mk4_probe_lk.py`, `mk4_probe_hk.py`
  - `mk4_probe_p2_lp.py`, `mk4_probe_p2_hp.py`, `mk4_probe_p2_lk.py`, `mk4_probe_p2_hk.py`
