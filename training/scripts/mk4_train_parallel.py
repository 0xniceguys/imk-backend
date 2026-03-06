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


def resolve_learner_device(device_arg: str) -> str:
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


def resolve_state_path(savestate_arg: str | None = None) -> str:
    if savestate_arg:
        p = Path(savestate_arg)
        if not p.is_absolute():
            p = N64_ROOT / 'training/data/savestates/mk4_arcade' / savestate_arg
        if p.exists():
            return str(p)
        raise FileNotFoundError(f'Specified savestate not found: {p}')

    candidates = [
        N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2_trainingscript.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/my_state.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st',
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError(f'No savestate found. Tried: {[str(c) for c in candidates]}')


def parse_opponent_pool(
    spec: str,
    *,
    fallback_agent: str,
    fallback_run_id: str,
) -> list[dict[str, str]]:
    """
    Parse comma-separated `agent:run_id` entries.
    Example: 'lstm:mk4_lstm_phase2,obj_belief:mk4_obj_phase2,disc_rssm:mk4_disc_phase2'
    """
    text = (spec or '').strip()
    if not text:
        return [{'agent_type': fallback_agent, 'run_id': fallback_run_id}]

    entries: list[dict[str, str]] = []
    for raw in text.split(','):
        item = raw.strip()
        if not item:
            continue
        if ':' in item:
            agent_type, run_id = item.split(':', 1)
            agent_type = agent_type.strip()
            run_id = run_id.strip() or agent_type
        else:
            agent_type = item
            run_id = item
        if not agent_type:
            continue
        entries.append({'agent_type': agent_type, 'run_id': run_id})

    if not entries:
        return [{'agent_type': fallback_agent, 'run_id': fallback_run_id}]
    return entries


def resolve_self_play_mode(mode_arg: str, savestate_path: str) -> bool:
    """
    Decide whether to inject a P2 policy.
    Default (auto) keeps self-play ON.
    """
    mode = (mode_arg or 'auto').strip().lower()
    if mode == 'on':
        return True
    if mode == 'off':
        return False
    return True


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


def launch_bridge(
    run_id: str,
    worker_id: int,
    log_path: Path,
    *,
    speed_mode: str,
    debugger_emumode: int,
    debugger_gfx_plugin: str,
    enable_p2_controller: bool,
) -> subprocess.Popen:
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
        '--debugger-gfx-plugin',   debugger_gfx_plugin,
        '--debugger-audio-plugin', 'dummy',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode',    str(debugger_emumode),
        '--speed-mode',          speed_mode,
        '--log-path',            str(log_path),
    ]

    # Per-worker ctrl file so buttons go to the RIGHT emulator
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = ctrl
    if enable_p2_controller:
        env['N64TRAIN_CTRL_P2'] = ctrl + '_p2'
    else:
        # Leave P2 unset so input plugin marks controller 2 as not present.
        # This keeps MK4 CPU/engine ownership of P2 instead of creating a dummy pad.
        env.pop('N64TRAIN_CTRL_P2', None)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, 'w')  # 'w' not 'a' — fresh log each run
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    return proc


def wait_for_socket(sock: str, timeout: float = 90.0) -> bool:
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
    """Kill bridge + child emulator (entire process group)."""
    if proc.poll() is not None:
        return
    # Bridge is launched with start_new_session=True, so kill the whole group
    # to avoid leaving orphan mupen64plus processes behind.
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    # Fallback in case group kill missed the parent.
    if proc.poll() is None:
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
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


def launch_bridge_with_retries(
    run_id: str,
    worker_id: int,
    *,
    log_dir: Path,
    speed_mode: str,
    debugger_emumode: int,
    debugger_gfx_plugin: str,
    enable_p2_controller: bool,
    max_attempts: int = 3,
    ready_timeout_s: float = 90.0,
) -> subprocess.Popen:
    """Launch bridge and require a successful HELLO before continuing."""
    sock = socket_path(run_id, worker_id)
    log_path = log_dir / f'emulator-{run_id}-{worker_id}.log'
    last_error = "unknown"
    for attempt in range(1, max_attempts + 1):
        proc = launch_bridge(
            run_id,
            worker_id,
            log_path,
            speed_mode=speed_mode,
            debugger_emumode=debugger_emumode,
            debugger_gfx_plugin=debugger_gfx_plugin,
            enable_p2_controller=enable_p2_controller,
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
        time.sleep(2.0)

    raise RuntimeError(
        f'Bridge launch failed for worker {worker_id} after {max_attempts} attempts: {last_error}'
    )


def main() -> int:
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
    ap.add_argument('--rollout-queue-mult', type=int, default=8,
                    help='Rollout queue size multiplier per worker')
    ap.add_argument('--speed-mode', default='TRAIN_TURBO',
                    choices=['DEBUG_VISIBLE', 'TRAIN_TURBO', 'EVAL_DETERMINISTIC'],
                    help='Bridge speed mode')
    ap.add_argument('--debugger-emumode', type=int, default=2, choices=[0, 1, 2],
                    help='Mupen mode: 0=Pure Interpreter, 1=Interpreter, 2=DynaRec')
    ap.add_argument('--debugger-gfx-plugin', default='mupen64plus-video-rice.dylib',
                    help='Debugger gfx plugin')
    ap.add_argument('--learner-device', default='auto', choices=['auto', 'cpu', 'mps', 'cuda'],
                    help='Learner update device; workers remain CPU')
    ap.add_argument('--disable-coach', action='store_true',
                    help='Disable LLM reward coach for speed/stability')
    ap.add_argument('--trace-every', type=int, default=0,
                    help='Worker TRACE print interval in steps (0 disables TRACE)')
    ap.add_argument('--savestate', default='p1p2_trainingscript.st',
                    help='Savestate filename in training/data/savestates/mk4_arcade, or absolute path')
    ap.add_argument('--opponent-pool', default='',
                    help='Comma-separated opponent checkpoints as agent:run_id entries')
    ap.add_argument('--opponent-rotate-every', type=int, default=30,
                    help='Rotate opponent every N valid episodes per worker')
    ap.add_argument('--self-play-mode', default='auto', choices=['auto', 'on', 'off'],
                    help='P2 control injection mode (auto=on)')
    ap.add_argument('--dry-run',  action='store_true')
    args = ap.parse_args()

    run_id = args.run_id or args.agent   # e.g. 'lstm', 'mlp', 'lstm-v2'
    n_workers      = min(args.workers, 6)
    eps_per_worker = args.episodes
    total_eps      = n_workers * eps_per_worker
    batch_size     = args.batch_size if args.batch_size is not None else max(2, n_workers)
    learner_device = resolve_learner_device(args.learner_device)
    state_path     = resolve_state_path(args.savestate)
    self_play_enabled = resolve_self_play_mode(args.self_play_mode, state_path)
    os.environ['N64TRACE_EVERY'] = str(max(0, int(args.trace_every)))
    opponent_pool = parse_opponent_pool(
        args.opponent_pool,
        fallback_agent=args.agent,
        fallback_run_id=run_id,
    )
    opponent_rotation = None
    if self_play_enabled:
        opponent_rotation = {
            'entries': opponent_pool,
            'rotate_every': max(1, int(args.opponent_rotate_every)),
        }

    print(f'[parallel] Run ID       : {run_id}')
    print(f'[parallel] Agent        : {args.agent}')
    print(f'[parallel] Workers      : {n_workers}')
    print(f'[parallel] Episodes/w   : {eps_per_worker}  (total: {total_eps})')
    print(f'[parallel] Batch size   : {batch_size}')
    print(f'[parallel] Savestate    : {Path(state_path).name}')
    print(f'[parallel] Speed mode   : {args.speed_mode}')
    print(f'[parallel] Emu mode     : {args.debugger_emumode}')
    print(f'[parallel] GFX plugin   : {args.debugger_gfx_plugin}')
    print(f'[parallel] Learner dev  : {learner_device}')
    print(f'[parallel] Coach        : {"OFF" if args.disable_coach else "ON"}')
    print(f'[parallel] TRACE every  : {os.environ["N64TRACE_EVERY"]} step(s)')
    if self_play_enabled and opponent_rotation is not None:
        print(f'[parallel] Self-play    : ON')
        print(f'[parallel] Opp rotate   : every {opponent_rotation["rotate_every"]} eps')
        print(
            '[parallel] Opp pool     : '
            + ', '.join([f'{x["agent_type"]}:{x["run_id"]}' for x in opponent_pool])
        )
    else:
        print(f'[parallel] Self-play    : OFF (single-controller mode for {Path(state_path).name})')

    if args.dry_run:
        for i in range(n_workers):
            print(f'  Worker {i}:  sock={socket_path(run_id,i)}  ctrl={ctrl_path(run_id,i)}')
        return 0

    # ── Launch bridge servers ────────────────────────────────────────────────
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    bridge_procs: list[subprocess.Popen] = []
    log_dir = N64_ROOT / 'training/data/logs'

    print(f'\n[parallel] Launching {n_workers} emulator instances (run={run_id})...')
    try:
        for i in range(n_workers):
            proc = launch_bridge_with_retries(
                run_id,
                i,
                log_dir=log_dir,
                speed_mode=args.speed_mode,
                debugger_emumode=args.debugger_emumode,
                debugger_gfx_plugin=args.debugger_gfx_plugin,
                enable_p2_controller=self_play_enabled,
            )
            bridge_procs.append(proc)
    except Exception as exc:
        print(f'[parallel] FATAL: {exc}')
        for p in bridge_procs:
            _terminate_process(p)
        return 1
    time.sleep(2.0)

    # ── Setup IPC queues ─────────────────────────────────────────────────────
    rollout_q_size = max(n_workers * 2, n_workers * int(args.rollout_queue_mult))
    rollout_queue  = Queue(maxsize=rollout_q_size)
    print(f'[parallel] Rollout q    : {rollout_q_size}')
    weight_queues  = [Queue(maxsize=2) for _ in range(n_workers)]

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
    print(f'\n[parallel] Learner started  pid={learner_proc.pid}')

    # Wait for learner to broadcast initial weights
    time.sleep(2.0)

    # ── Launch workers ───────────────────────────────────────────────────────
    from n64train.training.worker import run_worker
    worker_procs: list[Process] = []
    for i in range(n_workers):
        p2_path = (ctrl_path(run_id, i) + '_p2') if self_play_enabled else None
        p = Process(
            target=run_worker,
            args=(i, socket_path(run_id, i), ctrl_path(run_id, i),
                  rollout_queue, weight_queues[i],
                  eps_per_worker, state_path, args.agent,
                  p2_path, None, opponent_rotation),
            daemon=False,
            name=f'worker-{run_id}-{i}',
        )
        p.start()
        worker_procs.append(p)
        if self_play_enabled and opponent_rotation is not None:
            print(
                f'[parallel] Worker {i} started  pid={p.pid}  self-play=✓  '
                f'ctrl_p2={p2_path}  rotate_every={opponent_rotation["rotate_every"]}'
            )
        else:
            print(f'[parallel] Worker {i} started  pid={p.pid}  self-play=off (P2 unmanaged)')

    print(f'\n[parallel] All {n_workers} workers running. Training in progress...\n')

    # ── Wait for all processes to finish ─────────────────────────────────────
    exit_code = 0
    try:
        while True:
            dead_bridges = [
                (idx, proc.poll())
                for idx, proc in enumerate(bridge_procs)
                if proc.poll() is not None
            ]
            if dead_bridges:
                for idx, rc in dead_bridges:
                    log_path = log_dir / f'emulator-{run_id}-{idx}.log'
                    tail = _tail_log(log_path)
                    print(
                        f'[parallel] FATAL: bridge worker {idx} exited unexpectedly '
                        f'(rc={rc}). Tail:\n{tail}'
                    )
                exit_code = 1
                break

            # Fatal if any worker exits non-zero.
            worker_failures = [
                (idx, p.exitcode)
                for idx, p in enumerate(worker_procs)
                if (not p.is_alive()) and (p.exitcode not in (None, 0))
            ]
            if worker_failures:
                for idx, rc in worker_failures:
                    print(f'[parallel] FATAL: worker {idx} exited unexpectedly (rc={rc})')
                exit_code = 1
                break

            workers_alive = any(p.is_alive() for p in worker_procs)
            learner_alive = learner_proc.is_alive()
            if not learner_alive and workers_alive:
                rc = learner_proc.exitcode
                print(f'[parallel] FATAL: learner exited while workers are still running (rc={rc})')
                exit_code = 1 if rc in (None, 0) else int(rc)
                break
            if not workers_alive and not learner_alive:
                if learner_proc.exitcode not in (None, 0):
                    print(f'[parallel] FATAL: learner exited with rc={learner_proc.exitcode}')
                    exit_code = 1
                break
            time.sleep(2.0)
    except KeyboardInterrupt:
        print('\n[parallel] Interrupted — shutting down...')
    finally:
        for p in worker_procs:
            if p.is_alive():
                p.terminate()
            p.join(timeout=2.0)
        if learner_proc.is_alive():
            learner_proc.terminate()
        learner_proc.join(timeout=2.0)
        for p in bridge_procs:
            _terminate_process(p)
        print('[parallel] All processes stopped.')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
