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
from n64train.reverse.mk4_tracing import FIGHT_TIMER_ADDR


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
    """Worker process entry point. Runs N episodes, sends rollouts to learner.

    opponent_agent: either None (no self-play) or a state_dict dict produced by
    the main process. The worker reconstructs the frozen opponent locally to
    avoid pickling a full PyTorch model across process boundaries.
    """
    print(f'[worker-{worker_id}] starting pid={os.getpid()}  agent={agent_type}')
    sys.path.insert(0, str(N64_ROOT / 'training/src'))
    sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    from n64train.reverse.mk4_tracing import Mk4FightTraceProvider
    from n64train.experiments.mk4_agent import FrameStack, ACTIONS
    from mk4_train import build_obs, macro_to_ctrl_state, macro_to_ctrl_state_p2, MAX_EPISODE_SECS, STEP_SECS, SETTLE_SECS

    import torch

    save_path = Path(savestate_path)
    reward_extractor = Mk4ShapedRewardExtractor()
    action_frames = max(1, int(round(STEP_SECS * 60.0)))
    settle_frames = max(1, int(round(SETTLE_SECS * 60.0)))

    def _step_frames(frames: int) -> None:
        """Deterministic frame advance: emulator stays paused, advances exactly N frames."""
        if b is None:
            raise RuntimeError("bridge not connected")
        frames = max(1, int(frames))
        timeout_sec = max(10.0, float(frames) * 2.0)
        result = b.debugger_command(
            f'frame {frames}',
            timeout_sec=timeout_sec,
            output_tail_chars=2000,
        )
        output = str(result.get('output', ''))
        if f'M64P_FRAME_OK frames={frames}' not in output:
            raise RuntimeError(f'frame step failed: {output[-500:]}')

    # ── Build a LOCAL inference-only copy of the agent ─────────────────────────
    # For architectures with recurrent state (LSTM/GRU/RSSM/Transformer),
    # the worker maintains its own hidden state and syncs weights from learner.
    from mk4_train import build_agent
    agent = build_agent(agent_type)

    # ── Reconstruct frozen opponent from state_dict (not a pickled model) ──────
    # The main process passes a plain {str: Tensor} dict to avoid serializing
    # the full PyTorch module (which is large, slow, and may break CUDA context).
    _reconstructed_opponent = None
    if opponent_agent is not None and isinstance(opponent_agent, dict):
        _reconstructed_opponent = build_agent(agent_type)
        try:
            _reconstructed_opponent.net.load_state_dict(opponent_agent, strict=False)
        except Exception as e:
            print(f'[worker-{worker_id}] WARNING: could not load opponent weights: {e}')
        for p in _reconstructed_opponent.net.parameters():
            p.requires_grad_(False)
        _reconstructed_opponent.net.eval()
        print(f'[worker-{worker_id}] self-play opponent ready (state_dict loaded)')
    elif opponent_agent == 'random_p2':
        # Random P2 attacker: sends random attacks so P1 takes damage.
        # Used with p1p2state.st where there's no CPU AI.
        import random as _rng
        _p2_actions = list(ACTIONS)

        class _RandomP2:
            def __call__(self, obs):
                return _rng.choice(_p2_actions)
            def reset_episode(self):
                pass
        _reconstructed_opponent = _RandomP2()
        print(f'[worker-{worker_id}] P2 = random attacker')
    elif opponent_agent is not None:
        # Backward compat: accept pre-built agent objects too
        _reconstructed_opponent = opponent_agent
    opponent_agent = _reconstructed_opponent  # shadow param with local object

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

    _bridge_backoff = 0.5   # exponential backoff between episode-level bridge failures

    def _ensure_bridge() -> bool:
        """Check and reconnect bridge if dead. Returns True if bridge is usable."""
        nonlocal b, h, tracer, _bridge_backoff
        # Liveness check on existing connection — use a SHORT 5s timeout so we
        # don't block the worker for 120s if the hello is slow (e.g. on savestate load).
        if b is not None:
            try:
                if b._socket is not None:
                    b._socket.settimeout(5.0)   # quick liveness check
                b.hello()
                if b._socket is not None:
                    b._socket.settimeout(120.0) # restore normal training timeout
                _bridge_backoff = 0.5   # reset backoff on success
                return True  # still alive
            except Exception:
                try:
                    if b._socket is not None:
                        b._socket.settimeout(120.0)
                except Exception:
                    pass
                # fall through to reconnect below

        # Reconnect — emulator boots in ~60-90s under load; retry for up to
        # 150s (150 × 1.0s). Matches the watchdog's cycle time so the worker
        # survives a full bridge restart without giving up.
        RECONNECT_RETRIES = 150
        RECONNECT_SLEEP   = 1.0
        for attempt in range(RECONNECT_RETRIES):
            try:
                if b is not None:
                    try: b.close()
                    except Exception: pass
                b = SocketEmulatorBridge(sock_path, timeout_sec=300)
                h = Mk4BridgeHelper(b)
                tracer = Mk4FightTraceProvider(helper=h)
                b.connect()
                if attempt > 0:
                    print(f'[worker-{worker_id}] bridge reconnected (attempt {attempt+1})')
                _bridge_backoff = 0.5   # reset backoff on success
                return True
            except Exception as ce:
                # Log every 10th attempt to avoid spamming thousands of lines
                if attempt % 10 == 0 or attempt == RECONNECT_RETRIES - 1:
                    print(f'[worker-{worker_id}] bridge attempt {attempt+1}/{RECONNECT_RETRIES} failed: {ce}')
                time.sleep(RECONNECT_SLEEP)
        return False

    # No initial blocking FATAL check — the episode loop at line ~170 calls
    # _ensure_bridge() per episode and skips via 'continue' if unreachable.
    # Removing this prevents the worker→done→learner-exit→restart loop.

    def _sync_weights(bundle: dict | None) -> None:
        """Unpack {weights, reward_config} bundle from weight queue.

        Backward-compat: if bundle is a plain state_dict (old format, no 'weights' key),
        treat it as weights-only.
        """
        if bundle is None:
            return
        # Unpack bundle vs. legacy plain state_dict
        if 'weights' in bundle:
            latest_weights = bundle['weights']
            reward_cfg_dict = bundle.get('reward_config')
        else:
            latest_weights = bundle   # legacy plain dict
            reward_cfg_dict = None
        # Apply network weights
        try:
            agent.net.load_state_dict(latest_weights, strict=False)
        except Exception:
            pass
        # Hot-swap reward extractor config
        if reward_cfg_dict is not None:
            try:
                from n64train.runtime.rewards import RewardConfig
                cfg = RewardConfig(**{
                    k: v for k, v in reward_cfg_dict.items()
                    if k in RewardConfig.__dataclass_fields__
                })
                reward_extractor.update_config(cfg)
            except Exception as e:
                pass  # bad config from LLM — keep existing weights

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

        frame_stack      = FrameStack(obs_dim=14, n_frames=4)
        opp_frame_stack  = FrameStack(obs_dim=14, n_frames=4)   # opponent sees same 56-d obs
        action_history: deque[str] = deque(maxlen=20)
        obs_buf:          list[list[float]] = []
        act_buf:          list[int]         = []
        reward_buf:       list[float]       = []
        old_lp_buf:       list[float]       = []   # PPO: old log-probs from inference
        val_buf:          list[float]       = []   # PPO: value estimates from inference
        cpu_attacked_buf: list[float]       = []
        acc = dict(dealt=0.0, taken=0.0, approach=0.0,
                   dist_pen=0.0, survival=0.0, win=0.0, loss=0.0, spam=0.0,
                   dealt_hp=0.0, taken_hp=0.0)   # Fix 4: raw HP for coach stats

        try:
            # ── Ensure bridge is alive (reconnect if dropped) ─────────────────
            if not _ensure_bridge():
                print(f'[worker-{worker_id}] attempt={attempt_idx} bridge unreachable — backoff {_bridge_backoff:.1f}s')
                time.sleep(_bridge_backoff)
                _bridge_backoff = min(_bridge_backoff * 2, 30.0)  # exponential backoff, cap 30s
                continue

            # ── Episode setup: pause → stateload → deterministic frame step ────
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

            # Deterministic boot: keep emulator paused and advance known frames.
            _step_frames(settle_frames)

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
                        print(f'[worker-{worker_id}] attempt={attempt_idx} ready: p1={probe.p1_health} p2={probe.p2_health}')
                        health_ready = True
                        break
                except Exception:
                    pass
                _step_frames(5)

            if not health_ready:
                print(f'[worker-{worker_id}] attempt={attempt_idx} ERROR: health poll timeout — aborting episode')
                rollout_queue.put({'worker_id': worker_id,
                                   'error': 'health_poll_timeout',
                                   'obs': [], 'acts': [], 'rewards': [],
                                   'cpu_attacked': [], 'acc': acc})
                continue  # do NOT enter episode collection loop — does NOT count toward valid_episodes

            ep_start       = time.time()
            ep_steps       = 0
            # MIN_STEPS: minimum steps before is_round_over() can fire.
            # With verified health addresses (u32 read, normalized 0-160) and health_ready poll
            # above, false positives from uninitialized RAM are already handled.
            # 60 steps ≈ 2s of agent decisions — enough to skip the initial
            # savestate transition without missing quick KOs.
            MIN_STEPS      = 60
            prev_state     = None
            round_ended    = False   # set True only when is_round_over() confirms break
            _mid_ep_reload = False   # True when bridge dropped + savestate reloaded mid-episode

            while time.time() - ep_start < MAX_EPISODE_SECS:
                if prev_state is not None:
                    raw_obs = build_obs(prev_state)
                    obs     = frame_stack.push(raw_obs)
                    # agent.__call__ records old_lp / value into its own buffers
                    macro   = agent(obs)
                    action_history.append(macro.value)
                    write_ctrl_worker(macro_to_ctrl_state(macro), ctrl_path)
                    obs_buf.append(obs)
                    act_buf.append(ACTIONS.index(macro))
                    # PPO buffers: always pull the last entry so lengths stay
                    # identical to obs_buf / act_buf (Bug 2 fix).
                    # _old_lp_buf / _val_buf are initialised in reset_episode();
                    # for non-PPO agents (e.g. random) the hasattr guard is kept.
                    if hasattr(agent, '_old_lp_buf') and agent._old_lp_buf:
                        old_lp_buf.append(agent._old_lp_buf[-1])
                    elif hasattr(agent, '_old_lp_buf'):
                        old_lp_buf.append(0.0)   # placeholder if agent didn't populate
                    if hasattr(agent, '_val_buf') and agent._val_buf:
                        val_buf.append(agent._val_buf[-1])
                    elif hasattr(agent, '_val_buf'):
                        val_buf.append(0.0)      # placeholder
                else:
                    frame_stack.push([0.0] * 14)
                    write_ctrl_worker(ControllerState(), ctrl_path)

                # ── P2 self-play injection ────────────────────────────────────
                # Mirror obs: swap P1/P2 health and X so opponent sees itself as P1.
                if ctrl_path_p2 is not None and opponent_agent is not None and prev_state is not None:
                    try:
                        from n64train.reverse.mk4_tracing import TracedState
                        # Mirror extras dict: swap p1_↔p2_ prefixed keys
                        src_ex = prev_state.extras if prev_state.extras else {}
                        mir_ex = {}
                        for k, v in src_ex.items():
                            if k.startswith('p1_'):
                                mir_ex['p2_' + k[3:]] = v
                            elif k.startswith('p2_'):
                                mir_ex['p1_' + k[3:]] = v
                            else:
                                mir_ex[k] = v  # facing_sign etc.
                        # Flip facing_sign since perspective is reversed
                        if 'facing_sign' in mir_ex:
                            mir_ex['facing_sign'] = -mir_ex['facing_sign']
                        mirrored = TracedState(
                            frame_id  = prev_state.frame_id,
                            p1_health = prev_state.p2_health,
                            p2_health = prev_state.p1_health,
                            timer     = prev_state.timer,
                            p1_x      = (prev_state.p2_x or 0.0),
                            p2_x      = (prev_state.p1_x or 0.0),
                            p1_y      = prev_state.p2_y,
                            p2_y      = prev_state.p1_y,
                            p1_facing = prev_state.p2_facing,
                            p2_facing = prev_state.p1_facing,
                            extras    = mir_ex,
                        )
                        # Stack the mirrored raw obs so opponent sees 56-float input
                        opp_raw   = build_obs(mirrored)
                        opp_obs   = opp_frame_stack.push(opp_raw)
                        opp_macro = opponent_agent(opp_obs)
                        write_ctrl_worker(macro_to_ctrl_state_p2(opp_macro), ctrl_path_p2)  # Fix 6: mirrored P2 directions
                    except Exception:
                        write_ctrl_worker(ControllerState(), ctrl_path_p2)

                _step_frames(action_frames)

                # Freeze fight timer at 99 every step to prevent timeout-based
                # round endings. Without this, rounds end at timer=0 before the
                # CPU deals meaningful damage to P1, causing taken=+0.0.
                try:
                    h.write_u8(FIGHT_TIMER_ADDR, 99)
                except Exception:
                    pass  # non-fatal — round will just have a ticking timer

                # Retry read before concluding bridge dropped.
                # A single transient failure (socket hiccup, read timeout) should
                # NOT reload the savestate mid-fight while health bars are visible.
                # With 4 emulators running simultaneously (watchdog launches lstm,
                # obj_belief, transformer, disc_rssm), CPU contention causes bridge
                # read spikes of 5-10s+. tracer.read() does 14+ individual socket
                # reads per frame — any one failing triggers a full retry.
                # 30 × 0.5s = 15s tolerance: survives multi-emu CPU spikes without
                # false-positive savestate reloads mid-fight.
                next_state = None
                READ_RETRIES = 30
                READ_RETRY_SLEEP = 0.5   # 0.5s between retries → 15s total tolerance
                for _retry in range(READ_RETRIES):
                    try:
                        next_state = tracer.read(ep_steps)
                        break   # success
                    except Exception:
                        if _retry < READ_RETRIES - 1:
                            time.sleep(READ_RETRY_SLEEP)
                        else:
                            # All retries failed — game likely exited (arcade mode).
                            # Only NOW attempt a savestate reload.
                            print(f'[worker-{worker_id}] attempt={attempt_idx} read failed {READ_RETRIES}x at step={ep_steps}, reloading...')
                            if _ensure_bridge():
                                try:
                                    h.pause()
                                    time.sleep(0.2)
                                    b.load_savestate_path(save_path)
                                    write_ctrl_worker(ControllerState(), ctrl_path)
                                    # Bug 1: neutralize P2 on reload too
                                    if ctrl_path_p2 is not None:
                                        write_ctrl_worker(ControllerState(), ctrl_path_p2)
                                    _step_frames(settle_frames)
                                    _mid_ep_reload = True   # don't break — continue episode after reload
                                    prev_state = None
                                    next_state = None  # signal outer loop to skip
                                    # Bug 3: full temporal reset on mid-episode reload
                                    # — stale frames, hidden state, and action history from
                                    # the crashed fight must not bleed into the new one.
                                    frame_stack = FrameStack(obs_dim=14, n_frames=4)
                                    opp_frame_stack = FrameStack(obs_dim=14, n_frames=4)
                                    action_history.clear()
                                    if hasattr(agent, 'reset_episode'):
                                        agent.reset_episode()
                                    if opponent_agent is not None and hasattr(opponent_agent, 'reset_episode'):
                                        opponent_agent.reset_episode()
                                except Exception as reload_err:
                                    print(f'[worker-{worker_id}] attempt={attempt_idx} reload failed: {reload_err}')
                            break  # exit retry loop

                if next_state is None:
                    if prev_state is None and not _mid_ep_reload:
                        break  # truly could not get first state ever — fatal
                    _mid_ep_reload = False  # reset flag after first successful continue
                    continue   # recovering from mid-episode reload, try again

                ep_steps += 1

                if ep_steps % 50 == 1 and next_state is not None:
                    print(
                        f'[worker-{worker_id}] TRACE step={ep_steps} '
                        f'p1={next_state.p1_health} p2={next_state.p2_health} '
                        f'timer={next_state.timer}',
                        flush=True,
                    )


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
                    # Fix 4: also track raw HP (always positive) for coach stats
                    if prev_state.p2_health is not None and next_state.p2_health is not None:
                        acc['dealt_hp'] += max(0.0, float(prev_state.p2_health - next_state.p2_health))
                    if prev_state.p1_health is not None and next_state.p1_health is not None:
                        acc['taken_hp'] += max(0.0, float(prev_state.p1_health - next_state.p1_health))
                    if ep_steps >= MIN_STEPS and tracer.is_round_over(next_state):
                        # Confirm over a few extra frames to filter transient
                        # zero-health reads. 3/5 is enough — the old 10/12 was
                        # so strict that round 2 would auto-start during
                        # confirmation, causing multi-round bleed-through.
                        CONFIRM_FRAMES = 5
                        confirmed = 0
                        for _ in range(CONFIRM_FRAMES):
                            _step_frames(action_frames)
                            try:
                                confirm_state = tracer.read(ep_steps)
                                if tracer.is_round_over(confirm_state):
                                    confirmed += 1
                                else:
                                    break
                            except Exception:
                                confirmed = CONFIRM_FRAMES  # bridge drop = real end
                                break
                        if confirmed >= 3:
                            round_ended = True
                            break  # genuinely over

                prev_state = next_state

            write_ctrl_worker(ControllerState(), ctrl_path)
            # Bug 1: neutralize P2 at clean episode end
            if ctrl_path_p2 is not None:
                write_ctrl_worker(ControllerState(), ctrl_path_p2)
            won       = tracer.p1_won(next_state or prev_state) if (next_state or prev_state) else False
            ep_frames = int((time.time() - ep_start) * 60)

            # PPO truncation bootstrap.
            # truncated = True when the step loop exited due to wall-clock timeout,
            # NOT because is_round_over() confirmed the round finished.
            # Using the round_ended flag is exact and avoids both the MAX_STEPS
            # NameError and the 0.95×MAX_EPISODE_SECS false-positive risk.
            truncated     = not round_ended
            bootstrap_val = 0.0
            if truncated and val_buf:
                # val_buf[-1] = V̂(s_T): last in-trajectory value estimate.
                # True bootstrap needs V̂(s_{T+1}) via a forward pass on next_state,
                # but the bias is O(STEP_SECS) and negligible for short steps.
                bootstrap_val = val_buf[-1]

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
            'old_lps':      old_lp_buf,
            'vals':         val_buf,
            'cpu_attacked': cpu_attacked_buf,
            'acc':          acc,
            'won':          won,
            'ep_frames':    ep_frames,
            'ep_steps':     ep_steps,
            'truncated':    truncated,
            'bootstrap_val': bootstrap_val,
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

