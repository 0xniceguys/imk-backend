#!/usr/bin/env python3
"""
mk4_train_parallel_cpu.py — Phase 1: Train vs In-Game CPU (Scorpion)

PHASE 1 TRAINING — Agent learns by fighting MK4's built-in CPU AI.

Key differences from mk4_train_parallel.py (self-play):
  - Savestate: arcade_training_scorpion.st  (arcade mode, Scorpion vs CPU)
  - P1 only:   Agent controls Scorpion. P2 ctrl file is NEVER written.
               MK4's own CPU AI drives P2 automatically.
  - No self-play: No frozen opponent, no opponent state_dict, no P2 injection.
  - Episodes: 25000 per worker (configurable via --episodes)

Phase 2 (self-play) uses mk4_train_parallel.py, which loads Phase 1
checkpoints as the starting opponent. The scoped checkpoint filenames
(mk4_disc_rssm_disc_rssm.pt etc.) are identical across both phases so
continuation is seamless.

Usage:
    # Via watchdog (recommended):
    python3 training/scripts/watchdog_cpu.py

    # Direct single agent:
    python3 training/scripts/mk4_train_parallel_cpu.py \\
        --agent disc_rssm --run-id disc_rssm --workers 1 --episodes 25000
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))
sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

BRIDGE_DIR  = N64_ROOT / 'training/data/bridge'

# ── Phase 1 savestate: Arcade mode — P1 (Scorpion) vs CPU AI ─────────────────
STATE_PATH  = str(N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st')

ROM_PATH    = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN    = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB     = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN      = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR    = '/opt/homebrew/lib/mupen64plus'
DATA_DIR    = '/opt/homebrew/share/mupen64plus'


def resolve_learner_device(device_arg: str) -> str:
    """Resolve auto device selection for learner-side updates."""
    if device_arg != 'auto':
        return device_arg
    try:
        import torch
        if bool(torch.backends.mps.is_available()):
            return 'mps'
        if bool(torch.cuda.is_available()):
            return 'cuda'
    except Exception:
        pass
    return 'cpu'


def socket_path(run_id: str, worker_id: int) -> str:
    return str(BRIDGE_DIR / f'mk4-train-{run_id}-{worker_id}.sock')


def ctrl_path(run_id: str, worker_id: int) -> str:
    return f'/tmp/mk4_ctrl_{run_id}_{worker_id}'


def cfg_dir(run_id: str, worker_id: int) -> str:
    return str(N64_ROOT / f'.m64p/instances/train-{run_id}-{worker_id}/config')


def launch_bridge(
    run_id: str,
    worker_id: int,
    log_path: Path,
    *,
    speed_mode: str,
    debugger_emumode: int,
    debugger_gfx_plugin: str,
) -> subprocess.Popen:
    """Launch one bridge server + emulator.

    Phase 1 note: N64TRAIN_CTRL_P2 is set in env (mupen64plus requires it)
    but workers will NOT write to it — the game's CPU AI drives P2 natively.
    """
    sock = socket_path(run_id, worker_id)
    try: os.remove(sock)
    except: pass

    ctrl = ctrl_path(run_id, worker_id)

    import shutil
    cfg = Path(cfg_dir(run_id, worker_id))
    if cfg.exists():
        shutil.rmtree(str(cfg))
    cfg.mkdir(parents=True, exist_ok=True)

    cmd = [
        '/opt/homebrew/bin/python3', str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path',         sock,
        '--instance-id',         f'train-{run_id}-{worker_id}',
        '--memory-reader',       'debugger-dump',
        '--rom-path',            ROM_PATH,
        '--debugger-ui-binary',  M64P_BIN,
        '--debugger-corelib',    CORELIB,
        '--debugger-plugindir',  PLUG_DIR,
        '--debugger-configdir',  cfg_dir(run_id, worker_id),
        '--debugger-datadir',    '/opt/homebrew/share/mupen64plus',
        '--debugger-dump-dir',   str(N64_ROOT / 'training/data/bridge/debugger_dumps' / f'{run_id}_{worker_id}'),
        '--debugger-gfx-plugin',   debugger_gfx_plugin,
        '--debugger-audio-plugin', 'dummy',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode',    str(debugger_emumode),
        '--speed-mode',          speed_mode,
        '--log-path',            str(log_path),
    ]

    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = ctrl
    # Arcade mode: game's CPU AI drives P2 natively — no P2 interception.

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, 'w')
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    log_file.close()   # Popen inherited the fd; close parent's copy to avoid leak
    return proc


def wait_for_socket(sock: str, timeout: float = 120.0) -> bool:
    """Wait until the bridge server accepts a real protocol HELLO."""
    from n64train.runtime.bridge import SocketEmulatorBridge

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not os.path.exists(sock):
            time.sleep(0.5)
            continue
        probe = SocketEmulatorBridge(sock, timeout_sec=5.0)
        try:
            _ = probe.hello()
            return True
        except Exception:
            time.sleep(0.5)
        finally:
            try:
                probe.close()
            except Exception:
                pass
    return False


def _terminate_process(proc: subprocess.Popen, *, grace_s: float = 5.0) -> None:
    """Kill bridge + its child emulator (entire process group).

    Uses SIGKILL on the process group — no grace period for SIGTERM since
    mupen64plus doesn't handle signals cleanly and we need guaranteed cleanup.
    """
    if proc.poll() is not None:
        return
    # SIGKILL the entire process group immediately — bridge uses
    # start_new_session=True, so the group contains both bridge_server.py
    # AND its child mupen64plus.
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    # Fallback: direct kill if group kill missed it
    if proc.poll() is None:
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    # Wait for reaping
    try:
        proc.wait(timeout=grace_s)
    except Exception:
        pass


def _tail_log(path: Path, *, max_chars: int = 1200) -> str:
    if not path.exists():
        return "<log missing>"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"<log unreadable: {exc}>"
    if not text:
        return "<log empty>"
    return text[-max_chars:]


def _nuke_orphans(run_id: str, worker_id: int) -> None:
    """Kill ALL processes related to this run_id/worker_id — bridge + emulator.

    This is the nuclear option: pkill by pattern to catch anything that
    _terminate_process missed (orphaned mupen64plus, zombie bridges, etc.).
    """
    instance_id = f'train-{run_id}-{worker_id}'
    # Kill any bridge_server.py with matching instance-id
    subprocess.run(
        ['pkill', '-9', '-f', f'--instance-id.*{instance_id}'],
        capture_output=True)
    # Kill any mupen64plus with matching configdir
    subprocess.run(
        ['pkill', '-9', '-f', f'train-{run_id}-{worker_id}'],
        capture_output=True)
    # Remove stale socket
    sock = socket_path(run_id, worker_id)
    try:
        os.remove(sock)
    except OSError:
        pass
    time.sleep(1)  # let OS reclaim PIDs and ports


def launch_bridge_with_retries(
    run_id: str,
    worker_id: int,
    *,
    log_dir: Path,
    speed_mode: str,
    debugger_emumode: int,
    debugger_gfx_plugin: str,
    max_attempts: int = 3,
    ready_timeout_s: float = 90.0,
) -> subprocess.Popen:
    """Launch bridge and require a successful HELLO before continuing."""
    sock = socket_path(run_id, worker_id)
    log_path = log_dir / f'emulator-{run_id}-{worker_id}.log'
    last_error = "unknown"
    for attempt in range(1, max_attempts + 1):
        # Nuke any orphans from previous attempts BEFORE launching
        if attempt > 1:
            _nuke_orphans(run_id, worker_id)

        proc = launch_bridge(
            run_id,
            worker_id,
            log_path,
            speed_mode=speed_mode,
            debugger_emumode=debugger_emumode,
            debugger_gfx_plugin=debugger_gfx_plugin,
        )
        if wait_for_socket(sock, timeout=ready_timeout_s):
            print(
                f'  [emulator-{run_id}-{worker_id}] ready on attempt '
                f'{attempt}/{max_attempts} (pid={proc.pid})'
            )
            return proc

        rc = proc.poll()
        _terminate_process(proc)
        tail = _tail_log(log_path)
        last_error = (
            f'bridge failed readiness probe (attempt {attempt}/{max_attempts}, '
            f'pid={proc.pid}, rc={rc})\n{tail}'
        )
        print(f'  [emulator-{run_id}-{worker_id}] {last_error}')

    raise RuntimeError(
        f'Bridge launch failed for worker {worker_id} after {max_attempts} attempts: {last_error}'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description='Phase 1: MK4 CPU Training')
    ap.add_argument('--agent',
                    default='lstm', metavar='AGENT',
                    help='Architecture: mlp, lstm, gru, cont_rssm, disc_rssm,'
                         ' transformer, obj_belief, latent_planner')
    ap.add_argument('--run-id',   default=None, dest='run_id',
                    help='Unique ID for this training run (default: same as --agent)')
    ap.add_argument('--workers',  type=int, default=1,     help='Parallel emulator instances')
    ap.add_argument('--episodes', type=int, default=25000, help='Episodes per worker (Phase 1 default: 25000)')
    ap.add_argument('--save-every', type=int, default=25, dest='save_every',
                    help='Save checkpoint every N episodes (default: 25 for long runs)')
    ap.add_argument('--batch-size', type=int, default=None, dest='batch_size')
    ap.add_argument('--disable-coach', action='store_true',
                    help='Disable Bedrock LLM reward coach to avoid network-induced pauses')
    ap.add_argument('--rollout-queue-mult', type=int, default=8,
                    help='Rollout queue size multiplier per worker (higher reduces backpressure stalls)')
    ap.add_argument('--speed-mode', default='TRAIN_TURBO',
                    choices=['DEBUG_VISIBLE', 'TRAIN_TURBO', 'EVAL_DETERMINISTIC'],
                    help='Bridge speed mode (TRAIN_TURBO is fastest)')
    ap.add_argument('--debugger-emumode', type=int, default=0, choices=[0, 1, 2],
                    help='Mupen mode: 0=Pure Interpreter, 1=Interpreter, 2=DynaRec')
    ap.add_argument('--debugger-gfx-plugin', default='mupen64plus-video-rice.dylib',
                    help='Debugger gfx plugin (dummy may be faster but can be less stable)')
    ap.add_argument('--learner-device', default='auto', choices=['auto', 'cpu', 'mps', 'cuda'],
                    help='Learner update device; workers remain CPU')
    ap.add_argument('--trace-every', type=int, default=100,
                    help='Worker TRACE print interval in steps (0 disables TRACE)')
    ap.add_argument('--dry-run',  action='store_true')
    args = ap.parse_args()

    run_id         = args.run_id or args.agent
    n_workers      = min(args.workers, 6)
    eps_per_worker = args.episodes
    total_eps      = n_workers * eps_per_worker
    # With workers=2, defaulting to batch=2 reduces update latency and stale-policy windows.
    batch_size     = args.batch_size if args.batch_size is not None else max(2, n_workers)
    learner_device = resolve_learner_device(args.learner_device)
    os.environ['N64TRACE_EVERY'] = str(max(0, int(args.trace_every)))

    print(f'[cpu-train] ═══════════════════════════════════════')
    print(f'[cpu-train] PHASE 1 — CPU Training (Scorpion vs AI)')
    print(f'[cpu-train] ═══════════════════════════════════════')
    print(f'[cpu-train] Run ID       : {run_id}')
    print(f'[cpu-train] Agent        : {args.agent}')
    print(f'[cpu-train] Workers      : {n_workers}')
    print(f'[cpu-train] Episodes/w   : {eps_per_worker}  (total: {total_eps})')
    print(f'[cpu-train] Savestate    : arcade_training_scorpion.st')
    print(f'[cpu-train] Self-play    : DISABLED (P2 = MK4 built-in AI)')
    print(f'[cpu-train] Save every   : {args.save_every} episodes')
    print(f'[cpu-train] Batch size   : {batch_size}')
    print(f'[cpu-train] Speed mode   : {args.speed_mode}')
    print(f'[cpu-train] Emu mode     : {args.debugger_emumode}')
    print(f'[cpu-train] GFX plugin   : {args.debugger_gfx_plugin}')
    print(f'[cpu-train] Learner dev  : {learner_device}')
    print(f'[cpu-train] Coach        : {"OFF" if args.disable_coach else "ON"}')
    print(f'[cpu-train] TRACE every  : {os.environ["N64TRACE_EVERY"]} step(s)')

    if args.dry_run:
        for i in range(n_workers):
            print(f'  Worker {i}:  sock={socket_path(run_id,i)}  ctrl={ctrl_path(run_id,i)}')
        return 0

    # ── Launch bridge servers ────────────────────────────────────────────────
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    bridge_procs: list[subprocess.Popen] = []
    log_dir = N64_ROOT / 'training/data/logs'

    print(f'\n[cpu-train] Launching {n_workers} emulator instance(s) (run={run_id})...')
    try:
        for i in range(n_workers):
            proc = launch_bridge_with_retries(
                run_id,
                i,
                log_dir=log_dir,
                speed_mode=args.speed_mode,
                debugger_emumode=args.debugger_emumode,
                debugger_gfx_plugin=args.debugger_gfx_plugin,
            )
            bridge_procs.append(proc)
    except Exception as exc:
        print(f'[cpu-train] FATAL: {exc}')
        for p in bridge_procs:
            _terminate_process(p)
        return 1
    time.sleep(2.0)

    # ── Setup IPC queues ─────────────────────────────────────────────────────
    rollout_q_size = max(n_workers * 2, n_workers * int(args.rollout_queue_mult))
    rollout_queue = Queue(maxsize=rollout_q_size)
    print(f'[cpu-train] Rollout q    : {rollout_q_size}')
    weight_queues = [Queue(maxsize=2) for _ in range(n_workers)]

    # ── Launch learner ───────────────────────────────────────────────────────
    from n64train.training.learner import run_learner
    learner_proc = Process(
        target=run_learner,
        args=(rollout_queue, weight_queues, n_workers, total_eps,
              args.save_every, batch_size, args.agent,
              run_id, learner_device, args.disable_coach),
        daemon=False,
        name='learner',
    )
    learner_proc.start()
    print(f'\n[cpu-train] Learner started  pid={learner_proc.pid}')
    time.sleep(2.0)

    # ── Launch workers (NO self-play — P2 is the in-game CPU AI) ─────────────
    from n64train.training.worker import run_worker

    worker_procs: list[Process] = []
    for i in range(n_workers):
        p = Process(
            target=run_worker,
            args=(
                i,
                socket_path(run_id, i),
                ctrl_path(run_id, i),
                rollout_queue,
                weight_queues[i],
                eps_per_worker,
                STATE_PATH,
                args.agent,
                None,   # no P2 ctrl — game CPU AI drives P2
                None,   # no opponent agent
            ),
            daemon=False,
            name=f'worker-{run_id}-{i}',
        )
        p.start()
        worker_procs.append(p)
        print(f'[cpu-train] Worker {i} started  pid={p.pid}  (P2=CPU AI, no self-play)')

    print(f'\n[cpu-train] All {n_workers} worker(s) running. Phase 1 training in progress...\n')

    # ── SIGTERM handler — clean up child process groups on kill ──────────────
    _shutdown = False

    def _handle_term(signum, frame):
        nonlocal _shutdown
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_term)

    # ── Wait for all processes ────────────────────────────────────────────────
    exit_code = 0
    try:
        while not _shutdown:
            # Restart any dead bridges instead of exiting
            for idx, proc in enumerate(bridge_procs):
                if proc.poll() is not None:
                    rc = proc.returncode
                    log_path = log_dir / f'emulator-{run_id}-{idx}.log'
                    tail = _tail_log(log_path)
                    print(
                        f'[cpu-train] bridge {idx} died (rc={rc}), restarting... '
                        f'Tail:\n{tail}'
                    )
                    try:
                        bridge_procs[idx] = launch_bridge_with_retries(
                            run_id,
                            idx,
                            log_dir=log_dir,
                            speed_mode=args.speed_mode,
                            debugger_emumode=args.debugger_emumode,
                            debugger_gfx_plugin=args.debugger_gfx_plugin,
                        )
                    except Exception as exc:
                        print(f'[cpu-train] FATAL: could not restart bridge {idx}: {exc}')
                        exit_code = 1
                        _shutdown = True
                        break

            # Fatal if any worker exits non-zero (crash / hard failure).
            worker_failures = [
                (idx, p.exitcode)
                for idx, p in enumerate(worker_procs)
                if (not p.is_alive()) and (p.exitcode not in (None, 0))
            ]
            if worker_failures:
                for idx, rc in worker_failures:
                    print(f'[cpu-train] FATAL: worker {idx} exited unexpectedly (rc={rc})')
                exit_code = 1
                _shutdown = True
                break

            workers_alive = any(p.is_alive() for p in worker_procs)
            learner_alive = learner_proc.is_alive()
            if not learner_alive and workers_alive:
                rc = learner_proc.exitcode
                print(f'[cpu-train] FATAL: learner exited while workers are still running (rc={rc})')
                exit_code = 1 if rc in (None, 0) else int(rc)
                _shutdown = True
                break
            if not workers_alive and not learner_alive:
                if learner_proc.exitcode not in (None, 0):
                    print(f'[cpu-train] FATAL: learner exited with rc={learner_proc.exitcode}')
                    exit_code = 1
                break
            time.sleep(2.0)
    except KeyboardInterrupt:
        print('\n[cpu-train] Interrupted — shutting down...')
    finally:
        for p in worker_procs:
            if p.is_alive():
                p.terminate()
            p.join(timeout=2.0)
        if learner_proc.is_alive():
            learner_proc.terminate()
        learner_proc.join(timeout=2.0)
        for p in bridge_procs:
            _terminate_process(p)  # kills entire process group (bridge + emulator)
        # Clean up socket files
        for idx in range(n_workers):
            sock = socket_path(run_id, idx)
            try:
                Path(sock).unlink()
            except Exception:
                pass
        print('[cpu-train] All processes stopped.')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
