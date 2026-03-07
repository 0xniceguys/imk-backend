#!/usr/bin/env python3
"""
mk4_train.py — MK4 Training Loop
──────────────────────────────────────────────────────────
Runs RL training episodes against the MK4 CPU or in self-play.

Agent sees a 22-float observation per frame, stacked × 4 = 88 inputs:
  [p1_hp, p2_hp, timer, p1_x, p2_x, dist, facing,
   p1_action, p2_action, p1_y_vel, p2_airborne,
   p1_hitstun, p2_hitstun, p1_airborne,
   p1_move_lp, p1_move_hp, p1_move_lk, p1_move_hk,
   p2_move_lp, p2_move_hp, p2_move_lk, p2_move_hk]

Reward: Mk4ShapedRewardExtractor
  - health delta (asymmetric: taking damage hurts 1.5×)
  - approach/distance signals
  - win bonus / loss penalty
  - anti-air, punish, reckless-jump bonuses (RAM-verified signals)
  - survival per step, anti-spam

Usage:
    python3 training/scripts/mk4_train.py --episodes 50 --agent random
    python3 training/scripts/mk4_train.py --episodes 1000 --agent lstm
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import random
import struct
import sys
import time
from collections import deque
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

SOCK       = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
STATE_DIR  = N64_ROOT / 'training/data/savestates/mk4_arcade'
LOG_DIR    = N64_ROOT / 'training/data/logs'
TRAIN_LOG  = LOG_DIR / 'mk4_training_log.jsonl'
P1_CTRL    = '/tmp/mk4_ctrl'

# Episode tuning
MAX_EPISODE_SECS = 99      # full MK4 round timer — agent has full fight duration
STEP_SECS        = 0.1     # 6 frames per decision — amortizes debugger round-trip overhead, ~3x faster than 2-frame steps
# Faster episode turnover for parallel training: deterministic 30-frame settle.
SETTLE_SECS      = 0.5
X_NORM           = 30.0    # position normalisation ceiling (actual range ~±30)
DIST_NORM        = 30.0    # distance normalisation ceiling (actual distances reach 30+)


# ── Button mapping ────────────────────────────────────────────────────────────
# Per in-game "Configure Controller 1" screen:
#   A       = LOW PUNCH      B       = HIGH PUNCH
#   C-LEFT  = BLOCK          C-UP    = HIGH KICK
#   C-RIGHT = LOW KICK       C-DOWN  = RUN
#   Z       = BLOCK          L       = SIDE STEP IN
#   R       = SIDE STEP OUT

from n64train.runtime.actions import Button, ControllerState, MacroAction

# N64 hardware bitmask constants (plugin.c layout)
_BTN = {
    Button.D_RIGHT: 1 << 0,
    Button.D_LEFT:  1 << 1,
    Button.D_DOWN:  1 << 2,
    Button.D_UP:    1 << 3,
    Button.START:   1 << 4,
    Button.Z:       1 << 5,
    Button.B:       1 << 6,
    Button.A:       1 << 7,
    Button.C_RIGHT: 1 << 8,
    Button.C_LEFT:  1 << 9,
    Button.C_DOWN:  1 << 10,
    Button.C_UP:    1 << 11,
    Button.R:       1 << 12,
    Button.L:       1 << 13,
}

_MACRO_MAP: dict[MacroAction, ControllerState] = {
    # ── Movement ──────────────────────────────────────────────────────────
    MacroAction.NEUTRAL:       ControllerState(),
    MacroAction.ADVANCE:       ControllerState(pressed=frozenset([Button.D_RIGHT])),
    MacroAction.RETREAT:       ControllerState(pressed=frozenset([Button.D_LEFT])),
    MacroAction.CROUCH:        ControllerState(pressed=frozenset([Button.D_DOWN])),
    MacroAction.JUMP_FORWARD:  ControllerState(pressed=frozenset([Button.D_UP, Button.D_RIGHT])),
    MacroAction.JUMP_BACK:     ControllerState(pressed=frozenset([Button.D_UP, Button.D_LEFT])),
    MacroAction.JUMP_NEUTRAL:  ControllerState(pressed=frozenset([Button.D_UP])),
    MacroAction.SIDE_STEP_IN:  ControllerState(pressed=frozenset([Button.L])),
    MacroAction.SIDE_STEP_OUT: ControllerState(pressed=frozenset([Button.R])),
    MacroAction.RUN:           ControllerState(pressed=frozenset([Button.C_DOWN, Button.D_RIGHT])),
    # ── Defense ───────────────────────────────────────────────────────────
    MacroAction.STAND_BLOCK:   ControllerState(pressed=frozenset([Button.C_LEFT])),
    MacroAction.CROUCH_BLOCK:  ControllerState(pressed=frozenset([Button.D_DOWN, Button.C_LEFT])),
    # ── Attacks ───────────────────────────────────────────────────────────
    MacroAction.LOW_PUNCH:     ControllerState(pressed=frozenset([Button.A])),
    MacroAction.HIGH_PUNCH:    ControllerState(pressed=frozenset([Button.B])),
    MacroAction.LOW_KICK:      ControllerState(pressed=frozenset([Button.C_RIGHT])),
    MacroAction.HIGH_KICK:     ControllerState(pressed=frozenset([Button.C_UP])),
    # ── Combo / pressure ──────────────────────────────────────────────────
    MacroAction.JAB_COMBO:     ControllerState(pressed=frozenset([Button.A, Button.C_RIGHT])),
    MacroAction.PUNISH:        ControllerState(pressed=frozenset([Button.B, Button.A])),
    # ── Specials ──────────────────────────────────────────────────────────
    MacroAction.SPECIAL_1:     ControllerState(pressed=frozenset([Button.D_LEFT, Button.A])),
    MacroAction.SPECIAL_2:     ControllerState(pressed=frozenset([Button.D_DOWN, Button.D_LEFT, Button.A])),
    # THROW_ATTEMPT: forward throw = D-RIGHT + LOW PUNCH (distinct from SPECIAL_1 = D-LEFT+A)
    MacroAction.THROW_ATTEMPT: ControllerState(pressed=frozenset([Button.D_RIGHT, Button.A])),
}


def macro_to_ctrl_state(macro: MacroAction) -> ControllerState:
    return _MACRO_MAP.get(macro, ControllerState())


# Mirrored directional map used when the fighter is on the right side.
_MACRO_MAP_P2: dict[MacroAction, ControllerState] = {
    **_MACRO_MAP,   # inherit all non-directional actions unchanged
    # Override direction-dependent actions (P2 starts on the RIGHT side):
    MacroAction.ADVANCE:       ControllerState(pressed=frozenset([Button.D_LEFT])),
    MacroAction.RETREAT:       ControllerState(pressed=frozenset([Button.D_RIGHT])),
    MacroAction.RUN:           ControllerState(pressed=frozenset([Button.C_DOWN, Button.D_LEFT])),  # P2: forward dash = D_LEFT
    MacroAction.JUMP_FORWARD:  ControllerState(pressed=frozenset([Button.D_UP, Button.D_LEFT])),
    MacroAction.JUMP_BACK:     ControllerState(pressed=frozenset([Button.D_UP, Button.D_RIGHT])),
    MacroAction.SPECIAL_1:     ControllerState(pressed=frozenset([Button.D_RIGHT, Button.A])),
    MacroAction.SPECIAL_2:     ControllerState(pressed=frozenset([Button.D_DOWN, Button.D_RIGHT, Button.A])),
    MacroAction.THROW_ATTEMPT: ControllerState(pressed=frozenset([Button.D_LEFT, Button.A])),
}


def macro_to_ctrl_state_facing(
    macro: MacroAction,
    facing_sign: float | int | None = 1.0,
) -> ControllerState:
    """Direction-aware macro mapping.

    facing_sign:
      +1 -> opponent is to the right (default P1 orientation)
      -1 -> opponent is to the left (crossed-over/right-side orientation)
    """
    if facing_sign is not None and float(facing_sign) < 0.0:
        return _MACRO_MAP_P2.get(macro, ControllerState())
    return _MACRO_MAP.get(macro, ControllerState())


def macro_to_ctrl_state_p2(macro: MacroAction) -> ControllerState:
    """Backward-compatible helper for static right-side mapping."""
    return macro_to_ctrl_state_facing(macro, facing_sign=-1.0)


def write_ctrl(ctrl_state: ControllerState, path: str = P1_CTRL) -> None:
    mask = 0
    for btn in ctrl_state.pressed:
        mask |= _BTN.get(btn, 0)
    x = int(ctrl_state.analog_x * 80) & 0xFF
    y = int(ctrl_state.analog_y * 80) & 0xFF
    if not os.path.exists(path):
        with open(path, 'w+b') as f:
            f.write(b'\x00' * 4)
    with open(path, 'r+b') as f:
        m = mmap.mmap(f.fileno(), 4)
        m.seek(0)
        m.write(struct.pack('<Hbb', mask & 0xFFFF, x, y))
        m.flush()
        m.close()


# ── Observation builder ───────────────────────────────────────────────────────
# Observation size constants — FINAL: do not change without full checkpoint reset
RAW_OBS_DIM = 27    # single-frame; 7 core + 12 combat state + 8 move-type one-hot

def build_obs(state) -> list[float]:
    """
    27-float observation vector (FINAL — all MK4-relevant signals included):
      Position/health (7):
        [0]  p1_hp          normalised health P1  [0,1]
        [1]  p2_hp          normalised health P2  [0,1]
        [2]  timer          countdown [0,1]
        [3]  p1_x           position  [-1,1]
        [4]  p2_x           position  [-1,1]
        [5]  dist           distance  [0,1]
        [6]  facing_sign    +1 normal / -1 crossed over

      Combat state (12):
        [7]  p1_action      action state P1  {0,1}
        [8]  p2_action      action state P2  {0,1}
        [9]  p1_y_vel       Y velocity P1    [-1,1]  (neg=going up, pos=falling)
        [10] p2_airborne    airborne flag P2 {0,1}
        [11] p1_hitstun     attack active P1 {0,1}
        [12] p2_hitstun     attack active P2 {0,1}
        [13] p1_airborne    airborne flag P1 {0,1}
        [14] p1_victim      P1 being hit     {0,1}  — enables learning to block
        [15] p2_victim      P2 being hit     {0,1}  — enables combo follow-ups
        [16] p2_y_vel       Y velocity P2    [-1,1]  — enables anti-air timing
        [17] p2_committed   P2 in attack window {0,1} — enables punish timing
        [18] hp_advantage   health diff      [-1,1]  — who's winning

      Move-type one-hot (8):
        [19] p1_move_lp     P1 low punch
        [20] p1_move_hp     P1 high punch
        [21] p1_move_lk     P1 low kick
        [22] p1_move_hk     P1 high kick
        [23] p2_move_lp     P2 low punch
        [24] p2_move_hp     P2 high punch
        [25] p2_move_lk     P2 low kick
        [26] p2_move_hk     P2 high kick
    """
    ex = state.extras if hasattr(state, 'extras') and state.extras else {}

    p1_hp   = (state.p1_health or 0) / 160.0
    p2_hp   = (state.p2_health or 0) / 160.0
    timer   = (state.timer if state.timer is not None else 99) / 99.0
    p1_x    = max(-1.0, min(1.0, (state.p1_x or 0.0) / X_NORM))
    p2_x    = max(-1.0, min(1.0, (state.p2_x or 0.0) / X_NORM))
    dist    = min(1.0, abs((state.p2_x or 0.0) - (state.p1_x or 0.0)) / DIST_NORM)
    facing  = ex.get('facing_sign', 1.0 if (state.p2_x or 0.0) >= (state.p1_x or 0.0) else -1.0)
    p1_y_vel = max(-1.0, min(1.0, ex.get('p1_y_vel', 0.0)))
    p2_airborne = float(ex.get('p2_airborne', 0.0))
    p1_hitstun = float(ex.get('p1_hitstun', 0.0) > 0)
    p2_hitstun = float(ex.get('p2_hitstun', 0.0) > 0)
    p1_airborne = float(ex.get('p1_airborne', 0.0))

    # New signals
    p1_victim = float(ex.get('p1_victim_hitstun', 0.0) > 0)
    p2_victim = float(ex.get('p2_hitstun_state', 0.0) > 0)
    p2_y_vel  = max(-1.0, min(1.0, ex.get('p2_y_vel', 0.0)))
    p2_committed = float(ex.get('p2_recent_attack', 0.0) > 0)
    hp_advantage = max(-1.0, min(1.0, p1_hp - p2_hp))

    p1_move_lp = float(ex.get('p1_move_lp', 0.0) > 0)
    p1_move_hp = float(ex.get('p1_move_hp', 0.0) > 0)
    p1_move_lk = float(ex.get('p1_move_lk', 0.0) > 0)
    p1_move_hk = float(ex.get('p1_move_hk', 0.0) > 0)
    p2_move_lp = float(ex.get('p2_move_lp', 0.0) > 0)
    p2_move_hp = float(ex.get('p2_move_hp', 0.0) > 0)
    p2_move_lk = float(ex.get('p2_move_lk', 0.0) > 0)
    p2_move_hk = float(ex.get('p2_move_hk', 0.0) > 0)

    # Use decoded attack-state first, then fall back to proxy activity so
    # action slots remain live when signatures miss.
    p1_action = min(1.0, max(0.0, ex.get('p1_action', 0.0)))
    p2_action = min(1.0, max(0.0, ex.get('p2_action', 0.0)))
    p1_action = max(p1_action, p1_hitstun)
    p2_action = max(p2_action, p2_hitstun)
    if p1_action == 0.0:
        p1_action = float((abs(p1_y_vel) > 0.05) or (p1_airborne > 0.5) or (p1_hitstun > 0.5))
    if p2_action == 0.0:
        p2_action = float((p2_airborne > 0.5) or (p2_hitstun > 0.5))

    return [
        # ── 7 position/health ─────────────────────────────────────────────────
        p1_hp, p2_hp, timer, p1_x, p2_x, dist, facing,
        # ── 12 combat state ───────────────────────────────────────────────────
        p1_action,                                       # [7]
        p2_action,                                       # [8]
        p1_y_vel,                                        # [9]
        p2_airborne,                                     # [10]
        p1_hitstun,                                      # [11]
        p2_hitstun,                                      # [12]
        p1_airborne,                                     # [13]
        p1_victim,                                       # [14] P1 being hit
        p2_victim,                                       # [15] P2 being hit/stunned
        p2_y_vel,                                        # [16] P2 Y velocity
        p2_committed,                                    # [17] P2 in attack window
        hp_advantage,                                    # [18] health difference
        # ── 8 move-type one-hot ───────────────────────────────────────────────
        p1_move_lp,                                      # [19]
        p1_move_hp,                                      # [20]
        p1_move_lk,                                      # [21]
        p1_move_hk,                                      # [22]
        p2_move_lp,                                      # [23]
        p2_move_hp,                                      # [24]
        p2_move_lk,                                      # [25]
        p2_move_hk,                                      # [26]
    ]


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_agent(agent_type: str, args=None, device: str = 'cpu'):
    # Backward-compatible device resolution:
    # - explicit `device` arg wins
    # - optional args.device fallback for older callsites
    # - defaults to CPU when unspecified
    resolved_device = device
    if resolved_device == 'cpu' and args is not None:
        maybe = getattr(args, 'device', None)
        if isinstance(maybe, str) and maybe:
            resolved_device = maybe

    if agent_type == 'random':
        actions = list(MacroAction)
        return lambda obs: random.choice(actions)

    if agent_type == 'mlp':
        from n64train.experiments.mk4_agent import Mk4MlpAgent
        agent = Mk4MlpAgent(device=resolved_device)
        print(f'[agent] MLP — ep={agent.episode}')
        return agent

    if agent_type == 'lstm':
        from n64train.experiments.mk4_agent import Mk4LstmAgent
        agent = Mk4LstmAgent(device=resolved_device)
        print(f'[agent] LSTM — ep={agent.episode}')
        return agent

    # Archs 3–8 via registry
    from n64train.experiments.mk4_architectures import build_arch_agent
    agent = build_arch_agent(agent_type, device=resolved_device)
    print(f'[agent] {agent_type} — ep={agent.episode}')
    return agent



# ── Training loop ─────────────────────────────────────────────────────────────

def run_training(args) -> None:
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    from n64train.reverse.mk4_tracing import Mk4FightTraceProvider, ADDRESSES_CONFIRMED
    from n64train.runtime.rewards import Mk4ShapedRewardExtractor

    # Prefer p1p2state.st; fall back to kai_arcade_p1p2.st; error if neither present.
    savestates = [
        STATE_DIR / 'p1p2state.st',
        STATE_DIR / 'kai_arcade_p1p2.st',
    ]
    savestates = [s for s in savestates if s.exists()]
    if not savestates:
        print(f'ERROR: no savestate found in {STATE_DIR}. Expected p1p2state.st or kai_arcade_p1p2.st')
        sys.exit(1)

    if args.savestate:
        filtered = [s for s in savestates if args.savestate in s.name]
        if filtered:
            savestates = filtered

    print(f'[train] Savestates   : {len(savestates)} — {[s.stem for s in savestates]}')
    print(f'[train] Agent        : {args.agent}')
    print(f'[train] Addresses    : {"REAL" if ADDRESSES_CONFIRMED else "STUB"}')
    print(f'[train] Episodes     : {args.episodes}')
    print(f'[train] Obs dims     : {RAW_OBS_DIM}×4 frames = {RAW_OBS_DIM*4} stacked floats')
    print(f'[train] Actions      : {len(MacroAction)}')
    print(f'[train] Log          : {TRAIN_LOG}')
    print()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    agent = build_agent(args.agent)
    reward_extractor = Mk4ShapedRewardExtractor()
    save_every = getattr(args, 'save_every', 10)
    is_learning = hasattr(agent, 'learn')  # True for MLP, False for random

    total_reward = 0.0
    wins = 0
    ep_rewards: list[float] = []

    for ep_num in range(1, args.episodes + 1):
        save_path = savestates[(ep_num - 1) % len(savestates)]

        try:
            # ── Open ONE persistent bridge for the whole episode ───────────────
            b = SocketEmulatorBridge(SOCK, timeout_sec=120)
            h = Mk4BridgeHelper(b)
            tracer = Mk4FightTraceProvider(helper=h)

            # Load savestate — game starts running immediately
            b.load_savestate_path(save_path)

            # Let VS animation play through (keep connection open — no close)
            write_ctrl(ControllerState())
            time.sleep(SETTLE_SECS)

            ep_reward  = 0.0
            ep_steps   = 0
            ep_start   = time.time()
            prev_state = None

            action_history: deque[str] = deque(maxlen=20)
            frame_stack = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)  # 4×RAW_OBS_DIM stacked floats

            # Reset LSTM hidden state at episode start
            if hasattr(agent, 'reset_episode'):
                agent.reset_episode()

            # Reward term accumulators for this episode
            acc = dict(dealt=0.0, taken=0.0, approach=0.0,
                       dist_pen=0.0, survival=0.0, win=0.0, loss=0.0, spam=0.0)

            while time.time() - ep_start < MAX_EPISODE_SECS:
                # Agent decides action from previous observation
                if prev_state is not None:
                    obs = build_obs(prev_state)
                    stacked_obs = frame_stack.push(obs)  # 4×RAW_OBS_DIM stacked vector
                    action_macro = agent(stacked_obs)    # feed stacked obs to agent
                    action_history.append(action_macro.value)
                    write_ctrl(macro_to_ctrl_state(action_macro))
                else:
                    frame_stack.push([0.0] * RAW_OBS_DIM)     # warm up with RAW_OBS_DIM-wide zero obs
                    write_ctrl(ControllerState())

                time.sleep(STEP_SECS)

                # Read state — reuse the same persistent bridge connection
                try:
                    next_state = tracer.read(ep_steps)
                except Exception:
                    break

                ep_steps += 1
                done = False
                if prev_state is not None:
                    terms = reward_extractor.compute(
                        prev_state, next_state,
                        action_history=list(action_history)
                    )
                    step_reward = terms.scalar()
                    ep_reward += step_reward
                    acc['dealt']    += terms.damage_dealt
                    acc['taken']    += terms.damage_taken
                    acc['approach'] += terms.approach_reward
                    acc['dist_pen'] += terms.distance_penalty
                    acc['survival'] += terms.survival
                    acc['win']      += terms.win_bonus
                    acc['loss']     += terms.loss_penalty
                    acc['spam']     += terms.spam_penalty
                    done = tracer.is_round_over(next_state)
                    # ── Tell the MLP agent about this reward ───────────────────
                    if is_learning:
                        agent.record(step_reward)
                    if done:
                        break

                prev_state = next_state

            # Release controller + close bridge
            write_ctrl(ControllerState())
            b.close()

            ep_frames = int((time.time() - ep_start) * 60)
            won = (tracer.p1_won(prev_state) if prev_state else False)

            # ── MLP: learn from this episode ───────────────────────────────────
            ml_metrics: dict | None = None
            if is_learning:
                ml_metrics = agent.learn()
                # Save checkpoint every N episodes
                if ep_num % save_every == 0:
                    agent.save()
                    print(f'  [ckpt] saved at ep {agent.episode}')

        except Exception as exc:
            print(f'  [ep {ep_num}] ERROR: {exc}')
            write_ctrl(ControllerState())
            try: b.close()  # type: ignore[name-defined]
            except Exception: pass
            continue

        wins += int(won)
        total_reward += ep_reward
        ep_rewards.append(ep_reward)
        ep_time = time.time() - ep_start

        win_rate   = wins / ep_num * 100
        avg_reward = sum(ep_rewards[-50:]) / len(ep_rewards[-50:])

        # Console output — show reward breakdown
        print(
            f'Ep {ep_num:4d}/{args.episodes}'
            f'  frames={ep_frames:5d}'
            f'  r={ep_reward:+7.2f}'
            f'  [dealt={acc["dealt"]:+.1f}'
            f' taken={acc["taken"]:+.1f}'
            f' appr={acc["approach"]:+.1f}'
            f' surv={acc["survival"]:+.3f}'
            f' spam={acc["spam"]:+.1f}]'
            f'  won={"✓" if won else "✗"}'
            f'  win%={win_rate:5.1f}'
            f'  avg50={avg_reward:+6.2f}'
            f'  t={ep_time:.1f}s'
        )

        with open(TRAIN_LOG, 'a') as f:
            f.write(json.dumps({
                'episode':       ep_num,
                'frames':        ep_frames,
                'steps':         ep_steps,
                'reward':        round(ep_reward, 4),
                'won':           won,
                'win_rate':      round(win_rate, 2),
                'avg50':         round(avg_reward, 4),
                # ── Individual reward terms ───────────────────────────────────
                'r_dealt':       round(acc['dealt'], 3),
                'r_taken':       round(acc['taken'], 3),
                'r_approach':    round(acc['approach'], 3),
                'r_dist_pen':    round(acc['dist_pen'], 3),
                'r_survival':    round(acc['survival'], 3),
                'r_win':         round(acc['win'], 3),
                'r_loss':        round(acc['loss'], 3),
                # ── Episode state at end ──────────────────────────────────────
                'p1_health':     prev_state.p1_health if prev_state else None,
                'p2_health':     prev_state.p2_health if prev_state else None,
                'timer':         prev_state.timer if prev_state else None,
                'p1_x':          round(prev_state.p1_x or 0, 2) if prev_state else None,
                'p2_x':          round(prev_state.p2_x or 0, 2) if prev_state else None,
                'savestate':     save_path.stem,
                'timestamp':     time.time(),
            }) + '\n')

    print(f'\n[train] Done. {args.episodes} episodes.')

    print(f'[train] Win rate   : {wins / args.episodes * 100:.1f}%')
    print(f'[train] Total reward: {total_reward:.2f}')
    print(f'[train] Log        : {TRAIN_LOG}')


def main() -> None:
    parser = argparse.ArgumentParser(description='MK4 Training Loop')
    parser.add_argument('--episodes', type=int, default=50)
    _ALL_AGENTS = ['random', 'mlp', 'lstm',
                   'gru', 'cont_rssm', 'disc_rssm',
                   'transformer', 'obj_belief', 'latent_planner',
                   # full arch-id aliases
                   'cnn_rnn_reactive_baseline', 'continuous_rssm_hier_ac',
                   'discrete_rssm_hier_ac', 'transformer_wm_hier_ac',
                   'mk4_object_belief_hier_wm', 'latent_planner_mpc_prior']
    parser.add_argument('--agent', default='random', metavar='AGENT',
                        help=f'Architecture to train. Options: {_ALL_AGENTS}')


    parser.add_argument('--savestate', default='',
                        help='Filter to savestates containing this string, e.g. "sonya"')
    parser.add_argument('--save-every', type=int, default=10, dest='save_every',
                        help='Save MLP checkpoint every N episodes (default: 10)')
    args = parser.parse_args()
    run_training(args)


if __name__ == '__main__':
    main()
