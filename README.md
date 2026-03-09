<div align="center">

# ⚔️ Immortal Kombat

**LLMs fight. Humans bet. On-chain, on Solana.**

[![Platform](https://img.shields.io/badge/Platform-Android-green?logo=android)](../../releases)
[![Solana](https://img.shields.io/badge/Blockchain-Solana-purple?logo=solana)](https://solana.com)
[![Flutter](https://img.shields.io/badge/App-Flutter-blue?logo=flutter)](streaming/flutter_app)
[![Python](https://img.shields.io/badge/Backend-Python%203.11-yellow?logo=python)](backend)
[![License](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

> Autonomous AI fighters powered by large language models battle in real-time inside an N64 emulator.  
> Spectators watch the live fight, read fighter stats, and place on-chain bets — all from a mobile app.

</div>

---

## 📱 Download

| APK | Recommended For |
|-----|----------------|
| [`app-arm64-v8a-release.apk`](../../releases/latest) | Most modern Android phones ✅ |
| [`app-armeabi-v7a-release.apk`](../../releases/latest) | Older 32-bit Android |
| [`app-x86_64-release.apk`](../../releases/latest) | Android emulators |

**Install:** Enable *"Install from unknown sources"* in Android settings → tap the APK.

---

## 🎮 What Is This?

Immortal Kombat is a **fully autonomous fighting game** where:

1. **Two AI fighters** (each powered by a different LLM strategy) enter the ring
2. **Agents act in real-time** — reading RAM from a headless N64 emulator at 30Hz, making controller decisions 30 times per second
3. **A live WebRTC stream** (video + audio) is broadcast to all spectators via the mobile app
4. **Spectators place bets** on-chain using Solana before the fight starts
5. **Smart contract auto-settles** — winners receive payouts, no middleman

Every fight is unique. The LLM coach tunes the agent's aggression, defense style, and reward priorities between rounds.

---

## 🏗 Architecture

```
┌────────────────────────────────────────────────────────┐
│                     Flutter Mobile App                      │
│  Live WebRTC Stream (LiveKit) ● WebSocket state ● Wallet   │
└────────────────┬─────────────────────────┬─────────────┘
                 │ WebSocket               │ REST API
                 ▼                         ▼
┌────────────────────────────────────────────────────────┐
│               FastAPI Backend (Python)                  │
│  Match orchestration ● Bet management ● Redis cache     │
└────────────────┬───────────────────────────────────────┘
                 │ Unix socket
                 ▼
┌────────────────────────────────────────────────────────┐
│                    Match Runner                         │
│                                                        │
│  mupen64plus (N64 emulator, headless on Xvfb)          │
│      │                                                 │
│      ├── RAM reader → 14-float observation @ 30Hz      │
│      ├── RL Agent (MLP/LSTM/GRU/Transformer)           │
│      │       └── LLM Micro Coach (async, ~0.3Hz)       │
│      │               advises: attack / advance / defend │
│      ├── LLM Episode Coach (every N episodes)          │
│      │       tunes reward weights between rounds       │
│      └── FFmpeg capture → live mobile stream           │
└────────────────┬───────────────────────────────────────┘
                 │ Anchor CPI
                 ▼
┌────────────────────────────────────────────────────────┐
│         Solana Program (Anchor / Rust)                  │
│  Parimutuel betting ● Auto-settlement ● Fee sponsorship │
└────────────────────────────────────────────────────────┘
```

---

## ✨ Key Features

| Feature | Details |
|---------|---------|
| 🤖 **LLM-driven agents** | Each fighter's strategy is tuned by an LLM coach between rounds |
| 📡 **Live streaming** | Flutter subscribes to the live fight over LiveKit/WebRTC while WebSocket carries game-state updates |
| 🎯 **Two-tier AI coaching** | *Episode Coach* retunes reward weights; *Micro Coach* gives real-time tactical hints |
| 🔗 **On-chain bets** | Solana parimutuel smart contract — fully trustless, auto-settled |
| 👛 **Privy wallet auth** | Email, OAuth, passkey, and SIWS (Sign In With Solana) |
| 📊 **Real-time overlay** | Health, timer, round dots, viewer count, fighter names — all WebSocket-driven |
| ⚡ **Low-latency playback** | The mobile client is tuned for fast LiveKit/WebRTC startup and real-time fight viewing |

---

## 🤖 LLM Coaching System

The agent doesn't just run pure RL. It has a **two-tier LLM coach** that runs asynchronously without blocking the 30Hz game loop:

```
Episode Coach  (every 10 episodes)
  • Reviews: win rate, damage dealt/taken, action mix, spam penalties
  • Outputs: JSON reward weight patch → applied to RewardConfig live
  • Can tune: aggression, idle_penalty, anti_air_bonus, punish_bonus, etc.

Micro Coach  (every ~3 seconds, daemon thread)
  • Reads: current health, distance, timer, airborne/attack flags
  • Outputs: [attack_weight, advance_weight, defend_weight] → 4 obs dims
  • Agent learns when to follow or ignore the hint over training
```

Works with **OpenAI**, **Anthropic (Claude)**, and **Google Gemini**.

---

## 🧠 Agent Architectures

Eight novel agent architectures are available, all operating on a **56-float stacked RAM observation** (14 signals × 4 frames) and choosing from **21 macro actions** (attacks, movement, specials, throws). Each is trained with REINFORCE or PPO + architecture-specific auxiliary losses.

---

### Arch 1 — MLP (Baseline)
`--agent mlp`

A simple 3-layer MLP with actor-critic heads. No memory. Used as the performance baseline. Trained with REINFORCE + entropy regularisation. Demonstrates that even a memoryless network can learn basic approach-and-attack patterns.

---

### Arch 2 — LSTM
`--agent lstm`

GRU-equipped actor-critic with 128-dim hidden state. Carries temporal context across the 33ms timestep, enabling it to respond to multi-frame sequences like opponent combo chains. First architecture to reliably learn anti-jump punishes.

---

### Arch 3 — GRU Reactive Baseline
`--agent gru`

```
Obs(56) → Linear(64) → GRUCell(128) → Policy/Value heads
```

Like LSTM but with a GRUCell for faster convergence. Key innovation: **separate Adam optimisers for policy and value** to prevent gradient interference. Trained with REINFORCE + advantage normalisation. This is the speed-optimised recurrent baseline.

---

### Arch 4 — Continuous RSSM + Hierarchical AC
`--agent cont_rssm`

Inspired by DreamerV2's Recurrent State Space Model (RSSM) but adapted for real-time game AI without world-model rollouts.

```
Encoder → Deterministic GRU (128) ─┬─ Prior μ,σ (Gaussian z, 32-dim)
                                   └─ Posterior μ,σ (from obs+det)
                                         │
                                    z sampled via reparameterisation
                                         │
                                  latent = concat(det, z)
                                         │
                          Manager (fires every 10 steps) → goal (32-dim)
                                         │
                          Worker → Policy(latent + goal) / Value
```

**Key innovations:**
- **Latent world model**: Maintains a continuous probabilistic belief state `z` over game dynamics, even between raw RAM reads
- **KL divergence auxiliary loss**: Minimizes gap between prior and posterior, forcing the deterministic hidden state to predict the future
- **Hierarchical AC**: Manager sets abstract sub-goals every 10 steps; Worker executes micro-actions every 33ms

---

### Arch 5 — Discrete RSSM + Hierarchical AC
`--agent disc_rssm`

Same structure as Arch 4 but the latent `z` is **categorical** (8 categories × 8 classes = 64-dim discrete code) with **Gumbel-Softmax** straight-through estimation.

```
Posterior → 8 × Categorical(8 classes) → Gumbel-Softmax (τ=1.0, hard=True) → z (64-dim)
```

**Key innovations:**
- **Discrete latent**: Categorical bottleneck forces the model to commit to interpretable discrete game situations rather than fuzzy continuous codes
- **KL divergence via categorical cross-entropy** between posterior and uniform prior
- **PPO learner**: Upgraded from REINFORCE to PPO with GAE advantages and epoch replay for more stable training on longer episodes

---

### Arch 6 — Causal Transformer World Model + Hierarchical AC
`--agent transformer`

```
Obs history (16 frames × 56 floats)
     │
Positional embedding + Linear projection (56→64)
     │
2× Causal Transformer blocks (d_model=64, heads=4)
     │    └── Causal mask ensures attention only looks backward in time
Last token → Manager(every 10 steps) → goal(32-dim)
     │
Worker Policy/Value(context + goal)
```

**Key innovations:**
- **Causal self-attention over 16-frame window**: Unlike LSTM/GRU, the Transformer can directly attend to any frame in the context window, enabling long-range pattern recognition (e.g., recognising opponent's 10-step combo setup from the start)
- **Learnable positional embeddings** (not sinusoidal) tuned for the 33ms step cadence
- **No recurrent state between steps** — context window acts as implicit memory, making gradient flow clean and training parallelisable
- Trained with **PPO + GAE**

---

### Arch 7 — Object-Centric + Opponent Belief (Flagship)
`--agent obj_belief`

The most novel architecture. Treats each of the 14 RAM features as an **"object slot"** and runs self-attention across slots rather than across time.

```
Obs(56) → reshape → (14 slots × 4 time-steps each)
                         │
              Linear(4→16) per slot → embeddings (14 × 16)
                         │
              Multi-head Self-Attention (2 heads) across 14 slots
                         │
              LayerNorm + residual → context (14×16 = 224-dim)
                         │
              ┌──────────┴──────────┐
              │                     │
        Policy head           Belief head
        (21 actions)      (Binary BCE: did CPU attack?)
```

**Key innovations:**
- **Object-centric attention**: Instead of processing the observation as a flat vector, each semantic variable (P1 health, P2 position, distance, airborne flag, etc.) gets its own learnable slot embedding. Attention learns *which features matter most for each action*.
- **Opponent belief auxiliary loss**: A dedicated head predicts whether the CPU attacked this step (supervised with RAM data). This forces the internal representation to track opponent state explicitly, even without an opponent model.
- **Auxiliary loss co-training**: BCE belief loss (weight 0.1) regularises the shared representation without dominating the policy gradient
- Trained with **PPO + GAE**

---

### Arch 8 — Latent Planner (MPC + Cross-Entropy Method)
`--agent latent_planner`

The only architecture with **explicit lookahead planning**. Uses a neural world model to simulate future states and plans with CEM (Cross-Entropy Method) rather than pure gradient-based policy learning.

```
Current Obs → Encoder → z (128-dim latent)
                              │
         ┌────────────────────┘
         │   CEM Planning (H=3 steps ahead, 32 candidates, 3 iterations):
         │   1. Sample 32 action sequences from current belief
         │   2. Roll out each sequence through the world model:
         │        z_t+1 = Transition(z_t, action_embedding)
         │        r_hat = RewardHead(z_t+1)
         │   3. Select top-8 candidates by predicted cumulative reward
         │   4. Refit distribution; take mode action for step t
         │
Policy head (prior — used when CEM too slow)
Value head  (critic)
World model trained with reward prediction loss (MSE)
```

**Key innovations:**
- **Learned world model for planning**: The transition network `z_{t+1} = f(z_t, a)` learns the game's dynamics from experience, enabling imagination-based lookahead without the emulator
- **CEM planning**: 32 rollouts × 3 CEM iterations × 3-step horizon = evaluating 288 imagined futures per decision, selecting the best action
- **Three non-overlapping optimiser groups**: Policy head, value head, and world model have completely separate Adam optimisers to prevent destructive interference
- Falls back to policy-head prior when latency budget requires sub-33ms decisions

---

### Observation Space (shared across all architectures)

| Index | Signal | Source | Range |
|-------|--------|--------|-------|
| 0 | P1 health | RAM | [0, 1] |
| 1 | P2 health | RAM | [0, 1] |
| 2 | Timer | RAM | [0, 1] |
| 3 | P1 X position | RAM | [-1, 1] |
| 4 | P2 X position | RAM | [-1, 1] |
| 5 | Distance | derived | [0, 1] |
| 6 | Facing sign | derived | {-1, +1} |
| 7 | P1 action state | RAM verified | {0, 1} |
| 8 | P2 action state | RAM verified | {0, 1} |
| 9 | P1 Y velocity | RAM verified | [-1, 1] |
| 10 | P2 airborne | RAM verified | {0, 1} |
| 11 | P1 hitstun | RAM verified | {0, 1} |
| 12 | P2 hitstun | RAM verified | {0, 1} |
| 13 | P1 airborne | RAM verified | {0, 1} |
| 14–17 | LLM coach hint | async LLM | [0, 1] × 4 |

Stacked across 4 frames → **56 floats** base (or **72 floats** with LLM coaching).

---

## 🚀 Running Locally

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
uvicorn app.main:app --reload
```

### Flutter App
```bash
cd streaming/flutter_app
flutter pub get
flutter run                  # connects to backend over WebSocket + REST
```

### Training (RL + LLM Coach)

Training requires a physical MK4 ROM and mupen64plus built locally. There are three steps:

#### Step 1 — Dependencies

```bash
# macOS
brew install mupen64plus ffmpeg python@3.11

# Build the custom input plugin (reads controller state from mmap)
cd vendor/n64train-input && make

# Install Python training deps
cd training && pip install -r requirements.txt
```

#### Step 2 — Generate Savestates (GUI Tool)

Savestates capture a specific fight setup (characters, stage, health) as the starting point for every training episode. Use the debug GUI to generate them:

```bash
python3 training/tools/mk4_controller_debug.py
```

The GUI has four tabs:

| Tab | Purpose |
|-----|---------|
| **🎮 Controllers** | Live keyboard-driven N64 controller for P1 (Arrow keys / Z=A / X=B) and P2 (WASD / Q=A / E=B). Navigate the game's character select and reach a fight. |
| **💾 Savestates** | Once you reach the moment you want training to start (e.g., both fighters at full health, round 1), type a name and click **Save State**. States are saved as `.st` files to `training/data/savestates/mk4_arcade/`. Load/rename/delete from the same panel. |
| **🖥️ Emulators** | Launch and kill multiple headless mupen64plus instances. Each emulator connects via a Unix socket bridge. Add more instances for parallel training. |
| **📋 Input Log** | Live log of every `GetKeys()` call the emulator makes — verify controller inputs are being read correctly. |

**Typical savestate workflow:**
1. Click **🚀 Launch P1 Emu** in the Controllers tab
2. Use keyboard to navigate: character select → Scorpion vs Sub-Zero → Stage select
3. When the "FIGHT!" banner appears and both HP bars are full, switch to the Savestates tab
4. Type a name like `scorpion_subzero_pit` → **💾 Save State**
5. Repeat for other character matchups — more savestates = more training variety

#### Step 3 — Run Training

```bash
cd training

# Pure RL — fastest, no API keys needed
python3 scripts/mk4_train.py --episodes 500 --agent lstm \
    --savestate scorpion_subzero_pit      # filter to one matchup

# All savestates, rotating every episode
python3 scripts/mk4_train.py --episodes 1000 --agent obj_belief

# With LLM coaching — local Ollama (FREE, recommended for training)
# ollama pull llama3.2 && ollama serve
python3 scripts/mk4_train.py --episodes 500 --agent lstm \
    --coach ollama --coach-model llama3.2 \
    --coach-every 10 --micro-interval 90 \
    --fighter-name "Scorpion" --fighter-style "aggressive"

# With LLM coaching — OpenAI (cloud)
export OPENAI_API_KEY=sk-...
python3 scripts/mk4_train.py --episodes 500 --agent lstm \
    --coach openai --coach-model gpt-4o-mini \
    --coach-every 10 --micro-interval 90

# Parallel training (multiple savestates simultaneously)
python3 scripts/mk4_train_parallel.py --workers 4 --agent lstm
```

Checkpoints are saved to `training/data/checkpoints/` every `--save-every` episodes (default: 10).  
Training logs to `training/data/logs/mk4_training_log.jsonl`.

### Requirements
- Python 3.11+
- Flutter 3.29+
- FFmpeg
- mupen64plus
- Solana CLI (contract deployment only)
- A Mortal Kombat 4 ROM (not included — source legally)

---

## 🔑 Environment Variables

See [`backend/.env.example`](backend/.env.example) for the full list.

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `PRIVY_APP_ID` | Privy app ID for wallet/auth |
| `PRIVY_APP_SECRET` | Privy server-side secret |
| `SOLANA_RPC_URL` | Solana RPC endpoint |
| `OPENAI_API_KEY` | For LLM coaching (optional) |
| `ANTHROPIC_API_KEY` | For Claude coaching (optional) |
| `GEMINI_API_KEY` | For Gemini coaching (optional) |

---
