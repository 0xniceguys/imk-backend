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
from n64train.runtime.rewards import Mk4ShapedRewardExtractor, ATTACK_ACTIONS
from n64train.reverse.mk4_tracing import (
    FIGHT_TIMER_ADDR,
    P1_DISPLAY_HEALTH_ADDR,
    P1_HEALTH_ADDR,
    P2_DISPLAY_HEALTH_ADDR,
    P2_HEALTH_ADDR,
    HEALTH_FP_ONE,
    HEALTH_MAX,
)


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
    opponent_rotation: dict | None = None,  # self-play: rotate opponents from checkpoints
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
    from mk4_train import (
        build_obs,
        macro_to_ctrl_state_facing,
        MAX_EPISODE_SECS,
        RAW_OBS_DIM,
        STEP_SECS,
        SETTLE_SECS,
    )

    import torch
    torch.set_num_threads(1)  # workers are sequential — extra threads waste CPU cores

    save_path = Path(savestate_path)
    save_name = save_path.name.lower()
    # This state is the required training entrypoint; in practice it can show
    # transient zero-health reads right after load even when the fight is ready.
    # Do not block episode start on strict health gating for this file.
    skip_strict_start_health_gate = (save_name == 'p1p2_trainingscript.st')
    reward_extractor = Mk4ShapedRewardExtractor()
    action_frames = max(1, int(round(STEP_SECS * 60.0)))
    settle_frames = max(1, int(round(SETTLE_SECS * 60.0)))
    hit_credit_window = 8  # credit delayed damage to attack actions from recent steps
    # Terminal reward shaping: keep KO as the strongest objective while still
    # assigning a weak signal to non-KO endings.
    TERMINAL_WIN_MULT = {
        'ko': 1.00,
        'timer': 0.35,
        'wall_timeout': 0.0,   # no reward for running out the wall clock
    }
    TERMINAL_LOSS_MULT = {
        'ko': 1.00,
        'timer': 1.00,
        'wall_timeout': 1.50,  # extra punishment for passive wall-timeout losses
    }
    # TRACE logging is expensive with many workers; default to a lower frequency.
    # Set N64TRACE_EVERY=0 to disable, or a small integer for verbose tracing.
    try:
        trace_every = max(0, int(os.environ.get('N64TRACE_EVERY', '100')))
    except Exception:
        trace_every = 100

    def _step_frames(frames: int) -> None:
        """Deterministic frame advance: emulator stays paused, advances exactly N frames."""
        if b is None:
            raise RuntimeError("bridge not connected")
        remaining = max(1, int(frames))
        # Larger chunks = fewer round-trips = faster stepping (each call has fixed IPC overhead)
        chunk = 120
        while remaining > 0:
            n = min(chunk, remaining)
            timeout_sec = max(10.0, float(n) * 2.0)
            result = b.debugger_command(
                f'frame {n}',
                timeout_sec=timeout_sec,
                output_tail_chars=2000,
            )
            output = str(result.get('output', ''))
            if f'M64P_FRAME_OK frames={n}' not in output:
                raise RuntimeError(f'frame step failed: {output[-500:]}')
            remaining -= n

    def _terminal_reward(result: str | None, terminal_kind: str | None) -> float:
        if result not in ('win', 'loss'):
            return 0.0
        if not terminal_kind:
            terminal_kind = 'ko'
        if result == 'win':
            base = float(reward_extractor._get('win_bonus'))
            mult = float(TERMINAL_WIN_MULT.get(terminal_kind, 1.0))
            return base * mult
        base = float(reward_extractor._get('loss_penalty'))
        mult = float(TERMINAL_LOSS_MULT.get(terminal_kind, 1.0))
        return -(base * mult)

    # ── Build a LOCAL inference-only copy of the agent ─────────────────────────
    # For architectures with recurrent state (LSTM/GRU/RSSM/Transformer),
    # the worker maintains its own hidden state and syncs weights from learner.
    from mk4_train import build_agent
    agent = build_agent(agent_type)

    rotation_entries: list[dict[str, str]] = []
    rotation_every = 30
    rotation_idx = -1

    def _load_opponent_checkpoint(opp_agent_type: str, opp_run_id: str):
        opp = build_agent(opp_agent_type)
        ckpt_loaded = 'none'
        try:
            base = opp.CKPT
            scoped = base.parent / f'{base.stem}_{opp_run_id}{base.suffix}'
            if scoped.exists():
                opp.load(scoped)
                ckpt_loaded = scoped.name
            elif base.exists():
                opp.load(base)
                ckpt_loaded = base.name
            else:
                print(
                    f'[worker-{worker_id}] WARNING: no opponent checkpoint for '
                    f'{opp_agent_type}:{opp_run_id} (tried {scoped.name} and {base.name})'
                )
        except Exception as e:
            print(f'[worker-{worker_id}] WARNING: opponent load failed {opp_agent_type}:{opp_run_id}: {e}')
        try:
            for p in opp.net.parameters():
                p.requires_grad_(False)
            opp.net.eval()
        except Exception:
            pass
        return opp, ckpt_loaded

    # ── Reconstruct opponent (fixed or rotating) ──────────────────────────────
    _reconstructed_opponent = None
    if isinstance(opponent_rotation, dict):
        try:
            raw_entries = opponent_rotation.get('entries', [])
            rotation_every = max(1, int(opponent_rotation.get('rotate_every', 30)))
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                opp_type = str(item.get('agent_type', '')).strip()
                opp_run = str(item.get('run_id', opp_type)).strip() or opp_type
                if opp_type:
                    rotation_entries.append({'agent_type': opp_type, 'run_id': opp_run})
        except Exception:
            rotation_entries = []
        if rotation_entries and ctrl_path_p2 is not None:
            first = rotation_entries[0]
            _reconstructed_opponent, loaded = _load_opponent_checkpoint(
                first['agent_type'], first['run_id']
            )
            rotation_idx = 0
            print(
                f'[worker-{worker_id}] self-play rotation enabled '
                f'pool={len(rotation_entries)} every={rotation_every} eps '
                f'current={first["agent_type"]}:{first["run_id"]} ckpt={loaded}'
            )

    if opponent_agent is not None and isinstance(opponent_agent, dict):
        # Backward-compatible fixed-state_dict path.
        _reconstructed_opponent = build_agent(agent_type)
        try:
            _reconstructed_opponent.net.load_state_dict(opponent_agent, strict=False)
        except Exception as e:
            print(f'[worker-{worker_id}] WARNING: could not load opponent weights: {e}')
        for p in _reconstructed_opponent.net.parameters():
            p.requires_grad_(False)
        _reconstructed_opponent.net.eval()
        rotation_entries = []
        rotation_idx = -1
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
    STAGGER_PER_WORKER_SEC = 2.0  # was 8s — designed for 16 emulators, 2 per run needs much less
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
                    b = None
                new_b = SocketEmulatorBridge(sock_path, timeout_sec=30)
                new_b.connect()
                # Require an actual protocol roundtrip before declaring success.
                # A raw socket connect can succeed against a stale/half-dead server.
                new_b.hello()
                if new_b._socket is not None:
                    new_b._socket.settimeout(120.0)
                # Only assign on success — prevents leaking partial objects
                b = new_b
                h = Mk4BridgeHelper(b)
                tracer = Mk4FightTraceProvider(helper=h)
                if attempt > 0:
                    print(f'[worker-{worker_id}] bridge reconnected (attempt {attempt+1})')
                _bridge_backoff = 0.5   # reset backoff on success
                return True
            except Exception as ce:
                # Clean up partially-constructed bridge on failure
                if 'new_b' in dir() and new_b is not b:
                    try: new_b.close()
                    except Exception: pass
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

    valid_episodes = 0      # count only strict single-round valid rollouts
    max_attempts   = episodes_per_worker * 20  # tolerate retries from strict validation/reloads
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

        # ── Self-play opponent rotation (fixed interval, win-rate agnostic) ───
        if rotation_entries and ctrl_path_p2 is not None:
            desired_idx = (valid_episodes // rotation_every) % len(rotation_entries)
            if desired_idx != rotation_idx or opponent_agent is None:
                entry = rotation_entries[desired_idx]
                try:
                    new_opp, loaded = _load_opponent_checkpoint(
                        entry['agent_type'],
                        entry['run_id'],
                    )
                    opponent_agent = new_opp
                    rotation_idx = desired_idx
                    print(
                        f'[worker-{worker_id}] rotated opponent -> '
                        f'{entry["agent_type"]}:{entry["run_id"]} '
                        f'(valid_ep={valid_episodes}, ckpt={loaded})'
                    )
                except Exception as e:
                    print(f'[worker-{worker_id}] WARNING: opponent rotation load failed: {e}')

        # Bug 2: reset BOTH main agent AND frozen opponent at episode start
        if hasattr(agent, 'reset_episode'):
            agent.reset_episode()
        if opponent_agent is not None and hasattr(opponent_agent, 'reset_episode'):
            opponent_agent.reset_episode()

        frame_stack      = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)
        opp_frame_stack  = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)   # opponent sees same stacked obs
        action_history: deque[str] = deque(maxlen=20)
        recent_attack_steps: deque[int] = deque()
        obs_buf:          list[list[float]] = []
        act_buf:          list[int]         = []
        reward_buf:       list[float]       = []
        old_lp_buf:       list[float]       = []   # PPO: old log-probs from inference
        val_buf:          list[float]       = []   # PPO: value estimates from inference
        cpu_attacked_buf: list[float]       = []
        acc = dict(dealt=0.0, taken=0.0, approach=0.0,
                   dist_pen=0.0, survival=0.0, win=0.0, loss=0.0, spam=0.0,
                   dealt_hp=0.0, taken_hp=0.0,   # Fix 4: raw HP for coach stats
                   attacks=0, hits=0, moves=0, idles=0)  # behavioral stats for LLM coach

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
            except Exception:
                pass  # first episode may be already paused — ignore

            print(
                f'[worker-{worker_id}] attempt={attempt_idx} loading savestate: {save_path}',
                flush=True,
            )
            b.load_savestate_path(save_path)
            write_ctrl_worker(ControllerState(), ctrl_path)
            # Bug 1: always neutralize P2 at episode start regardless of self-play
            if ctrl_path_p2 is not None:
                write_ctrl_worker(ControllerState(), ctrl_path_p2)

            # Deterministic boot: keep emulator paused and advance known frames.
            _step_frames(settle_frames)
            print(
                f'[worker-{worker_id}] attempt={attempt_idx} settle done ({settle_frames} frames)',
                flush=True,
            )

            # ── Poll until RAM shows a real round start (full-ish health) ─────
            # Keep this lightweight, but tolerant to transient debugger read
            # failures by treating each address read independently.
            boot_start = time.time()
            health_ready = False
            round_start_ready = False
            poll_iter = 0
            stable_ready_hits = 0
            last_p1_hp = None
            last_p2_hp = None
            last_p1_ts = 0.0
            last_p2_ts = 0.0
            hp_grace_sec = 2.0

            def _decode_start_hp_from_word(raw_word: int | None) -> int | None:
                """Decode fixed-point health for round-start readiness only.

                Reject implausible tiny/garbage values to avoid caching noise
                (e.g., transient p1=2) as a "valid" health sample.
                """
                if raw_word is None or raw_word <= 0:
                    return None
                ratio = float(raw_word) / float(HEALTH_FP_ONE)
                # Round-start should be in a plausible band near full health.
                if ratio < 0.5 or ratio > 1.5:
                    return None
                hp = int(round(max(0.0, min(1.0, ratio)) * HEALTH_MAX))
                if hp < 80:
                    return None
                return hp

            def _decode_start_hp_from_hud(raw_hud: int | None) -> int | None:
                """Decode HUD byte to 0..160 for readiness checks.

                Some states expose 0..255 HUD scale; map that to 0..160.
                """
                if raw_hud is None or raw_hud <= 0:
                    return None
                if raw_hud > HEALTH_MAX:
                    hp = int(round((float(raw_hud) / 255.0) * HEALTH_MAX))
                else:
                    hp = int(raw_hud)
                hp = max(0, min(int(HEALTH_MAX), hp))
                if hp < 80:
                    return None
                return hp

            if skip_strict_start_health_gate:
                health_ready = True
                round_start_ready = True
                print(
                    f'[worker-{worker_id}] attempt={attempt_idx} start gate bypass '
                    f'for {save_name} (using post-settle immediate start)',
                    flush=True,
                )
            else:
                while time.time() - boot_start < 45.0:
                    poll_iter += 1
                    now = time.time()
                    p1_hp = None
                    p2_hp = None
                    p1_raw = None
                    p2_raw = None
                    p1_hud = None
                    p2_hud = None
                    timer = None

                    try:
                        p1_raw = h.read_u32(P1_HEALTH_ADDR)
                        p1_hp = _decode_start_hp_from_word(p1_raw)
                    except Exception:
                        pass
                    try:
                        p2_raw = h.read_u32(P2_HEALTH_ADDR)
                        p2_hp = _decode_start_hp_from_word(p2_raw)
                    except Exception:
                        pass
                    try:
                        timer = h.read_u8(FIGHT_TIMER_ADDR)
                    except Exception:
                        timer = None

                    # Fallback to HUD bytes if fixed-point health words are still
                    # unavailable during early boot after savestate load.
                    if p1_hp is None:
                        try:
                            p1_hud = h.read_u8(P1_DISPLAY_HEALTH_ADDR)
                            p1_hp = _decode_start_hp_from_hud(p1_hud)
                        except Exception:
                            pass
                    if p2_hp is None:
                        try:
                            p2_hud = h.read_u8(P2_DISPLAY_HEALTH_ADDR)
                            p2_hp = _decode_start_hp_from_hud(p2_hud)
                        except Exception:
                            pass

                    # Health can transiently dip to 0 right after savestate load.
                    # Keep the last non-zero sample for a short grace window.
                    if p1_hp is not None and p1_hp >= 120:
                        last_p1_hp = p1_hp
                        last_p1_ts = now
                    elif last_p1_hp is not None and (now - last_p1_ts) <= hp_grace_sec:
                        p1_hp = last_p1_hp

                    if p2_hp is not None and p2_hp >= 120:
                        last_p2_hp = p2_hp
                        last_p2_ts = now
                    elif last_p2_hp is not None and (now - last_p2_ts) <= hp_grace_sec:
                        p2_hp = last_p2_hp

                    if p1_hp is not None and p2_hp is not None:
                        health_ready = True
                        # Start once we are near a clean round start; keep timer gate
                        # relaxed because timer reads can jitter during transitions.
                        timer_ok = (timer is None) or (timer >= 20)
                        if p1_hp >= 150 and p2_hp >= 150 and timer_ok:
                            stable_ready_hits += 1
                            if stable_ready_hits >= 2:
                                print(
                                    f'[worker-{worker_id}] attempt={attempt_idx} ready: '
                                    f'p1={p1_hp} p2={p2_hp} timer={timer}',
                                    flush=True,
                                )
                                round_start_ready = True
                                break
                        else:
                            stable_ready_hits = 0
                    else:
                        stable_ready_hits = 0

                    if poll_iter % 30 == 0:
                        print(
                            f'[worker-{worker_id}] attempt={attempt_idx} waiting: '
                            f'p1={p1_hp} p2={p2_hp} timer={timer} '
                            f'raw=({p1_raw},{p2_raw}) hud=({p1_hud},{p2_hud})',
                            flush=True,
                        )
                    _step_frames(5)

            if not health_ready:
                print(f'[worker-{worker_id}] attempt={attempt_idx} ERROR: health poll timeout — aborting episode')
                rollout_queue.put({'worker_id': worker_id,
                                   'error': 'health_poll_timeout',
                                   'obs': [], 'acts': [], 'rewards': [],
                                   'cpu_attacked': [], 'acc': acc})
                continue  # do NOT enter episode collection loop — does NOT count toward valid_episodes
            if not round_start_ready:
                msg = 'round_start_not_detected'
                print(f'[worker-{worker_id}] attempt={attempt_idx} ERROR: {msg} — aborting episode')
                rollout_queue.put({'worker_id': worker_id,
                                   'error': msg,
                                   'obs': [], 'acts': [], 'rewards': [],
                                   'cpu_attacked': [], 'acc': acc})
                continue

            # For p1p2_trainingscript.st, controls become reliably responsive only
            # after a short post-load warmup window. Also pre-engage in self-play
            # so both fighters start close enough to exchange immediately.
            if save_name == 'p1p2_trainingscript.st':
                write_ctrl_worker(ControllerState(), ctrl_path)
                if ctrl_path_p2 is not None:
                    write_ctrl_worker(ControllerState(), ctrl_path_p2)
                warmup_frames = 12   # ~0.2s at 60 FPS — minimal settle for control responsiveness
                _step_frames(warmup_frames)
                print(
                    f'[worker-{worker_id}] attempt={attempt_idx} warmup done '
                    f'({warmup_frames} frames) for {save_name}',
                    flush=True,
                )

                # No pre-engage walk — approach reward handles closing distance

            ep_start       = time.time()
            ep_steps       = 0
            # MIN_STEPS: minimum steps before is_round_over() can fire.
            # With verified health addresses (u32 read, normalized 0-160) and health_ready poll
            # above, false positives from uninitialized RAM are already handled.
            # 30 steps ≈ 1s — fast enough to catch quick KOs.
            MIN_STEPS      = 30
            prev_state     = None
            next_state     = None
            round_ended    = False
            round_result   = None    # one of: 'win', 'loss'
            invalid_reason = None
            terminal_kind  = None    # one of: 'ko', 'timer', 'wall_timeout'
            terminal_reason = None
            _fighting_started = False  # True once either health drops below 160

            while time.time() - ep_start < MAX_EPISODE_SECS:
                if prev_state is not None:
                    raw_obs = build_obs(prev_state)
                    obs     = frame_stack.push(raw_obs)
                    # agent.__call__ records old_lp / value into its own buffers
                    macro   = agent(obs)
                    action_history.append(macro.value)
                    p1_facing = 1.0
                    if prev_state.extras:
                        p1_facing = float(prev_state.extras.get('facing_sign', 1.0))
                    write_ctrl_worker(
                        macro_to_ctrl_state_facing(macro, p1_facing),
                        ctrl_path,
                    )
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
                    frame_stack.push([0.0] * RAW_OBS_DIM)
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
                        # Stack mirrored raw obs so opponent sees full stacked input.
                        opp_raw   = build_obs(mirrored)
                        opp_obs   = opp_frame_stack.push(opp_raw)
                        opp_macro = opponent_agent(opp_obs)
                        opp_facing = float(mir_ex.get('facing_sign', 1.0))
                        write_ctrl_worker(
                            macro_to_ctrl_state_facing(opp_macro, opp_facing),
                            ctrl_path_p2,
                        )
                    except Exception as _p2_exc:
                        print(
                            f'[worker-{worker_id}] P2 opponent error step={ep_steps}: {_p2_exc}',
                            flush=True,
                        )
                        write_ctrl_worker(ControllerState(), ctrl_path_p2)

                _step_frames(action_frames)

                # Retry reads briefly for transient debugger hiccups.
                # If we still cannot read state, mark this rollout invalid and
                # restart from a fresh savestate on the next attempt.
                next_state = None
                READ_RETRIES = 5
                READ_RETRY_SLEEP = 0.1   # 0.1s between retries → 0.5s total tolerance
                for _retry in range(READ_RETRIES):
                    try:
                        next_state = tracer.read(ep_steps)
                        break   # success
                    except Exception:
                        if _retry < READ_RETRIES - 1:
                            time.sleep(READ_RETRY_SLEEP)
                        else:
                            invalid_reason = f'state_read_failed step={ep_steps} retries={READ_RETRIES}'
                            print(
                                f'[worker-{worker_id}] attempt={attempt_idx} {invalid_reason} — '
                                f'restarting episode from savestate',
                                flush=True,
                            )
                if invalid_reason is not None or next_state is None:
                    if invalid_reason is None:
                        invalid_reason = f'state_read_none step={ep_steps}'
                    break

                ep_steps += 1

                if trace_every > 0 and (ep_steps % trace_every == 1) and next_state is not None:
                    print(
                        f'[worker-{worker_id}] TRACE step={ep_steps} '
                        f'p1={next_state.p1_health} p2={next_state.p2_health} '
                        f'timer={next_state.timer} fighting={_fighting_started} '
                        f'action={action_history[-1] if action_history else "N/A"}',
                        flush=True,
                    )


                if prev_state is not None:
                    terms  = reward_extractor.compute(
                        prev_state, next_state,
                        action_history=list(action_history))
                    step_r = terms.scalar()
                    reward_buf.append(step_r)
                    # Belief target should reflect any real HP loss this step,
                    # not thresholded/scaled reward terms.
                    p1_hp_lost = 0.0
                    if prev_state.p1_health is not None and next_state.p1_health is not None:
                        p1_hp_lost = max(0.0, float(prev_state.p1_health - next_state.p1_health))
                    cpu_attacked_buf.append(1.0 if p1_hp_lost > 0.0 else 0.0)
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
                    # Behavioral stats: what is the agent actually doing?
                    last_act = action_history[-1] if action_history else None
                    while recent_attack_steps and (ep_steps - recent_attack_steps[0]) > hit_credit_window:
                        recent_attack_steps.popleft()
                    if last_act in ATTACK_ACTIONS:
                        acc['attacks'] += 1
                        recent_attack_steps.append(ep_steps)
                    elif last_act in ('ADVANCE', 'RETREAT', 'RUN', 'JUMP_FORWARD',
                                      'JUMP_BACK', 'JUMP_NEUTRAL', 'SIDE_STEP_IN',
                                      'SIDE_STEP_OUT', 'CROUCH'):
                        acc['moves'] += 1
                    elif last_act == 'NEUTRAL':
                        acc['idles'] += 1
                    if terms.damage_dealt > 0 and recent_attack_steps:
                        acc['hits'] += 1
                        recent_attack_steps.popleft()

                    # ── Track fighting started (health dropped below max) ────
                    p1h = next_state.p1_health
                    p2h = next_state.p2_health
                    if not _fighting_started:
                        if (p1h is not None and p1h < 160) or (p2h is not None and p2h < 160):
                            _fighting_started = True
                            print(
                                f'[worker-{worker_id}] FIGHT STARTED step={ep_steps} '
                                f'p1={p1h} p2={p2h}',
                                flush=True,
                            )

                    # ── Strict single-round end detection ────────────────────
                    # We train only on a decisive *single* round outcome.
                    # Any transition to round-2/menu without explicit win/loss is invalid.
                    if _fighting_started and ep_steps >= MIN_STEPS:
                        _end_reason = None
                        _timeout_outcome = False
                        if p1h is not None and p1h <= 0 and p2h is not None and p2h <= 0:
                            invalid_reason = f'double_ko p1={p1h} p2={p2h}'
                            _end_reason = invalid_reason
                        elif p1h is not None and p1h <= 0:
                            round_result = 'loss'
                            terminal_kind = 'ko'
                            _end_reason = f'P1_KO p1={p1h} p2={p2h}'
                        elif p2h is not None and p2h <= 0:
                            round_result = 'win'
                            terminal_kind = 'ko'
                            _end_reason = f'P2_KO p1={p1h} p2={p2h}'
                        elif (next_state.timer is not None and next_state.timer <= 0
                                and p1h is not None and p2h is not None):
                            # Timer expiry is a valid terminal outcome.
                            _timeout_outcome = True
                            terminal_kind = 'timer'
                            if p1h > p2h:
                                round_result = 'win'
                                _end_reason = f'TIMER_WIN p1={p1h} p2={p2h}'
                            elif p2h > p1h:
                                round_result = 'loss'
                                _end_reason = f'TIMER_LOSS p1={p1h} p2={p2h}'
                            else:
                                # Draws at timeout are treated as losses to
                                # discourage passive play.
                                round_result = 'loss'
                                _end_reason = f'TIMER_DRAW_AS_LOSS p1={p1h} p2={p2h}'
                        elif (p1h is not None and p1h >= 160
                                and p2h is not None and p2h >= 160):
                            invalid_reason = (
                                f'health_reset_transition p1={p1h} p2={p2h}'
                            )
                            _end_reason = invalid_reason
                        elif (p1h is None or p1h == 0) and (p2h is None or p2h == 0):
                            invalid_reason = (
                                f'both_zero_transition p1={p1h} p2={p2h}'
                            )
                            _end_reason = invalid_reason

                        if _end_reason:
                            terminal_reason = _end_reason
                            if _timeout_outcome and round_result in ('win', 'loss') and reward_buf:
                                terminal_reward = _terminal_reward(round_result, terminal_kind)
                                reward_buf[-1] += terminal_reward
                                if round_result == 'win':
                                    acc['win'] += terminal_reward
                                else:
                                    acc['loss'] += terminal_reward
                            print(
                                f'[worker-{worker_id}] EPISODE ENDED step={ep_steps} '
                                f'reason={_end_reason} timer={next_state.timer}',
                                flush=True,
                            )
                            round_ended = (round_result is not None and invalid_reason is None)
                            break

                    # If round-over triggers before combat starts, treat as invalid.
                    # This is usually a stale transition state, not a real rollout.
                    if not _fighting_started and ep_steps >= MIN_STEPS:
                        if tracer.is_round_over(next_state):
                            invalid_reason = (
                                f'prefight_round_over p1={p1h} p2={p2h} timer={next_state.timer}'
                            )
                            print(
                                f'[worker-{worker_id}] INVALID EPISODE step={ep_steps} '
                                f'reason={invalid_reason}',
                                flush=True,
                            )
                            break

                prev_state = next_state

            # Wall-clock timeout fallback: if no KO/timer terminal was observed,
            # resolve outcome by remaining HP so the rollout stays usable.
            if invalid_reason is None and round_result is None and _fighting_started and ep_steps >= MIN_STEPS:
                timeout_st = next_state or prev_state
                if (timeout_st is not None
                        and timeout_st.p1_health is not None
                        and timeout_st.p2_health is not None):
                    p1h = int(timeout_st.p1_health)
                    p2h = int(timeout_st.p2_health)
                    if p1h > p2h:
                        round_result = 'win'
                        timeout_reason = f'WALL_TIMEOUT_WIN p1={p1h} p2={p2h}'
                    elif p2h > p1h:
                        round_result = 'loss'
                        timeout_reason = f'WALL_TIMEOUT_LOSS p1={p1h} p2={p2h}'
                    else:
                        # Break ties against passivity.
                        round_result = 'loss'
                        timeout_reason = f'WALL_TIMEOUT_DRAW_AS_LOSS p1={p1h} p2={p2h}'

                    terminal_kind = 'wall_timeout'
                    terminal_reason = timeout_reason
                    terminal_reward = _terminal_reward(round_result, terminal_kind)
                    if reward_buf:
                        reward_buf[-1] += terminal_reward
                    if round_result == 'win':
                        acc['win'] += terminal_reward
                    else:
                        acc['loss'] += terminal_reward
                    round_ended = True
                    print(
                        f'[worker-{worker_id}] EPISODE ENDED step={ep_steps} '
                        f'reason={timeout_reason} timer={timeout_st.timer}',
                        flush=True,
                    )
                else:
                    invalid_reason = (
                        f'wall_timeout_missing_health p1='
                        f'{timeout_st.p1_health if timeout_st else None} '
                        f'p2={timeout_st.p2_health if timeout_st else None}'
                    )

            write_ctrl_worker(ControllerState(), ctrl_path)
            # Bug 1: neutralize P2 at clean episode end
            if ctrl_path_p2 is not None:
                write_ctrl_worker(ControllerState(), ctrl_path_p2)

            final_st = next_state or prev_state
            print(
                f'[worker-{worker_id}] attempt={attempt_idx} EPISODE END '
                f'steps={ep_steps} round_ended={round_ended} '
                f'fighting_started={_fighting_started} '
                f'result={round_result if round_result else "invalid"} '
                f'term={terminal_kind if terminal_kind else "n/a"} '
                f'p1={final_st.p1_health if final_st else "?"} '
                f'p2={final_st.p2_health if final_st else "?"} '
                f'wall={time.time()-ep_start:.1f}s',
                flush=True,
            )
            if invalid_reason is not None:
                rollout_queue.put({
                    'worker_id': worker_id,
                    'error': f'invalid_episode: {invalid_reason}',
                    'obs': [],
                    'acts': [],
                    'rewards': [],
                    'cpu_attacked': [],
                    'acc': acc,
                })
                continue
            if round_result not in ('win', 'loss'):
                rollout_queue.put({
                    'worker_id': worker_id,
                    'error': f'invalid_episode: missing_round_result steps={ep_steps}',
                    'obs': [],
                    'acts': [],
                    'rewards': [],
                    'cpu_attacked': [],
                    'acc': acc,
                })
                continue
            won       = (round_result == 'win')
            ep_frames = int((time.time() - ep_start) * 60)
            # Store final P2 health fraction for close-loss detection in learner
            if final_st and final_st.p2_health is not None:
                acc['p2_health_final'] = max(0.0, float(final_st.p2_health)) / HEALTH_MAX
            if final_st and final_st.p1_health is not None:
                acc['p1_health_final'] = max(0.0, float(final_st.p1_health)) / HEALTH_MAX
            if final_st and final_st.p1_x is not None:
                acc['p1_x_final'] = float(final_st.p1_x)
            if final_st and final_st.p2_x is not None:
                acc['p2_x_final'] = float(final_st.p2_x)
            if final_st and final_st.timer is not None:
                acc['timer_final'] = int(final_st.timer)
            if final_st and final_st.p1_x is not None and final_st.p2_x is not None:
                acc['dist_final'] = float(abs(final_st.p2_x - final_st.p1_x))

            # Episodes are terminal by KO, timer expiry, or wall-time HP fallback.
            truncated     = False
            bootstrap_val = 0.0

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

        # Guard against passive-opponent corruption in CPU-training mode:
        # if the fight ran long and we dealt major damage but took effectively none,
        # treat this rollout as invalid instead of training on a degenerate opponent.
        if (
            ctrl_path_p2 is None
            and ep_steps >= 600
            and acc.get('dealt_hp', 0.0) >= 80.0
            and acc.get('taken_hp', 0.0) <= 0.5
        ):
            msg = (
                f'cpu_passive_guard: steps={ep_steps} '
                f'dealt_hp={acc.get("dealt_hp", 0.0):.1f} '
                f'taken_hp={acc.get("taken_hp", 0.0):.1f}'
            )
            print(f'[worker-{worker_id}] {msg} — skipping rollout')
            rollout_queue.put({
                'worker_id': worker_id,
                'error': msg,
                'obs': [],
                'acts': [],
                'rewards': [],
                'cpu_attacked': [],
                'acc': acc,
            })
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
            'terminal_kind': terminal_kind,
            'terminal_reason': terminal_reason,
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
        hit_rate = (acc['hits'] / acc['attacks'] * 100) if acc['attacks'] > 0 else 0.0
        print(f'[worker-{worker_id}] valid={valid_episodes}/{episodes_per_worker}  steps={ep_steps}'
              f'  r={total_r:+.2f}  [dealt={acc["dealt"]:+.1f} taken={acc["taken"]:+.1f}'
              f' spam={acc["spam"]:+.1f}]  won={won_sym}'
              f'  trunc={truncated}  hit%={hit_rate:.0f}'
              f'  atk={acc["attacks"]} move={acc["moves"]} idle={acc["idles"]}')

    try:
        b.close()
    except Exception:
        pass
    completed = valid_episodes >= episodes_per_worker
    rollout_queue.put({
        'worker_id': worker_id,
        'done': True,
        'completed': completed,
        'valid_episodes': valid_episodes,
        'target_episodes': episodes_per_worker,
        'attempts': attempt_idx,
    })
    if completed:
        print(f'[worker-{worker_id}] finished all {episodes_per_worker} episodes')
    else:
        print(
            f'[worker-{worker_id}] INCOMPLETE: valid={valid_episodes}/{episodes_per_worker} '
            f'attempts={attempt_idx}/{max_attempts}',
            flush=True,
        )
