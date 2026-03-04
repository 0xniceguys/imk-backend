#!/usr/bin/env python3
"""
mk4_train_parallel.py — Parallel MK4 Training with Multiple Emulator Instances

Launches N emulator instances simultaneously, each in TURBO (nospeedlimit) mode.
Supports running multiple concurrent architecture training jobs via --run-id isolation.

Usage:
    # Single architecture, 3 workers
    python3 mk4_train_parallel.py --agent lstm --workers 3 --episodes 100 --run-id lstm

    # Run all architectures simultaneously (via launch_all_archs.sh):
    # LSTM  job: --agent lstm  --run-id lstm  --workers 3
    # MLP   job: --agent mlp   --run-id mlp   --workers 3
    # Each gets completely isolated socket/ctrl/cfg/log paths
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]

# Use the same Python that is running this script (ensures torch is available)
PYTHON = sys.executable
sys.path.insert(0, str(N64_ROOT / 'training/src'))
sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

BRIDGE_DIR  = N64_ROOT / 'training/data/bridge'
def resolve_state_path() -> str:
    candidates = [
        N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/my_state.st',
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError(
        f'No savestate found. Tried: {[str(c) for c in candidates]}'
    )
ROM_PATH    = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN    = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB     = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN      = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR    = '/opt/homebrew/lib/mupen64plus'
DATA_DIR    = '/opt/homebrew/share/mupen64plus'


def socket_path(run_id: str, worker_id: int) -> str:
    return str(BRIDGE_DIR / f'mk4-train-{run_id}-{worker_id}.sock')


def ctrl_path(run_id: str, worker_id: int) -> str:
    return f'/tmp/mk4_ctrl_{run_id}_{worker_id}'


def cfg_dir(run_id: str, worker_id: int) -> str:
    return str(N64_ROOT / f'.m64p/instances/train-{run_id}-{worker_id}/config')


def launch_bridge(run_id: str, worker_id: int, log_path: Path) -> subprocess.Popen:
    """Launch one bridge server + emulator.
    Matches mk4_controller_debug.py launch exactly so we get a visible game window.
    Each worker gets its own ctrl file via N64TRAIN_CTRL_P1 env var.
    """
    sock = socket_path(run_id, worker_id)
    try: os.remove(sock)
    except: pass

    ctrl = ctrl_path(run_id, worker_id)

    # Always delete stale config dir to avoid mupen64plus startup hangs
    # from corrupted/stale mupen64plus.cfg written by previous killed runs.
    # NOTE: cfg_dir() returns a str, so we cast to Path for .exists()/.mkdir()
    # Safe for checkpoint continuation — config dir only holds mupen64plus
    # video/audio settings, NOT training weights (those live in data/checkpoints/).
    import shutil
    cfg = Path(cfg_dir(run_id, worker_id))
    if cfg.exists():
        shutil.rmtree(str(cfg))
    cfg.mkdir(parents=True, exist_ok=True)

    cmd = [
        PYTHON, str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path',         sock,
        '--instance-id',         f'train-{run_id}-{worker_id}',
        '--memory-reader',       'debugger-dump',
        '--rom-path',            ROM_PATH,
        '--debugger-ui-binary',  M64P_BIN,
        '--debugger-corelib',    CORELIB,
        '--debugger-plugindir',  PLUG_DIR,
        '--debugger-configdir',  cfg_dir(run_id, worker_id),
        '--debugger-datadir',    '/opt/homebrew/share/mupen64plus',  # must match working debug tool
        '--debugger-dump-dir',   str(N64_ROOT / 'training/data/bridge/debugger_dumps' / f'{run_id}_{worker_id}'),
        '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode',    '0',  # Pure Interpreter — stable (DynaRec crashes mupen64plus mid-game)
        '--speed-mode',          'DEBUG_VISIBLE',  # stable speed mode
        '--log-path',            str(log_path),
    ]

    # Per-worker ctrl file so buttons go to the RIGHT emulator
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = ctrl
    env['N64TRAIN_CTRL_P2'] = ctrl + '_p2'

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, 'w')  # 'w' not 'a' — fresh log each run
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
    return proc


def wait_for_socket(sock: str, timeout: float = 90.0) -> bool:
    """Wait until the bridge server socket is truly ready (accepts connections).
    The socket FILE appears early, but mupen64plus (via PTY) takes 10-30s to
    boot. We probe with a real connection+HELLO to confirm readiness.
    """
    import socket as _socket
    deadline = time.time() + timeout
    file_seen = False
    while time.time() < deadline:
        if not file_seen and os.path.exists(sock):
            file_seen = True
        if file_seen:
            # Try a real connection + HELLO
            try:
                s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(sock)
                s.close()
                return True  # connection accepted — server is up
            except (ConnectionRefusedError, OSError):
                pass
        time.sleep(0.5)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description='Parallel MK4 Training')
    ap.add_argument('--agent',
                    default='lstm', metavar='AGENT',
                    help='Architecture: mlp, lstm, gru, cont_rssm, disc_rssm,'
                         ' transformer, obj_belief, latent_planner')
    ap.add_argument('--run-id',   default=None, dest='run_id',
                    help='Unique ID for this training run (default: same as --agent). '
                         'Use different IDs to run multiple architectures simultaneously.')
    ap.add_argument('--workers',  type=int, default=3,   help='Parallel emulator instances (max 6)')
    ap.add_argument('--episodes', type=int, default=100, help='Episodes per worker')
    ap.add_argument('--save-every', type=int, default=10, dest='save_every')
    ap.add_argument('--batch-size', type=int, default=None, dest='batch_size')
    ap.add_argument('--dry-run',  action='store_true')
    args = ap.parse_args()

    run_id = args.run_id or args.agent   # e.g. 'lstm', 'mlp', 'lstm-v2'
    n_workers      = min(args.workers, 6)
    eps_per_worker = args.episodes
    total_eps      = n_workers * eps_per_worker
    state_path     = resolve_state_path()

    print(f'[parallel] Run ID       : {run_id}')
    print(f'[parallel] Agent        : {args.agent}')
    print(f'[parallel] Workers      : {n_workers}')
    print(f'[parallel] Episodes/w   : {eps_per_worker}  (total: {total_eps})')
    print(f'[parallel] Savestate    : {Path(state_path).name}')
    print(f'[parallel] Speed mode   : Pure Interpreter + DEBUG_VISIBLE (DynaRec disabled — crashes mupen64plus mid-game)')

    if args.dry_run:
        for i in range(n_workers):
            print(f'  Worker {i}:  sock={socket_path(run_id,i)}  ctrl={ctrl_path(run_id,i)}')
        return

    # ── Launch bridge servers ────────────────────────────────────────────────
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    bridge_procs: list[subprocess.Popen] = []
    log_dir = N64_ROOT / 'training/data/logs'

    print(f'\n[parallel] Launching {n_workers} emulator instances (run={run_id})...')
    for i in range(n_workers):
        log = log_dir / f'emulator-{run_id}-{i}.log'
        proc = launch_bridge(run_id, i, log)
        bridge_procs.append(proc)
        print(f'  [emulator-{run_id}-{i}] pid={proc.pid}  log={log}')
        time.sleep(1.0)

    print(f'\n[parallel] Waiting for emulator sockets...')
    for i in range(n_workers):
        sock = socket_path(run_id, i)
        if wait_for_socket(sock, timeout=60):
            print(f'  [emulator-{run_id}-{i}] socket ready')
        else:
            print(f'  [emulator-{run_id}-{i}] TIMEOUT — aborting')
            for p in bridge_procs: p.terminate()
            sys.exit(1)
    time.sleep(3.0)

    # ── Setup IPC queues ─────────────────────────────────────────────────────
    rollout_queue  = Queue(maxsize=n_workers * 4)
    weight_queues  = [Queue(maxsize=2) for _ in range(n_workers)]

    # ── Launch learner ───────────────────────────────────────────────────────
    from n64train.training.learner import run_learner
    learner_proc = Process(
        target=run_learner,
        args=(rollout_queue, weight_queues, n_workers, total_eps,
              args.save_every, args.batch_size or n_workers, args.agent,
              run_id),   # Bug 4+5: isolate heartbeat/log/stats per run_id
        daemon=False,
        name='learner',
    )
    learner_proc.start()
    print(f'\n[parallel] Learner started  pid={learner_proc.pid}')

    # Wait for learner to broadcast initial weights
    time.sleep(2.0)

    # ── Launch workers ───────────────────────────────────────────────────────
    from n64train.training.worker import run_worker
    from mk4_train import build_agent

    # ── Self-play: load frozen opponent from latest checkpoint ───────────────
    # Pass only the state_dict (plain tensor dict) instead of the full model
    # object to avoid pickling a PyTorch module into each worker process
    # (slow, large, and causes CUDA deserialization issues on GPU setups).
    # Workers rebuild the opponent agent locally from this dict.
    # ── Self-play: load frozen opponent from run-scoped checkpoint ─────────────
    # Build scoped path from agent's CKPT: mk4_policy.pt → mk4_policy_{run_id}.pt
    _tmp_opponent = build_agent(args.agent)
    _base = _tmp_opponent.CKPT
    _scoped_ckpt = _base.parent / f'{_base.stem}_{run_id}{_base.suffix}'
    if _scoped_ckpt.exists():
        try: _tmp_opponent.load(_scoped_ckpt)
        except Exception as e: print(f'[parallel] warn: could not load scoped ckpt {_scoped_ckpt.name}: {e}')
    frozen_opponent_weights = {k: v.cpu().clone() for k, v in _tmp_opponent.net.state_dict().items()}
    opp_ep = getattr(_tmp_opponent, "episode", 0)
    del _tmp_opponent
    print("[parallel] Self-play opponent: " + args.agent + " ep=" + str(opp_ep) + " frozen (state_dict transfer)")


    worker_procs: list[Process] = []
    for i in range(n_workers):
        p2_path = ctrl_path(run_id, i) + '_p2'
        p = Process(
            target=run_worker,
            args=(i, socket_path(run_id, i), ctrl_path(run_id, i),
                  rollout_queue, weight_queues[i],
                  eps_per_worker, state_path, args.agent,
                  p2_path, frozen_opponent_weights),    # pass state_dict, not model object
            daemon=False,
            name=f'worker-{run_id}-{i}',
        )
        p.start()
        worker_procs.append(p)
        print(f'[parallel] Worker {i} started  pid={p.pid}  self-play=✓  ctrl_p2={p2_path}')

    print(f'\n[parallel] All {n_workers} workers running. Training in progress...\n')

    # ── Wait for all processes to finish ─────────────────────────────────────
    try:
        for p in worker_procs:
            p.join()
        learner_proc.join()
    except KeyboardInterrupt:
        print('\n[parallel] Interrupted — shutting down...')
    finally:
        for p in worker_procs:
            if p.is_alive(): p.terminate()
        if learner_proc.is_alive(): learner_proc.terminate()
        for p in bridge_procs:
            p.terminate()
        print('[parallel] All processes stopped.')


if __name__ == '__main__':
    main()
