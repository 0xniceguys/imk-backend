#!/usr/bin/env python3
"""
watchdog_cpu.py — Phase 1 Watchdog: monitors 4 agents training vs in-game CPU.

Identical to watchdog.py except:
  - Uses mk4_train_parallel_cpu.py (CPU training, no self-play)
  - Episodes: 25000 per agent (Phase 1 target)
  - Save every: 25 episodes (more frequent checkpoints for long runs)

Phase 1 goal: train until win rate >85% consistently for 100+ episodes.
Then switch to Phase 2 (self-play) via start_training.sh + watchdog.py.
"""
import subprocess, time, os
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR  = N64_ROOT / 'training/data/logs'
SCRIPT   = N64_ROOT / 'training/scripts/mk4_train_parallel_cpu.py'
AGENTS   = ['lstm', 'obj_belief', 'transformer', 'disc_rssm']
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'

# Phase 1: 25000 episodes each
EPISODES_PER_AGENT = 25000  # Phase 1 target (20000–30000 range)
SAVE_EVERY         = 10     # checkpoint every 10 episodes

HEARTBEAT_MAX_AGE_SECS = 600.0  # 10 min — covers long PPO updates on CPU

agent_procs:    dict[str, subprocess.Popen] = {}
agent_logfiles: dict[str, object] = {}


def kill_stale(agent: str) -> None:
    # Kill by stored PID first (process group = bridge + mupen64plus)
    proc = agent_procs.get(agent)
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:
            pass
    # Fallback: pkill patterns to catch ALL orphans
    subprocess.run(['pkill', '-9', '-f', f'mk4-train-{agent}-0'], capture_output=True)
    subprocess.run(['pkill', '-9', '-f', f'--run-id.*{agent}'], capture_output=True)
    subprocess.run(['pkill', '-9', '-f', f'train-{agent}-0'], capture_output=True)
    time.sleep(2)   # wait for processes to actually die
    # Clean up stale socket file (root cause of ECONNREFUSED on restart)
    sock = BRIDGE_DIR / f'mk4-train-{agent}-0.sock'
    try: sock.unlink()
    except Exception: pass
    cfg = N64_ROOT / f'.m64p/instances/train-{agent}-0'
    subprocess.run(['rm', '-rf', str(cfg)], capture_output=True)
    time.sleep(1)


def launch(agent: str) -> subprocess.Popen:
    kill_stale(agent)
    hb = LOG_DIR / f'learner_heartbeat_{agent}'
    try: hb.unlink()
    except: pass
    log = LOG_DIR / f'arch-{agent}.log'
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'

    old_handle = agent_logfiles.get(agent)
    if old_handle is not None:
        try: old_handle.close()
        except Exception: pass

    log_file = open(log, 'a')
    agent_logfiles[agent] = log_file

    proc = subprocess.Popen(
        ['/opt/homebrew/bin/python3', str(SCRIPT),
         '--agent', agent,
         '--run-id', agent,
         '--workers', '1',
         '--episodes', str(EPISODES_PER_AGENT),
         '--save-every', str(SAVE_EVERY)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    print(f'[watchdog-cpu] launched {agent} pid={proc.pid}  ({EPISODES_PER_AGENT} eps, save/{SAVE_EVERY})')
    return proc


def _heartbeat_stale(agent: str) -> bool:
    hb = LOG_DIR / f'learner_heartbeat_{agent}'
    if not hb.exists():
        return False
    age = time.time() - hb.stat().st_mtime
    if age > HEARTBEAT_MAX_AGE_SECS:
        print(f'[watchdog-cpu] {agent} heartbeat is {age:.0f}s old — learner appears hung')
        return True
    return False


def main() -> None:
    print('[watchdog-cpu] ═══════════════════════════════════════')
    print('[watchdog-cpu] PHASE 1 — CPU Training (4 agents)')
    print('[watchdog-cpu] ═══════════════════════════════════════')
    print(f'[watchdog-cpu] Script   : mk4_train_parallel_cpu.py')
    print(f'[watchdog-cpu] Agents   : {AGENTS}')
    print(f'[watchdog-cpu] Episodes : {EPISODES_PER_AGENT} per agent')
    print(f'[watchdog-cpu] Save/N   : every {SAVE_EVERY} episodes')
    print(f'[watchdog-cpu] Savestate: arcade_training_scorpion.st')
    print()

    time.sleep(2)
    for i, agent in enumerate(AGENTS):
        agent_procs[agent] = launch(agent)
        if i < len(AGENTS) - 1:
            print(f'[watchdog-cpu] waiting 60s before next agent launch...')
            time.sleep(60)

    completed = set()
    print('[watchdog-cpu] all 4 agents launched — monitoring...')
    while True:
        time.sleep(30)
        for agent in AGENTS:
            if agent in completed:
                continue
            proc = agent_procs.get(agent)
            dead = proc is None or proc.poll() is not None
            hung = (not dead) and _heartbeat_stale(agent)

            # Check if agent completed its episodes (clean exit = returncode 0)
            if dead and proc and proc.returncode == 0:
                print(f'[watchdog-cpu] {agent} COMPLETED {EPISODES_PER_AGENT} episodes cleanly ✓')
                completed.add(agent)
                continue

            if dead or hung:
                code = proc.returncode if (proc and proc.poll() is not None) else 'hung'
                reason = 'died' if dead else 'hung'
                print(f'[watchdog-cpu] {agent} {reason} (exit={code}), restarting...')
                if hung:
                    try: proc.kill()
                    except Exception: pass
                time.sleep(3)
                agent_procs[agent] = launch(agent)

        if len(completed) == len(AGENTS):
            print('[watchdog-cpu] All agents completed training ✓')
            break


if __name__ == '__main__':
    main()
