"""
worker.py — Per-emulator rollout collector for parallel MK4 training.

Each worker:
  1. Connects to its own emulator socket
  2. Collects one episode using the current shared policy weights
  3. Sends (obs_list, act_list, rewards_list, acc_terms) to the learner via Queue
  4. Fetches updated weights from the weight queue before next episode

Supports all 8 architectures via agent_type parameter.
"""
from __future__ import annotations

import os
import sys
import time
import json
import struct
import mmap
import random
import traceback
from collections import deque
from multiprocessing import Queue
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[4]   # worker.py→training→n64train→src→training→n64
sys.path.insert(0, str(N64_ROOT / 'training/src'))
sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

from n64train.runtime.actions import Button, ControllerState, MacroAction
from n64train.runtime.rewards import Mk4ShapedRewardExtractor


def write_ctrl_worker(ctrl_state: ControllerState, path: str) -> None:
    """Write controller state to per-worker ctrl file."""
    from mk4_train import _BTN
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


def run_worker(
    worker_id: int,
    sock_path: str,
    ctrl_path: str,
    rollout_queue: Queue,
    weight_queue: Queue,
    episodes_per_worker: int,
    savestate_path: str,
    agent_type: str = 'mlp',
    ctrl_path_p2: str | None = None,        # self-play: path to P2 ctrl mmap
    opponent_agent=None,                    # self-play: frozen opponent agent
) -> None:
    """Worker process entry point. Runs N episodes, sends rollouts to learner."""
    print(f'[worker-{worker_id}] starting pid={os.getpid()}  agent={agent_type}')
    sys.path.insert(0, str(N64_ROOT / 'training/src'))
    sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    from n64train.reverse.mk4_tracing import Mk4FightTraceProvider
    from n64train.experiments.mk4_agent import FrameStack, ACTIONS
    from mk4_train import build_obs, macro_to_ctrl_state, MAX_EPISODE_SECS, STEP_SECS, SETTLE_SECS

    import torch

    save_path = Path(savestate_path)
    reward_extractor = Mk4ShapedRewardExtractor()

    # ── Build a LOCAL inference-only copy of the agent ─────────────────────────
    # For architectures with recurrent state (LSTM/GRU/RSSM/Transformer),
    # the worker maintains its own hidden state and syncs weights from learner.
    from mk4_train import build_agent
    agent = build_agent(agent_type)

    # ── Stagger startup to prevent all workers from slamming stateload at once ──
    # With 16 simultaneous emulators, CPU is bottlenecked; spread load over time.
    STAGGER_PER_WORKER_SEC = 8.0
    stagger = worker_id * STAGGER_PER_WORKER_SEC
    if stagger > 0:
        print(f'[worker-{worker_id}] startup stagger: sleeping {stagger:.0f}s')
        time.sleep(stagger)

    # ── Bridge connection — reconnect as needed per episode ──────────────────
    # The BridgeServer closes conn after each request loop (idle/timeout).
    # We try to keep ONE connection alive, but reconnect if it breaks.
    b: SocketEmulatorBridge | None = None
    h: Mk4BridgeHelper | None = None
    tracer: Mk4FightTraceProvider | None = None

    def _ensure_bridge() -> bool:
        """Check and reconnect bridge if dead. Returns True if bridge is usable."""
        nonlocal b, h, tracer
        # Liveness check on existing connection — use a SHORT 5s timeout so we
        # don't block the worker for 120s if the hello is slow (e.g. on savestate load).
        if b is not None:
            try:
                if b._socket is not None:
                    b._socket.settimeout(5.0)   # quick liveness check
                b.hello()
                if b._socket is not None:
                    b._socket.settimeout(120.0) # restore normal training timeout
                return True  # still alive
            except Exception:
                try:
                    if b._socket is not None:
                        b._socket.settimeout(120.0)
                except Exception:
                    pass
                # fall through to reconnect below

        # Reconnect — emulator boots in ~60-90s; retry for up to 100s (20 × 5s)
        for attempt in range(20):
            try:
                if b is not None:
                    try: b.close()
                    except Exception: pass
                b = SocketEmulatorBridge(sock_path, timeout_sec=120)
                h = Mk4BridgeHelper(b)
                tracer = Mk4FightTraceProvider(helper=h)
                # Only verify socket accepts — hello() blocks until emulator finishes
                # booting (~90s). The first h.pause() at episode start confirms readiness.
                b.connect()
                if attempt > 0:
                    print(f'[worker-{worker_id}] bridge reconnected (attempt {attempt+1})')
                return True
            except Exception as ce:
                print(f'[worker-{worker_id}] bridge attempt {attempt+1} failed: {ce}')
                time.sleep(5.0)
        return False

    # No initial blocking FATAL check — the episode loop at line ~170 calls
    # _ensure_bridge() per episode and skips via 'continue' if unreachable.
    # Removing this prevents the worker→done→learner-exit→restart loop.

    def _sync_weights(latest_weights: dict | None) -> None:
        if latest_weights is None:
            return
        try:
            agent.net.load_state_dict(latest_weights, strict=False)
        except Exception:
            pass

    valid_episodes = 0      # Fix 1: count valid rollouts, not raw attempts
    max_attempts   = episodes_per_worker * 4  # safety cap: never loop forever
    attempt_idx    = 0
    while valid_episodes < episodes_per_worker and attempt_idx < max_attempts:
        attempt_idx += 1
        # ── Sync latest weights from learner (Bug 6: pure get_nowait loop, no empty()) ──
        latest_weights = None
        while True:
            try:
                latest_weights = weight_queue.get_nowait()
            except Exception:
                break   # queue drained or empty
        _sync_weights(latest_weights)

        # Bug 2: reset BOTH main agent AND frozen opponent at episode start
        if hasattr(agent, 'reset_episode'):
            agent.reset_episode()
        if opponent_agent is not None and hasattr(opponent_agent, 'reset_episode'):
            opponent_agent.reset_episode()

        frame_stack      = FrameStack(obs_dim=7, n_frames=4)
        opp_frame_stack  = FrameStack(obs_dim=7, n_frames=4)   # opponent sees same 28-d obs
        action_history: deque[str] = deque(maxlen=20)
        obs_buf:          list[list[float]] = []
        act_buf:          list[int]         = []
        reward_buf:       list[float]       = []
        old_lp_buf:       list[float]       = []   # PPO: old log-probs from inference
        val_buf:          list[float]       = []   # PPO: value estimates from inference
        cpu_attacked_buf: list[float]       = []
        acc = dict(dealt=0.0, taken=0.0, approach=0.0,
                   dist_pen=0.0, survival=0.0, win=0.0, loss=0.0, spam=0.0)

        try:
            # ── Ensure bridge is alive (reconnect if dropped) ─────────────────
            if not _ensure_bridge():
                print(f'[worker-{worker_id}] ep={ep_idx+1} bridge unreachable — skipping episode, retrying next')
                time.sleep(5.0)
                continue

            # ── Episode setup: pause → stateload → run ────────────────────────
            try:
                h.pause()
                time.sleep(0.2)
            except Exception:
                pass  # first episode may be already paused — ignore

            b.load_savestate_path(save_path)
            write_ctrl_worker(ControllerState(), ctrl_path)
            # Bug 1: always neutralize P2 at episode start regardless of self-play
            if ctrl_path_p2 is not None:
                write_ctrl_worker(ControllerState(), ctrl_path_p2)

            # Fire-and-forget: game starts running, window appears
            h.run()
            time.sleep(SETTLE_SECS)

            # ── Poll until RAM shows live health values ────────────────────────
            # Fix 3: if health never appears, abort the episode entirely.
            # Continuing with uninitialized RAM creates garbage rollouts (Finding 1+3).
            boot_start = time.time()
            health_ready = False
            while time.time() - boot_start < 30.0:
                try:
                    probe = tracer.read(0)
                    if (probe.p1_health is not None and probe.p2_health is not None
                            and probe.p1_health > 0 and probe.p2_health > 0):
                        print(f'[worker-{worker_id}] ep={ep_idx+1} ready: p1={probe.p1_health} p2={probe.p2_health}')
                        health_ready = True
                        break
                except Exception:
                    pass
                time.sleep(0.3)

            if not health_ready:
                print(f'[worker-{worker_id}] attempt={attempt_idx} ERROR: health poll timeout — aborting episode')
                rollout_queue.put({'worker_id': worker_id,
                                   'error': 'health_poll_timeout',
                                   'obs': [], 'acts': [], 'rewards': [],
                                   'cpu_attacked': [], 'acc': acc})
                continue  # do NOT enter episode collection loop — does NOT count toward valid_episodes

            ep_start   = time.time()
            ep_steps   = 0
            MIN_STEPS  = 30   # require ≥ 3s of game time (30 × 0.1s) before early termination
            prev_state = None

            while time.time() - ep_start < MAX_EPISODE_SECS:
                if prev_state is not None:
                    raw_obs = build_obs(prev_state)
                    obs     = frame_stack.push(raw_obs)
                    macro   = agent(obs)
                    action_history.append(macro.value)
                    write_ctrl_worker(macro_to_ctrl_state(macro), ctrl_path)
                    obs_buf.append(obs)
                    act_buf.append(ACTIONS.index(macro))
                    # Grab PPO buffers from agent (populated in __call__)
                    if getattr(agent, '_old_lp_buf', None):
                        old_lp_buf.append(agent._old_lp_buf[-1])
                    if getattr(agent, '_val_buf', None):
                        val_buf.append(agent._val_buf[-1])
                else:
                    frame_stack.push([0.0] * 7)
                    write_ctrl_worker(ControllerState(), ctrl_path)

                # ── P2 self-play injection ────────────────────────────────────
                # Mirror obs: swap P1/P2 health and X so opponent sees itself as P1.
                if ctrl_path_p2 is not None and opponent_agent is not None and prev_state is not None:
                    try:
                        from n64train.reverse.mk4_tracing import TracedState
                        mirrored = TracedState(
                            frame_id  = prev_state.frame_id,
                            p1_health = prev_state.p2_health,
                            p2_health = prev_state.p1_health,
                            timer     = prev_state.timer,
                            p1_x      = (prev_state.p2_x or 0.0),
                            p2_x      = (prev_state.p1_x or 0.0),
                        )
                        # Stack the mirrored raw obs so opponent sees 28-float input
                        # (same format its policy was trained on — raw obs is only 7-float)
                        opp_raw   = build_obs(mirrored)
                        opp_obs   = opp_frame_stack.push(opp_raw)
                        opp_macro = opponent_agent(opp_obs)
                        write_ctrl_worker(macro_to_ctrl_state(opp_macro), ctrl_path_p2)
                    except Exception:
                        write_ctrl_worker(ControllerState(), ctrl_path_p2)

                time.sleep(STEP_SECS)

                # Retry read up to 5 times before concluding bridge dropped.
                # A single transient failure (socket hiccup, read timeout) should
                # NOT reload the savestate mid-fight while health bars are visible.
                next_state = None
                READ_RETRIES = 5
                for _retry in range(READ_RETRIES):
                    try:
                        next_state = tracer.read(ep_steps)
                        break   # success
                    except Exception:
                        if _retry < READ_RETRIES - 1:
                            time.sleep(STEP_SECS)  # brief wait before retry
                        else:
                            # All retries failed — game likely exited (arcade mode).
                            # Only NOW attempt a savestate reload.
                            print(f'[worker-{worker_id}] ep={ep_idx+1} read failed {READ_RETRIES}x at step={ep_steps}, reloading...')
                            if _ensure_bridge():
                                try:
                                    h.pause()
                                    time.sleep(0.2)
                                    b.load_savestate_path(save_path)
                                    write_ctrl_worker(ControllerState(), ctrl_path)
                                    # Bug 1: neutralize P2 on reload too
                                    if ctrl_path_p2 is not None:
                                        write_ctrl_worker(ControllerState(), ctrl_path_p2)
                                    h.run()
                                    time.sleep(SETTLE_SECS)
                                    prev_state = None
                                    next_state = None  # signal outer loop to skip
                                    # Bug 3: full temporal reset on mid-episode reload
                                    # — stale frames, hidden state, and action history from
                                    # the crashed fight must not bleed into the new one.
                                    frame_stack = FrameStack(obs_dim=7, n_frames=4)
                                    opp_frame_stack = FrameStack(obs_dim=7, n_frames=4)
                                    action_history.clear()
                                    if hasattr(agent, 'reset_episode'):
                                        agent.reset_episode()
                                    if opponent_agent is not None and hasattr(opponent_agent, 'reset_episode'):
                                        opponent_agent.reset_episode()
                                except Exception as reload_err:
                                    print(f'[worker-{worker_id}] ep={ep_idx+1} reload failed: {reload_err}')
                            break  # exit retry loop

                if next_state is None:
                    if prev_state is None:
                        break  # couldn't even get first state — fatal
                    continue   # reloaded ok or still recovering, loop again

                ep_steps += 1


                if prev_state is not None:
                    terms  = reward_extractor.compute(
                        prev_state, next_state,
                        action_history=list(action_history))
                    step_r = terms.scalar()
                    reward_buf.append(step_r)
                    cpu_attacked_buf.append(float(terms.damage_taken < -1.0))
                    acc['dealt']    += terms.damage_dealt
                    acc['taken']    += terms.damage_taken
                    acc['approach'] += terms.approach_reward
                    acc['dist_pen'] += terms.distance_penalty
                    acc['survival'] += terms.survival
                    acc['win']      += terms.win_bonus
                    acc['loss']     += terms.loss_penalty
                    acc['spam']     += terms.spam_penalty
                    if ep_steps >= MIN_STEPS and tracer.is_round_over(next_state):
                        # Confirm over 3 extra frames — single transient zero-health
                        # reads can fire is_round_over() falsely mid-match.
                        CONFIRM_FRAMES = 12
                        confirmed = 0
                        for _ in range(CONFIRM_FRAMES):
                            time.sleep(STEP_SECS)
                            try:
                                confirm_state = tracer.read(ep_steps)
                                if tracer.is_round_over(confirm_state):
                                    confirmed += 1
                                else:
                                    confirmed = 0
                                    next_state = confirm_state  # resume from here
                                    break
                            except Exception:
                                confirmed = CONFIRM_FRAMES  # bridge drop = real end
                                break
                        if confirmed >= 10:
                            break  # genuinely over

                prev_state = next_state

            write_ctrl_worker(ControllerState(), ctrl_path)
            # Bug 1: neutralize P2 at clean episode end
            if ctrl_path_p2 is not None:
                write_ctrl_worker(ControllerState(), ctrl_path_p2)
            won       = tracer.p1_won(next_state or prev_state) if (next_state or prev_state) else False
            ep_frames = int((time.time() - ep_start) * 60)

        except Exception as e:
            traceback.print_exc()
            write_ctrl_worker(ControllerState(), ctrl_path)
            # Bug 1: neutralize P2 on exception exit too
            if ctrl_path_p2 is not None:
                write_ctrl_worker(ControllerState(), ctrl_path_p2)
            rollout_queue.put({'worker_id': worker_id, 'error': str(e),
                               'obs': [], 'acts': [], 'rewards': [],
                               'cpu_attacked': [], 'acc': acc})
            continue

        rollout_queue.put({
            'worker_id':    worker_id,
            'obs':          obs_buf,
            'acts':         act_buf,
            'rewards':      reward_buf,
            'old_lps':      old_lp_buf,     # PPO: log-probs at collection time
            'vals':         val_buf,         # PPO: value estimates at collection time
            'cpu_attacked': cpu_attacked_buf,
            'acc':          acc,
            'won':          won,
            'ep_frames':    ep_frames,
            'ep_steps':     ep_steps,
        })
        # Fix 1: only count this as a valid episode if we actually collected data
        if len(obs_buf) >= 2:
            valid_episodes += 1
        total_r = sum(reward_buf)
        won_sym = '\u2713' if won else '\u2717'
        print(f'[worker-{worker_id}] valid={valid_episodes}/{episodes_per_worker}  steps={ep_steps}'
              f'  r={total_r:+.2f}  [dealt={acc["dealt"]:+.1f} taken={acc["taken"]:+.1f}'
              f' spam={acc["spam"]:+.1f}]  won={won_sym}')

    try:
        b.close()
    except Exception:
        pass
    rollout_queue.put({'worker_id': worker_id, 'done': True})
    print(f'[worker-{worker_id}] finished all {episodes_per_worker} episodes')

