# Training (research-grade scaffold, savestate-first)

This folder is the research/training workspace for Mortal Kombat 4 on N64.

Current priority:
- deterministic emulator boot/reset and per-run instance isolation
- savestate-driven scenario training workflows
- typed multimodal data contracts (frames + privileged traced state + events)
- experiment fairness bookkeeping (equal env frames, all frames count)
- architecture suite orchestration (6 concurrent headed runs)

Not yet implemented:
- reverse-engineered menu/character-select automation
- in-match RAM tracing (positions/health/timer/facing)
- emulator bridge/frame stepping
- world-model training loops
- self-play league trainer

## Runtime status (important)

Current local implementation supports:
- `headed` windowed emulator launches (including isolated per-run instances)
- `TRAIN_TURBO` via `--nospeedlimit`
- tiny-window low-resolution runs for concurrent experiments
- real local Unix-socket bridge transport (`run_bridge_server.py` + `bridge_smoke_client.py`)
- bridge-backed `reset/step/observation/RAM-export` command surface (placeholder data providers until Mupen patch)
- reward/event extraction hooks wired into the bridge step path
- reverse-engineering toolkit: task checklists, RAM range capture, snapshot diffing, bridge state polling

Still pending:
- deterministic eval frame stepping (bridge patch)
- headless Linux runtime path (EC2 phase)
- Mupen64Plus source patch for real frame stepping/input injection/RAM tracing

## Quick start (local)

List known savestates:

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/list_states.py
```

Launch the game with explicit speed mode / per-run instance:

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/boot_game.py \
  --speed-mode TRAIN_TURBO \
  --resolution 320x240 \
  --instance-id debug-a
```

Launch with the safe reverse-engineering human hotkey profile (isolated instance config only):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/boot_game.py \
  --instance-id reverse-a \
  --profile reverse_human
```

Verify an instance config matches the tracked reverse profile:

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/verify_m64p_keybindings.py \
  --profile reverse_human \
  --instance-id reverse-a
```

List the fixed architecture suite (includes explicit Transformer and CNN baseline):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/list_architectures.py
```

Preview the 6 concurrent headed launches (dry-run only):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/launch_architecture_suite.py --dry-run
```

Run the local bridge server (transport + command surface):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/run_bridge_server.py \
  --socket-path /Users/ichiropractic/code/n64/training/data/bridge/mk4.sock \
  --instance-id bridge-main
```

Smoke test the bridge client:

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/bridge_smoke_client.py \
  --socket-path /Users/ichiropractic/code/n64/training/data/bridge/mk4.sock
```

List known MK4 reverse-engineered symbols and use helper commands (persists discoveries in code, not chat):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/mk4_helper.py symbols

python3 /Users/ichiropractic/code/n64/training/scripts/mk4_helper.py \
  --socket-path /Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock \
  difficulty get

python3 /Users/ichiropractic/code/n64/training/scripts/mk4_helper.py \
  --socket-path /Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock \
  difficulty set ultimate

python3 /Users/ichiropractic/code/n64/training/scripts/mk4_helper.py \
  --socket-path /Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock \
  menu top-cursor get
```

Current confirmed symbol:
- `Options -> Difficulty` (`0x800FE758`, `u32 enum`): `0=Very Easy ... 5=Ultimate`
- `Main Menu -> Top-Level Cursor Index` (`0x8011D810`, `u8 enum`): `0=Arcade ... 5=Options` (top-level menu screen)

List reverse-engineering tasks (menu/select, difficulty, in-match core state):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/re_list_tasks.py
```

Capture and diff RAM range snapshots through the bridge (works now, meaningful after RAM export hooks land):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/re_capture_range.py \
  difficulty_low \
  --socket-path /Users/ichiropractic/code/n64/training/data/bridge/mk4.sock \
  --start 0x0 --end 0x1000 --chunk-size 0x100 --task-id difficulty_setting

python3 /Users/ichiropractic/code/n64/training/scripts/re_diff_snapshots.py \
  /path/to/before.json \
  /path/to/after.json
```

Create and list scenario manifests (savestate-first training):

```bash
python3 /Users/ichiropractic/code/n64/training/scripts/create_scenario_manifest.py \
  mk4-neutral-001 \
  /Users/ichiropractic/code/n64/.m64p/data/savestates/example.st \
  --tactical-class neutral_spacing \
  --source curated

python3 /Users/ichiropractic/code/n64/training/scripts/list_scenarios.py
```

## Layout

- `src/n64train/`: low-level runtime and training interfaces
- `configs/`: local and task configs
- `data/`: scenario manifests, logs, checkpoints, datasets
- `docs/`: training plan and assumptions
- `scripts/`: local utilities
