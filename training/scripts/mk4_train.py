#!/usr/bin/env python3
"""
mk4_train.py — MK4 Training Loop
──────────────────────────────────────────────────────────
Runs RL training episodes against the MK4 CPU.

Agent sees a 7-float observation:
  [p1_health_norm, p2_health_norm, timer_norm,
   p1_x_norm, p2_x_norm, dist_norm, facing_sign]

Reward: Mk4ShapedRewardExtractor
  - health delta (asymmetric: taking damage hurts 1.5× more)
  - approach reward (get close enough to attack)
  - distance penalty (don't camp far away)
  - win bonus / loss penalty
  - survival per step

Usage:
    # Random agent, 50 episodes
    python3 training/scripts/mk4_train.py --episodes 50 --agent random

    # Filter to a specific character savestate
    python3 training/scripts/mk4_train.py --episodes 10 --savestate sonya

    # Full training with MLP policy
    python3 training/scripts/mk4_train.py --episodes 1000 --agent mlp
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
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

SOCK       = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
STATE_DIR  = N64_ROOT / 'training/data/savestates/mk4_arcade'
LOG_DIR    = N64_ROOT / 'training/data/logs'
TRAIN_LOG  = LOG_DIR / 'mk4_training_log.jsonl'
P1_CTRL    = '/tmp/mk4_ctrl'

# Episode tuning
MAX_EPISODE_SECS = 70      # doubled: gives agents time to finish KOs (~70s ≈ 2 full rounds)
STEP_SECS        = 0.1     # 100ms per agent decision (~6 frames at 60fps)
SETTLE_SECS      = 2.0     # let VS splash animation play before agent acts
X_NORM           = 15.0    # position normalisation ceiling
DIST_NORM        = 15.0    # distance normalisation ceiling


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

def build_obs(state) -> list[float]:
    """
    7-float observation vector:
      [p1_hp, p2_hp, timer, p1_x, p2_x, dist, facing_sign]
    All values normalised to roughly [-1, 1] / [0, 1].
    """
    p1_hp   = (state.p1_health or 0) / 160.0
    p2_hp   = (state.p2_health or 0) / 160.0
    timer   = (state.timer if state.timer is not None else 99) / 99.0
    p1_x    = max(-1.0, min(1.0, (state.p1_x or 0.0) / X_NORM))
    p2_x    = max(-1.0, min(1.0, (state.p2_x or 0.0) / X_NORM))
    dist    = min(1.0, abs((state.p2_x or 0.0) - (state.p1_x or 0.0)) / DIST_NORM)
    # +1 if P2 is to the right of P1 (normal start), -1 if they've crossed over
    facing  = 1.0 if (state.p2_x or 0.0) >= (state.p1_x or 0.0) else -1.0
    return [p1_hp, p2_hp, timer, p1_x, p2_x, dist, facing]


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_agent(agent_type: str, args=None):
    if agent_type == 'random':
        actions = list(MacroAction)
        return lambda obs: random.choice(actions)

    if agent_type == 'mlp':
        from n64train.experiments.mk4_agent import Mk4MlpAgent
        agent = Mk4MlpAgent()
        print(f'[agent] MLP — ep={agent.episode}')
        return agent

    if agent_type == 'lstm':
        from n64train.experiments.mk4_agent import Mk4LstmAgent
        agent = Mk4LstmAgent()
        print(f'[agent] LSTM — ep={agent.episode}')
        return agent

    # Archs 3–8 via registry
    from n64train.experiments.mk4_architectures import build_arch_agent
    agent = build_arch_agent(agent_type)
    print(f'[agent] {agent_type} — ep={agent.episode}')
    return agent



# ── Training loop ─────────────────────────────────────────────────────────────

def run_training(args) -> None:
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    from n64train.reverse.mk4_tracing import Mk4FightTraceProvider, ADDRESSES_CONFIRMED
    from n64train.runtime.rewards import Mk4ShapedRewardExtractor

    savestates = [STATE_DIR / 'test.st']
    if not savestates[0].exists():
        print(f'ERROR: test.st not found at {savestates[0]}')
        sys.exit(1)

    if args.savestate:
        filtered = [s for s in savestates if args.savestate in s.name]
        if filtered:
            savestates = filtered

    print(f'[train] Savestates   : {len(savestates)} — {[s.stem for s in savestates]}')
    print(f'[train] Agent        : {args.agent}')
    print(f'[train] Addresses    : {"REAL" if ADDRESSES_CONFIRMED else "STUB"}')
    print(f'[train] Episodes     : {args.episodes}')
    print(f'[train] Obs dims     : 7  (hp×2, timer, p1_x, p2_x, dist, facing)')
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
            b = SocketEmulatorBridge(SOCK, timeout_sec=20)
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

            # Rolling action history for anti-spam (most recent last)
            from collections import deque
            from n64train.experiments.mk4_agent import FrameStack
            action_history: deque[str] = deque(maxlen=20)
            frame_stack = FrameStack(obs_dim=7, n_frames=4)   # 4 frames → 28 floats

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
                    stacked_obs = frame_stack.push(obs)  # 28-float stacked vector
                    action_macro = agent(stacked_obs)    # feed stacked obs to agent
                    action_history.append(action_macro.value)
                    write_ctrl(macro_to_ctrl_state(action_macro))
                else:
                    frame_stack.push([0.0] * 7)          # warm up frame stack with zero obs
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
            try: b.close()
            except: pass
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
