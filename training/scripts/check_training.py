#!/usr/bin/env python3
"""
check_training.py — One-shot training health dashboard.
Run:  python3 training/scripts/check_training.py
Shows: process status, socket health, episode stats, error counts, P1/P2 health
       tracking, bridge latency, and actionable warnings.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR  = N64_ROOT / 'training/data/logs'
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
AGENTS = ['lstm', 'obj_belief', 'transformer', 'disc_rssm']

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def ok(msg):   return f'{GREEN}OK{RESET}  {msg}'
def fail(msg): return f'{RED}FAIL{RESET}  {msg}'
def warn(msg): return f'{YELLOW}WARN{RESET}  {msg}'
def section(title): print(f'\n{BOLD}{CYAN}{"═"*60}{RESET}\n{BOLD}{CYAN}  {title}{RESET}\n{BOLD}{CYAN}{"═"*60}{RESET}')


def check_processes():
    """Check which training processes are alive."""
    section('1. PROCESS STATUS')
    checks = {
        'watchdog':       'watchdog.py',
        'mk4_train_parallel': 'mk4_train_parallel',
        'bridge_server':  'run_bridge_server',
        'mupen64plus':    'mupen64plus',
    }
    counts = {}
    for label, pattern in checks.items():
        result = subprocess.run(
            ['pgrep', '-f', pattern], capture_output=True, text=True
        )
        pids = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
        counts[label] = len(pids)
        status = ok(f'{len(pids)} running') if pids else fail('0 running')
        print(f'  {label:25s} {status}')
        # Show per-agent for trainers and emulators
        if label in ('mk4_train_parallel', 'mupen64plus', 'bridge_server') and pids:
            ps = subprocess.run(['ps', '-p', ','.join(pids), '-o', 'pid,etime,args'],
                                capture_output=True, text=True)
            for line in ps.stdout.strip().split('\n')[1:]:
                agent = 'unknown'
                for a in AGENTS:
                    if a in line:
                        agent = a
                        break
                pid_match = re.match(r'\s*(\d+)\s+(\S+)', line)
                if pid_match:
                    print(f'    pid={pid_match.group(1):>6s}  uptime={pid_match.group(2):>8s}  agent={agent}')

    # Expected counts
    warnings = []
    if counts.get('watchdog', 0) != 1:
        warnings.append(fail('Expected 1 watchdog'))
    if counts.get('mk4_train_parallel', 0) != 4:
        warnings.append(warn(f'Expected 4 trainers, got {counts.get("mk4_train_parallel", 0)}'))
    if counts.get('bridge_server', 0) != 4:
        warnings.append(warn(f'Expected 4 bridge servers, got {counts.get("bridge_server", 0)}'))
    if counts.get('mupen64plus', 0) < 4:
        warnings.append(warn(f'Expected 4+ emulators, got {counts.get("mupen64plus", 0)}'))
    if counts.get('mupen64plus', 0) > counts.get('bridge_server', 0) + 1:
        orphans = counts.get('mupen64plus', 0) - counts.get('bridge_server', 0)
        warnings.append(warn(f'{orphans} possibly orphaned emulators'))
    for w in warnings:
        print(f'  {w}')
    return counts


def check_sockets():
    """Check bridge sockets exist and accept connections."""
    section('2. BRIDGE SOCKETS')
    results = {}
    for agent in AGENTS:
        sock_path = BRIDGE_DIR / f'mk4-train-{agent}-0.sock'
        exists = sock_path.exists()
        connectable = False
        latency_ms = None
        if exists:
            try:
                t0 = time.time()
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect(str(sock_path))
                req = json.dumps({"id": "diag", "command": "HELLO", "payload": {}}) + "\n"
                s.sendall(req.encode())
                resp_line = s.makefile('r').readline()
                latency_ms = (time.time() - t0) * 1000
                s.close()
                resp = json.loads(resp_line)
                connectable = resp.get('ok', False)
            except Exception:
                connectable = False
                latency_ms = None

        results[agent] = {'exists': exists, 'connectable': connectable, 'latency_ms': latency_ms}
        if connectable:
            print(f'  {agent:20s} {ok(f"latency={latency_ms:.0f}ms")}')
        elif exists:
            print(f'  {agent:20s} {fail("socket exists but cannot connect")}')
        else:
            print(f'  {agent:20s} {fail("socket file missing")}')
    return results


def check_episodes():
    """Parse episode summary lines from agent logs."""
    section('3. EPISODE STATS')
    all_stats = {}
    for agent in AGENTS:
        log = LOG_DIR / f'arch-{agent}.log'
        if not log.exists():
            print(f'  {agent:20s} {fail("no log file")}')
            continue

        episodes = []
        with open(log) as f:
            for line in f:
                m = re.search(
                    r'valid=(\d+)/\d+\s+steps=(\d+)\s+r=([+-]?\d+\.?\d*)\s+'
                    r'\[dealt=([+-]?\d+\.?\d*)\s+taken=([+-]?\d+\.?\d*)\s+spam=([+-]?\d+\.?\d*)\]\s+'
                    r'won=(.)',
                    line
                )
                if m:
                    episodes.append({
                        'valid': int(m.group(1)),
                        'steps': int(m.group(2)),
                        'reward': float(m.group(3)),
                        'dealt': float(m.group(4)),
                        'taken': float(m.group(5)),
                        'spam': float(m.group(6)),
                        'won': m.group(7) == '\u2713',
                    })

        if not episodes:
            print(f'  {agent:20s} {warn("0 episodes completed")}')
            continue

        total = len(episodes)
        wins = sum(1 for e in episodes if e['won'])
        taken_nonzero = sum(1 for e in episodes if e['taken'] != 0.0)
        dealt_nonzero = sum(1 for e in episodes if e['dealt'] != 0.0)
        avg_steps = sum(e['steps'] for e in episodes) / total
        avg_reward = sum(e['reward'] for e in episodes) / total
        avg_dealt = sum(e['dealt'] for e in episodes) / total
        avg_taken = sum(e['taken'] for e in episodes) / total
        last5 = episodes[-5:]

        print(f'  {BOLD}{agent}{RESET}:')
        print(f'    episodes:     {total}')
        print(f'    wins:         {wins}/{total} ({100*wins/total:.1f}%)')
        print(f'    avg steps:    {avg_steps:.0f}')
        print(f'    avg reward:   {avg_reward:+.1f}')
        print(f'    avg dealt:    {avg_dealt:+.1f}')
        print(f'    avg taken:    {avg_taken:+.1f}')

        # P1 HEALTH BUG CHECK
        p1_pct = 100 * taken_nonzero / total if total else 0
        if taken_nonzero == 0:
            print(f'    {RED}P1 HEALTH:    taken=0 in ALL {total} eps — P1 NEVER takes damage!{RESET}')
        elif p1_pct < 20:
            print(f'    {YELLOW}P1 HEALTH:    taken!=0 in only {taken_nonzero}/{total} eps ({p1_pct:.0f}%){RESET}')
        else:
            print(f'    P1 HEALTH:    taken!=0 in {taken_nonzero}/{total} eps ({p1_pct:.0f}%) {GREEN}OK{RESET}')

        if dealt_nonzero == 0:
            print(f'    {RED}P2 HEALTH:    dealt=0 in ALL {total} eps — agent never hits!{RESET}')

        print(f'    last 5:')
        for e in last5:
            w = '\u2713' if e['won'] else '\u2717'
            t_col = RED if e['taken'] == 0 else ''
            t_end = RESET if e['taken'] == 0 else ''
            print(f'      steps={e["steps"]:>4d}  r={e["reward"]:+8.1f}  dealt={e["dealt"]:+7.1f}  '
                  f'{t_col}taken={e["taken"]:+7.1f}{t_end}  spam={e["spam"]:+7.1f}  won={w}')

        all_stats[agent] = {
            'total': total, 'wins': wins, 'taken_nonzero': taken_nonzero,
            'avg_steps': avg_steps, 'avg_reward': avg_reward,
        }
    return all_stats


def check_errors():
    """Count error types per agent log."""
    section('4. ERROR COUNTS')
    error_patterns = {
        'Connection refused': r'Connection refused',
        'BrokenPipe':         r'BrokenPipe',
        'Traceback':          r'Traceback',
        'health_poll_timeout': r'health_poll_timeout',
        'frame step failed':  r'frame step failed',
        'read failed':        r'read failed.*reloading',
        'ModuleNotFoundError': r'ModuleNotFoundError',
        'torch not found':    r"No module named 'torch'",
    }
    for agent in AGENTS:
        log = LOG_DIR / f'arch-{agent}.log'
        if not log.exists():
            continue
        counts = defaultdict(int)
        with open(log) as f:
            for line in f:
                for label, pattern in error_patterns.items():
                    if re.search(pattern, line):
                        counts[label] += 1

        nonzero = {k: v for k, v in counts.items() if v > 0}
        if not nonzero:
            print(f'  {agent:20s} {ok("no errors")}')
        else:
            total_err = sum(nonzero.values())
            status = fail(f'{total_err} total errors') if total_err > 100 else warn(f'{total_err} errors')
            print(f'  {agent:20s} {status}')
            for label, count in sorted(nonzero.items(), key=lambda x: -x[1]):
                severity = RED if count > 1000 else YELLOW if count > 10 else ''
                end = RESET if severity else ''
                print(f'    {severity}{label:25s} {count:>7d}{end}')


def check_health_trace():
    """Look for TRACE lines showing P1 health changes."""
    section('5. HEALTH TRACE SAMPLES')
    for agent in AGENTS:
        log = LOG_DIR / f'arch-{agent}.log'
        if not log.exists():
            continue
        traces = []
        with open(log) as f:
            for line in f:
                if 'TRACE step=' in line:
                    m = re.search(r'p1=(\d+)\s+p2=(\d+)\s+timer=(\d+)', line)
                    if m:
                        traces.append({
                            'p1': int(m.group(1)),
                            'p2': int(m.group(2)),
                            'timer': int(m.group(3)),
                        })

        if not traces:
            print(f'  {agent:20s} {warn("no TRACE lines")}')
            continue

        p1_changed = sum(1 for t in traces if t['p1'] != 160)
        p2_changed = sum(1 for t in traces if t['p2'] != 160)
        timer_zero = sum(1 for t in traces if t['timer'] == 0)
        timer_99   = sum(1 for t in traces if t['timer'] >= 99)

        print(f'  {BOLD}{agent}{RESET}: {len(traces)} trace samples')
        p1_pct = 100 * p1_changed / len(traces)
        p2_pct = 100 * p2_changed / len(traces)
        p1_status = f'{GREEN}OK{RESET}' if p1_pct > 10 else f'{RED}BUG{RESET}'
        p2_status = f'{GREEN}OK{RESET}' if p2_pct > 10 else f'{RED}BUG{RESET}'
        print(f'    P1 health < 160:  {p1_changed}/{len(traces)} ({p1_pct:.0f}%) {p1_status}')
        print(f'    P2 health < 160:  {p2_changed}/{len(traces)} ({p2_pct:.0f}%) {p2_status}')
        print(f'    timer=0 (timeout): {timer_zero}/{len(traces)} ({100*timer_zero/len(traces):.0f}%)')
        print(f'    timer>=99 (frozen): {timer_99}/{len(traces)} ({100*timer_99/len(traces):.0f}%)')

        p1_samples = [t for t in traces if t['p1'] != 160][:3]
        if p1_samples:
            print(f'    P1 damage samples: {p1_samples}')
        else:
            print(f'    {RED}P1 NEVER took damage in any trace — timer freeze needed!{RESET}')


def check_checkpoints():
    """Check if model checkpoints are being saved."""
    section('6. CHECKPOINTS')
    ckpt_dir = N64_ROOT / 'training/data/checkpoints'
    if not ckpt_dir.exists():
        print(f'  {fail("checkpoint directory missing")}')
        return
    for agent in AGENTS:
        matches = list(ckpt_dir.glob(f'*{agent}*'))
        if not matches:
            print(f'  {agent:20s} {warn("no checkpoints")}')
        else:
            newest = max(matches, key=lambda p: p.stat().st_mtime)
            age_min = (time.time() - newest.stat().st_mtime) / 60
            size_mb = newest.stat().st_size / 1024 / 1024
            if age_min > 30:
                print(f'  {agent:20s} {warn(f"{len(matches)} files, newest={newest.name} ({size_mb:.1f}MB, {age_min:.0f}min ago)")}')
            else:
                print(f'  {agent:20s} {ok(f"{len(matches)} files, newest={newest.name} ({size_mb:.1f}MB, {age_min:.0f}min ago)")}')


def check_heartbeats():
    """Check learner heartbeat files."""
    section('7. HEARTBEATS')
    for agent in AGENTS:
        hb = LOG_DIR / f'learner_heartbeat_{agent}'
        if not hb.exists():
            print(f'  {agent:20s} {warn("no heartbeat file")}')
        else:
            age = time.time() - hb.stat().st_mtime
            if age > 600:
                print(f'  {agent:20s} {fail(f"heartbeat {age:.0f}s old — learner may be hung")}')
            elif age > 120:
                print(f'  {agent:20s} {warn(f"heartbeat {age:.0f}s old")}')
            else:
                print(f'  {agent:20s} {ok(f"heartbeat {age:.0f}s ago")}')


def check_watchdog():
    """Check watchdog log."""
    section('8. WATCHDOG')
    log = LOG_DIR / 'watchdog.log'
    if not log.exists():
        print(f'  {fail("watchdog.log missing")}')
        return
    age = time.time() - log.stat().st_mtime
    size = log.stat().st_size
    print(f'  log size: {size/1024:.1f}KB, last modified {age:.0f}s ago')
    with open(log) as f:
        lines = f.readlines()
    for line in lines[-10:]:
        line = line.strip()
        if 'died' in line or 'hung' in line or 'restarting' in line:
            print(f'  {RED}{line}{RESET}')
        elif 'launched' in line:
            print(f'  {GREEN}{line}{RESET}')
        else:
            print(f'  {line}')


def summary(proc_counts, socket_results, episode_stats):
    """Print final summary with actionable items."""
    section('SUMMARY')
    issues = []

    if proc_counts.get('watchdog', 0) == 0:
        issues.append(('CRITICAL', 'Watchdog not running'))
    if proc_counts.get('mk4_train_parallel', 0) < 4:
        issues.append(('WARN', f'Only {proc_counts.get("mk4_train_parallel", 0)}/4 trainers running'))
    if proc_counts.get('mupen64plus', 0) > proc_counts.get('bridge_server', 0) + 1:
        orphans = proc_counts.get('mupen64plus', 0) - proc_counts.get('bridge_server', 0)
        issues.append(('WARN', f'{orphans} possibly orphaned emulators'))

    for agent, s in socket_results.items():
        if not s['connectable']:
            issues.append(('CRITICAL', f'{agent} bridge socket not connectable'))

    for agent, stats in episode_stats.items():
        if stats['total'] == 0:
            issues.append(('CRITICAL', f'{agent} has 0 completed episodes'))
        if stats['taken_nonzero'] == 0 and stats['total'] > 5:
            issues.append(('BUG', f'{agent}: P1 NEVER takes damage (taken=0 in all {stats["total"]} eps)'))

    if not issues:
        print(f'  {GREEN}{BOLD}All checks passed!{RESET}')
    else:
        for severity, msg in issues:
            if severity == 'CRITICAL':
                print(f'  {RED}{BOLD}[{severity}]{RESET} {msg}')
            elif severity == 'BUG':
                print(f'  {RED}[{severity}]{RESET} {msg}')
            else:
                print(f'  {YELLOW}[{severity}]{RESET} {msg}')

    print(f'\n  {BOLD}Quick fix:{RESET}')
    print(f'    bash training/scripts/start_training.sh   # full restart')


def main():
    print(f'{BOLD}\u2554{"═"*54}\u2557{RESET}')
    print(f'{BOLD}\u2551  MK4 Training Health Check  \u2014  {time.strftime("%Y-%m-%d %H:%M:%S")}  \u2551{RESET}')
    print(f'{BOLD}\u255a{"═"*54}\u255d{RESET}')

    proc_counts = check_processes()
    socket_results = check_sockets()
    episode_stats = check_episodes()
    check_errors()
    check_health_trace()
    check_checkpoints()
    check_heartbeats()
    check_watchdog()
    summary(proc_counts, socket_results, episode_stats)


if __name__ == '__main__':
    main()
