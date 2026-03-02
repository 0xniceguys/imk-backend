# MK4 Training Plan (Implemented Scaffold Status)

## Current implemented foundation

The repo now includes scaffolding for:
- speed modes (`DEBUG_VISIBLE`, `TRAIN_TURBO`, `EVAL_DETERMINISTIC`)
- per-instance Mupen64Plus directories for concurrent headed runs
- scenario manifest bank (savestate-first training metadata)
- typed feature registry with privileged/deploy feature separation
- experiment frame-budget accounting (all env frames count)
- fixed 6-family architecture suite registry
- launcher for concurrent headed architecture runs

## Goal (next implementation phases)

Create a deterministic loop:
1. Start emulator
2. Load savestate
3. Apply action sequence
4. Capture observation (initially frames, later RAM)
5. Repeat

## Why low-level first

- We need a stable environment interface before any ML stack matters.
- Savestates and action timing are the foundation for reproducible experiments.
- If the environment is noisy, training results are meaningless.

## Phase order

1. Process control + savestate management
2. Action encoding + injection timing
3. Observation capture contract
4. Reward/task definitions
5. Baseline scripted agent
6. RL training loop

## Headless strategy (later)

- Local macOS: windowed mode for development
- Linux EC2 CPU prototype: `xvfb-run` + Mupen64Plus
- Linux EC2 GPU: native accelerated render path for lower latency frame capture

## Portability assumption

Your assumption is mostly right:
- The **training logic/source code** can stay the same.
- The **build + launcher + plugin paths + display backend** will differ between macOS and Linux.

This is why launcher/process details are isolated behind a runtime wrapper and instance-specific env vars.

## Architecture suite (current registry)

- Continuous RSSM + Hierarchical Actor-Critic
- Discrete RSSM + Hierarchical Actor-Critic
- Transformer World Model + Hierarchical Actor-Critic
- Object-Centric + Opponent-Belief + Hierarchical WM (flagship)
- Latent Planner (MPC/CEM) + Policy Prior
- CNN+RNN Reactive Control Baseline
