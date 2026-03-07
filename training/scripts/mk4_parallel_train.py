#!/usr/bin/env python3
"""
mk4_parallel_train.py — Parallel MK4 training launcher.

Spawns N emulator+worker pairs feeding a single GPU learner.
Each worker runs its own mupen64plus instance via run_bridge_server.py.

Usage:
    python3 training/scripts/mk4_parallel_train.py --agent lstm --workers 8 --episodes 200
    python3 training/scripts/mk4_parallel_train.py --agent lstm --workers auto  # uses all cores
    python3 training/scripts/mk4_parallel_train.py --smoke  # 2 workers, 10 episodes

Resources on this machine:
    CPU: Ryzen 9 7900X  24 threads
    RAM: 62 GB
    GPU: RTX 4090 24 GB  (learner runs here)

Optimal workers: 16  (leaves 8 threads for learner + OS)
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'training' / 'src'))
sys.path.insert(0, str(ROOT / 'training' / 'scripts'))

# ── Constants ──────────────────────────────────────────────────────────────────
ROM_PATH       = ROOT / 'Mortal Kombat 4 (USA).z64'
INPUT_PLUGIN   = ROOT / 'vendor' / 'n64train-input' / 'n64train-input.so'
BRIDGE_SCRIPT  = ROOT / 'training' / 'scripts' / 'run_bridge_server.py'
VENV_PYTHON    = ROOT / 'training' / '.venv' / 'bin' / 'python3'
PYTHON         = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
SOCK_DIR       = Path('/tmp/mk4_train_socks')
STATE_DIR      = ROOT / 'training' / 'data' / 'savestates' / 'mk4_arcade'
# Best general-purpose savestate for training (verified working)
DEFAULT_STATE  = str(STATE_DIR / 'p1p2_trainingscript.st')
MAX_WORKERS    = 16   # safe ceiling for 24-thread Ryzen 9 7900X


def _all_savestates() -> list[str]:
    # Only use p1p2_trainingscript.st — the verified round-start save.
    # Other p1p2_*.st files have health=0 (end-of-fight state) and fail
    # the worker health gate. Training script save has the bypass.
    trainingscript = STATE_DIR / 'p1p2_trainingscript.st'
    if trainingscript.exists():
        return [str(trainingscript)]
    return [str(p) for p in sorted(STATE_DIR.glob('p1p2*.st'))]


def _bridge_sock(worker_id: int) -> str:
    return str(SOCK_DIR / f'worker_{worker_id}.sock')


def _ctrl_path(worker_id: int) -> str:
    return f'/tmp/mk4_ctrl_{worker_id}'


def _ctrl_path_p2(worker_id: int) -> str:
    return f'/tmp/mk4_ctrl_p2_{worker_id}'


# ── Bridge / emulator launcher ─────────────────────────────────────────────────

GFX_PLUGIN_HEADED  = 'mupen64plus-video-rice'
GFX_PLUGIN_HEADLESS = 'dummy'


def launch_bridge(worker_id: int, headed: bool = False, display: str = ':0') -> subprocess.Popen:
    """Start run_bridge_server.py which launches mupen64plus internally."""
    sock = _bridge_sock(worker_id)
    instance_id = f'train_{worker_id:02d}'

    env = dict(os.environ)
    env['DISPLAY'] = display if headed else f':9{worker_id}'  # Xvfb display per worker
    env['M64P_CTRL_PATH']    = _ctrl_path(worker_id)
    env['M64P_CTRL_PATH_P2'] = _ctrl_path_p2(worker_id)

    gfx = GFX_PLUGIN_HEADED if headed else GFX_PLUGIN_HEADLESS

    cmd = [
        PYTHON, str(BRIDGE_SCRIPT),
        '--socket-path',           sock,
        '--instance-id',           instance_id,
        '--speed-mode',            'TRAIN_TURBO',
        '--launch-emulator',
        '--memory-reader',         'debugger-dump',
        '--rom-path',              str(ROM_PATH),
        '--debugger-input-plugin', str(INPUT_PLUGIN),
        '--debugger-gfx-plugin',   gfx,
        '--resolution',            '320x240',
    ]
    log_path = ROOT / 'training' / 'data' / 'logs' / f'bridge_{instance_id}.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, 'w')
    proc = subprocess.Popen(cmd, env=env, stdout=log_f, stderr=log_f)
    print(f'  [launcher] worker-{worker_id} bridge pid={proc.pid}  sock={sock}')
    return proc


def wait_for_sock(sock_path: str, timeout: float = 60.0) -> bool:
    """Block until Unix socket file appears (emulator ready)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if Path(sock_path).exists():
            return True
        time.sleep(0.5)
    return False


# ── Worker process ─────────────────────────────────────────────────────────────

def run_worker_process(
    worker_id: int,
    sock_path: str,
    ctrl_path: str,
    rollout_queue: mp.Queue,
    weight_queue: mp.Queue,
    episodes_per_worker: int,
    savestate_path: str,
    agent_type: str,
) -> None:
    """Worker entry point — runs inside a subprocess.Process."""
    sys.path.insert(0, str(ROOT / 'training' / 'src'))
    sys.path.insert(0, str(ROOT / 'training' / 'scripts'))

    from n64train.training.worker import run_worker
    run_worker(
        worker_id=worker_id,
        sock_path=sock_path,
        ctrl_path=ctrl_path,
        rollout_queue=rollout_queue,
        weight_queue=weight_queue,
        episodes_per_worker=episodes_per_worker,
        savestate_path=savestate_path,
        agent_type=agent_type,
    )


# ── Learner ────────────────────────────────────────────────────────────────────

def run_learner_process(
    rollout_queue: mp.Queue,
    weight_queues: list[mp.Queue],
    n_workers: int,
    total_episodes: int,
    agent_type: str,
    run_id: str,
    device: str,
) -> None:
    sys.path.insert(0, str(ROOT / 'training' / 'src'))
    sys.path.insert(0, str(ROOT / 'training' / 'scripts'))
    from n64train.training.learner import run_learner
    run_learner(
        rollout_queue=rollout_queue,
        weight_queues=weight_queues,
        n_workers=n_workers,
        total_episodes=total_episodes,
        save_every=max(1, total_episodes // 20),
        batch_size=max(1, n_workers // 2),
        agent_type=agent_type,
        run_id=run_id,
        learner_device=device,
        disable_coach=False,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Parallel MK4 training')
    parser.add_argument('--agent',    default='lstm',
                        choices=['mlp','lstm','gru','cont_rssm','disc_rssm',
                                 'transformer','obj_belief','latent_planner'],
                        help='Agent architecture to train')
    parser.add_argument('--workers',  default='auto',
                        help='Number of parallel workers (int or "auto")')
    parser.add_argument('--episodes', type=int, default=500,
                        help='Total training episodes across all workers')
    parser.add_argument('--device',   default='cuda',
                        choices=['cuda','cpu'], help='Learner device')
    parser.add_argument('--headed',   action='store_true',
                        help='Show emulator windows (uses DISPLAY=:0)')
    parser.add_argument('--smoke',    action='store_true',
                        help='Quick smoke test: 2 workers, 10 episodes')
    parser.add_argument('--run-id',   default='',
                        help='Unique run identifier (auto-generated if not set)')
    args = parser.parse_args()

    if args.smoke:
        args.workers  = '2'
        args.episodes = 10
        args.device   = 'cpu'    # smoke test doesn't need GPU
        print('[launcher] SMOKE TEST MODE: 2 workers, 10 episodes, cpu')

    # ── Resolve worker count ───────────────────────────────────────────────────
    if args.workers == 'auto':
        cpu_count = mp.cpu_count()   # 24 on this machine
        # Leave 4 threads for learner + OS; each worker needs ~1 core
        n_workers = min(MAX_WORKERS, max(1, cpu_count - 4))
    else:
        n_workers = min(MAX_WORKERS, int(args.workers))

    run_id  = args.run_id or f'{args.agent}_{n_workers}w_{int(time.time()) % 100000}'
    ep_per_worker = max(1, args.episodes // n_workers)
    total_eps     = ep_per_worker * n_workers

    print(f'\n{"="*60}')
    print(f'  MK4 Parallel Training')
    print(f'  agent={args.agent}  workers={n_workers}  episodes={total_eps}')
    print(f'  device={args.device}  run_id={run_id}')
    print(f'{"="*60}\n')

    SOCK_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up stale sockets from previous runs
    for old in SOCK_DIR.glob('worker_*.sock'):
        old.unlink(missing_ok=True)

    # ── Launch N Xvfb displays (one per worker, unless headed) ────────────────
    xvfb_procs: list[subprocess.Popen] = []
    if not args.headed:
        for i in range(n_workers):
            disp_num = 90 + i
            xvfb = subprocess.Popen(
                ['Xvfb', f':{disp_num}', '-screen', '0', '320x240x24'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            xvfb_procs.append(xvfb)
        print(f'[launcher] Started {n_workers} Xvfb displays (:90–:{89+n_workers})')
        time.sleep(1.0)   # give Xvfb a moment

    # ── Launch N bridge servers (each starts its own mupen64plus) ─────────────
    states = _all_savestates()
    bridge_procs: list[subprocess.Popen] = []
    for i in range(n_workers):
        proc = launch_bridge(i, headed=args.headed)
        bridge_procs.append(proc)

    print(f'\n[launcher] Waiting for {n_workers} emulators to boot...')
    ready = 0
    for i in range(n_workers):
        sock = _bridge_sock(i)
        if wait_for_sock(sock, timeout=90.0):
            ready += 1
            print(f'  worker-{i} ready  ({ready}/{n_workers})')
        else:
            print(f'  worker-{i} TIMEOUT — check training/data/logs/bridge_train_{i:02d}.log')
            _cleanup(bridge_procs, xvfb_procs, [])
            sys.exit(1)

    # ── Build queues ───────────────────────────────────────────────────────────
    ctx = mp.get_context('spawn')
    rollout_queue: mp.Queue = ctx.Queue(maxsize=n_workers * 4)
    weight_queues: list[mp.Queue] = [ctx.Queue(maxsize=2) for _ in range(n_workers)]

    # ── Start worker processes ─────────────────────────────────────────────────
    worker_procs: list[mp.Process] = []
    for i in range(n_workers):
        state = states[i % len(states)] if states else DEFAULT_STATE
        p = ctx.Process(
            target=run_worker_process,
            args=(
                i,
                _bridge_sock(i),
                _ctrl_path(i),
                rollout_queue,
                weight_queues[i],
                ep_per_worker,
                state,
                args.agent,
            ),
            daemon=True,
        )
        p.start()
        worker_procs.append(p)

    print(f'[launcher] {n_workers} workers started. Running learner on {args.device}...\n')

    # ── Run learner on main process ────────────────────────────────────────────
    try:
        run_learner_process(
            rollout_queue=rollout_queue,
            weight_queues=weight_queues,
            n_workers=n_workers,
            total_episodes=total_eps,
            agent_type=args.agent,
            run_id=run_id,
            device=args.device,
        )
    finally:
        print('\n[launcher] Training done. Cleaning up...')
        _cleanup(bridge_procs, xvfb_procs, worker_procs)


def _cleanup(
    bridge_procs: list[subprocess.Popen],
    xvfb_procs: list[subprocess.Popen],
    worker_procs: list[mp.Process],
) -> None:
    for p in worker_procs:
        if p.is_alive():
            p.terminate()
    for p in bridge_procs + xvfb_procs:
        try:
            p.terminate()
        except Exception:
            pass
    # Kill any stray mupen64plus instances from this run
    subprocess.run(['pkill', '-f', 'mupen64plus.*mk4_train'],
                   capture_output=True)
    for sock in SOCK_DIR.glob('worker_*.sock'):
        sock.unlink(missing_ok=True)


if __name__ == '__main__':
    mp.set_start_method('spawn', force=True)
    main()
