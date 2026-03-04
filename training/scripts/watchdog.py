#!/usr/bin/env python3
"""
watchdog.py — Monitors the 4 training agents and auto-restarts any that die.
Run with: python3 training/scripts/watchdog.py
Leave running in background.

Hang detection: the learner process writes a heartbeat timestamp to
  training/data/logs/learner_heartbeat  before each rollout_queue.get().
If the file is older than HEARTBEAT_MAX_AGE_SECS the process is considered
hung (live but blocked) and is killed + relaunched.
"""
import shutil
import subprocess, time, os
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]

# Use the Homebrew Python that has torch installed (not Xcode's Python 3.9)
_BREW_PY = '/opt/homebrew/bin/python3'
PYTHON = _BREW_PY if shutil.which(_BREW_PY) else shutil.which('python3') or 'python3'
LOG_DIR  = N64_ROOT / 'training/data/logs'
SCRIPT   = N64_ROOT / 'training/scripts/mk4_train_parallel.py'
AGENTS   = ['lstm', 'obj_belief', 'transformer', 'disc_rssm']
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'

HEARTBEAT_MAX_AGE_SECS = 600.0  # allow up to 10 min for long PPO updates / checkpoint saves on CPU

# pids of currently running agents
agent_procs:    dict[str, subprocess.Popen] = {}
agent_logfiles: dict[str, object] = {}  # track open log file handles to avoid fd leak


def kill_stale(agent: str) -> None:
    """Kill stale bridge/emulator processes for this agent."""
    subprocess.run(['pkill', '-9', '-f', f'train-{agent}'], capture_output=True)
    sock = BRIDGE_DIR / f'mk4-train-{agent}-0.sock'
    try: sock.unlink()
    except: pass
    cfg = N64_ROOT / f'.m64p/instances/train-{agent}-0'
    subprocess.run(['rm', '-rf', str(cfg)], capture_output=True)
    time.sleep(1)


def launch(agent: str) -> subprocess.Popen:
    kill_stale(agent)
    # Bug 4: remove only THIS agent's heartbeat so peer agents aren't affected
    hb = LOG_DIR / f'learner_heartbeat_{agent}'
    try: hb.unlink()
    except: pass
    log = LOG_DIR / f'arch-{agent}.log'
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'

    # Close the previous log handle for this agent (file handle leak fix)
    old_handle = agent_logfiles.get(agent)
    if old_handle is not None:
        try: old_handle.close()
        except Exception: pass

    log_file = open(log, 'a')
    agent_logfiles[agent] = log_file

    proc = subprocess.Popen(
        [PYTHON, str(SCRIPT),
         '--agent', agent, '--run-id', agent,
         '--workers', '1', '--episodes', '9999'],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    print(f'[watchdog] launched {agent} pid={proc.pid}')
    return proc


def _heartbeat_stale(agent: str) -> bool:
    """Return True if THIS agent's heartbeat is older than HEARTBEAT_MAX_AGE_SECS.
    Bug 4: each agent writes to its own file (learner_heartbeat_{run_id}),
    so a healthy lstm can no longer mask a hung disc_rssm."""
    hb = LOG_DIR / f'learner_heartbeat_{agent}'
    if not hb.exists():
        return False
    age = time.time() - hb.stat().st_mtime
    if age > HEARTBEAT_MAX_AGE_SECS:
        print(f'[watchdog] {agent} heartbeat is {age:.0f}s old — learner appears hung')
        return True
    return False


def main() -> None:
    print('[watchdog] starting — monitoring 4 agents')
    time.sleep(2)  # let any previous processes settle
    for i, agent in enumerate(AGENTS):
        agent_procs[agent] = launch(agent)
        if i < len(AGENTS) - 1:
            # Stagger launches: mupen64plus takes 10-30s to boot fully.
            # 60s gap ensures each emulator is socket-ready before the next starts,
            # preventing simultaneous boot contention that causes ConnectionRefused.
            print(f'[watchdog] waiting 60s before next agent launch...')
            time.sleep(60)

    print('[watchdog] all 4 agents running — watching...')
    while True:
        time.sleep(30)
        for agent in AGENTS:
            proc = agent_procs.get(agent)
            dead = proc is None or proc.poll() is not None
            hung = (not dead) and _heartbeat_stale(agent)
            if dead or hung:
                code = proc.returncode if (proc and proc.poll() is not None) else 'hung'
                print(f'[watchdog] {agent} {"died" if dead else "hung"} (exit={code}), restarting...')
                if hung:
                    try: proc.kill()
                    except Exception: pass
                time.sleep(3)
                agent_procs[agent] = launch(agent)


if __name__ == '__main__':
    main()
