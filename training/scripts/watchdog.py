#!/usr/bin/env python3
"""
watchdog.py — Phase 2 self-play watchdog with worker-level health checks.

This watchdog launches `mk4_train_parallel.py` for each agent and restarts
an agent run when any of the following happens:
1) parent trainer exits
2) learner heartbeat goes stale
3) bridge/emulator worker count drops below expected workers
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = N64_ROOT / 'training/data/logs'
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
INSTANCE_DIR = N64_ROOT / '.m64p/instances'
SCRIPT = N64_ROOT / 'training/scripts/mk4_train_parallel.py'

_BREW_PY = '/opt/homebrew/bin/python3'
PYTHON = _BREW_PY if shutil.which(_BREW_PY) else shutil.which('python3') or 'python3'

# Phase 2 runs (3 agents; transformer removed).
AGENT_RUNS = [
    ('lstm', 'mk4_lstm_35k_w3_v1'),
    ('obj_belief', 'mk4_obj_belief_35k_w3_v1'),
    ('disc_rssm', 'mk4_disc_rssm_35k_w3_v1'),
]

WORKERS = int(os.environ.get('WD_WORKERS', '2'))
EPISODES = int(os.environ.get('WD_EPISODES', '52500'))
SAVE_EVERY = int(os.environ.get('WD_SAVE_EVERY', '10'))
BATCH_SIZE = int(os.environ.get('WD_BATCH_SIZE', '2'))
ROLLOUT_Q_MULT = int(os.environ.get('WD_ROLLOUT_Q_MULT', '16'))
TRACE_EVERY = int(os.environ.get('WD_TRACE_EVERY', '0'))
HEARTBEAT_MAX_AGE_SECS = float(os.environ.get('WD_HEARTBEAT_MAX_AGE', '300'))
WORKER_GRACE_SECS = float(os.environ.get('WD_WORKER_GRACE_SECS', '120'))
CHECK_EVERY_SECS = float(os.environ.get('WD_CHECK_EVERY_SECS', '20'))
LAUNCH_STAGGER_SECS = float(os.environ.get('WD_LAUNCH_STAGGER_SECS', '25'))
OPP_ROTATE_EVERY = int(os.environ.get('WD_OPP_ROTATE_EVERY', '30'))
SAVESTATE_NAME = os.environ.get('WD_SAVESTATE', 'p1p2_trainingscript.st')

agent_procs: dict[str, subprocess.Popen] = {}
agent_logfiles: dict[str, object] = {}
agent_started_at: dict[str, float] = {}


def _run_id(agent: str) -> str:
    for a, rid in AGENT_RUNS:
        if a == agent:
            return os.environ.get(f'WD_RUNID_{agent.upper()}', rid)
    return agent


def _proc_count(pattern: str) -> int:
    try:
        out = subprocess.check_output(['pgrep', '-f', pattern], text=True)
    except subprocess.CalledProcessError:
        return 0
    return len([ln for ln in out.splitlines() if ln.strip()])


def _kill_group(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass


def kill_stale(agent: str) -> None:
    run_id = _run_id(agent)
    _kill_group(agent_procs.get(agent))

    # Fallback process cleanup for orphan bridge/emulator processes.
    patterns = [
        rf'mk4_train_parallel\.py.*--run-id {run_id}\b',
        rf'run_bridge_server\.py.*mk4-train-{run_id}-',
        rf'mupen64plus.*train-{run_id}-',
    ]
    for pat in patterns:
        subprocess.run(['pkill', '-9', '-f', pat], capture_output=True)

    # Remove stale worker sockets for all worker indices.
    for sock in BRIDGE_DIR.glob(f'mk4-train-{run_id}-*.sock'):
        try:
            sock.unlink()
        except Exception:
            pass

    # Remove stale instance dirs for all worker indices.
    for cfg in INSTANCE_DIR.glob(f'train-{run_id}-*'):
        shutil.rmtree(cfg, ignore_errors=True)

    time.sleep(1.0)


def launch(agent: str) -> subprocess.Popen:
    run_id = _run_id(agent)
    kill_stale(agent)

    hb = LOG_DIR / f'learner_heartbeat_{run_id}'
    try:
        hb.unlink()
    except Exception:
        pass

    old_handle = agent_logfiles.get(agent)
    if old_handle is not None:
        try:
            old_handle.close()
        except Exception:
            pass

    log_file = open(LOG_DIR / f'arch-{run_id}.log', 'a')
    agent_logfiles[agent] = log_file

    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    env.setdefault('N64_PPO_EPOCHS', os.environ.get('WD_PPO_EPOCHS', '2'))
    opp_pool = ','.join([f'{a}:{_run_id(a)}' for a, _ in AGENT_RUNS])

    cmd = [
        PYTHON, str(SCRIPT),
        '--agent', agent,
        '--run-id', run_id,
        '--workers', str(WORKERS),
        '--episodes', str(EPISODES),
        '--save-every', str(SAVE_EVERY),
        '--batch-size', str(BATCH_SIZE),
        '--rollout-queue-mult', str(ROLLOUT_Q_MULT),
        '--speed-mode', 'TRAIN_TURBO',
        '--debugger-emumode', '2',
        '--disable-coach',
        '--trace-every', str(TRACE_EVERY),
        '--learner-device', 'mps',
        '--savestate', SAVESTATE_NAME,
        '--self-play-mode', 'on',
        '--opponent-pool', opp_pool,
        '--opponent-rotate-every', str(OPP_ROTATE_EVERY),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    agent_started_at[agent] = time.time()
    print(
        f'[watchdog] launched {agent} run_id={run_id} pid={proc.pid} '
        f'workers={WORKERS} batch={BATCH_SIZE} '
        f'opp_rotate={OPP_ROTATE_EVERY}'
    )
    return proc


def _heartbeat_stale(agent: str) -> bool:
    run_id = _run_id(agent)
    hb = LOG_DIR / f'learner_heartbeat_{run_id}'
    if not hb.exists():
        return False
    age = time.time() - hb.stat().st_mtime
    if age > HEARTBEAT_MAX_AGE_SECS:
        print(f'[watchdog] {agent} heartbeat stale ({age:.0f}s > {HEARTBEAT_MAX_AGE_SECS:.0f}s)')
        return True
    return False


def _workers_stale(agent: str) -> bool:
    # Let fresh launches boot before enforcing worker-count checks.
    start_ts = agent_started_at.get(agent, 0.0)
    if time.time() - start_ts < WORKER_GRACE_SECS:
        return False

    run_id = _run_id(agent)
    bridge_n = _proc_count(rf'run_bridge_server\.py.*mk4-train-{run_id}-')
    emu_n = _proc_count(rf'mupen64plus.*train-{run_id}-')
    if bridge_n < WORKERS or emu_n < WORKERS:
        print(
            f'[watchdog] {agent} worker-count unhealthy '
            f'(bridge={bridge_n}/{WORKERS}, emu={emu_n}/{WORKERS})'
        )
        return True
    return False


def main() -> None:
    agents = [a for a, _ in AGENT_RUNS]
    print('[watchdog] starting Phase 2 self-play watchdog')
    print(f'[watchdog] script={SCRIPT.name} workers={WORKERS} episodes={EPISODES} batch={BATCH_SIZE}')
    print(f'[watchdog] savestate={SAVESTATE_NAME} opp_rotate_every={OPP_ROTATE_EVERY}')
    print(f'[watchdog] heartbeat_timeout={HEARTBEAT_MAX_AGE_SECS:.0f}s worker_grace={WORKER_GRACE_SECS:.0f}s')

    time.sleep(2.0)
    for i, agent in enumerate(agents):
        agent_procs[agent] = launch(agent)
        if i < len(agents) - 1 and LAUNCH_STAGGER_SECS > 0:
            print(f'[watchdog] stagger sleep {LAUNCH_STAGGER_SECS:.0f}s before next launch')
            time.sleep(LAUNCH_STAGGER_SECS)

    print('[watchdog] all runs launched — monitoring...')
    while True:
        time.sleep(CHECK_EVERY_SECS)
        for agent in agents:
            proc = agent_procs.get(agent)
            dead = proc is None or proc.poll() is not None
            hung = (not dead) and _heartbeat_stale(agent)
            workers_bad = (not dead) and _workers_stale(agent)
            if dead or hung or workers_bad:
                reason = 'dead' if dead else ('hung' if hung else 'worker_drop')
                code = proc.returncode if (proc and proc.poll() is not None) else 'n/a'
                print(f'[watchdog] restarting {agent}: reason={reason} exit={code}')
                _kill_group(proc)
                time.sleep(1.0)
                agent_procs[agent] = launch(agent)


if __name__ == '__main__':
    main()
