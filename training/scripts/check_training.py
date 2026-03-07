#!/usr/bin/env python3
"""
check_training.py — Training health check for mk4_train_parallel.py runs.

Usage:
    # Check all 3 agents (default)
    python training/scripts/check_training.py

    # Check one agent
    python training/scripts/check_training.py --run-ids lstm

    # Live mode (refresh every 5s)
    python training/scripts/check_training.py --live
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR  = N64_ROOT / 'training/data/logs'
CKPT_DIR = N64_ROOT / 'training/data/checkpoints'

# Agent → checkpoint stem mapping
CKPT_STEMS = {
    'lstm':        'mk4_lstm_policy',
    'disc_rssm':   'mk4_disc_rssm',
    'obj_belief':  'mk4_obj_belief',
    'mlp':         'mk4_policy',
    'gru':         'mk4_gru_policy',
    'transformer': 'mk4_transformer',
}

G = '\033[92m'; Y = '\033[93m'; R = '\033[91m'; B = '\033[94m'; BOLD = '\033[1m'; X = '\033[0m'

def ok(s):   return f'{G}OK   {X}{s}'
def warn(s): return f'{Y}WARN {X}{s}'
def fail(s): return f'{R}FAIL {X}{s}'
def info(s): return f'{B}INFO {X}{s}'


# ─── emulator logs ────────────────────────────────────────────────────────────

def check_emulator_logs(run_id: str, n_workers: int) -> list[str]:
    out = []
    for i in range(n_workers):
        path = LOG_DIR / f'emulator-{run_id}-{i}.log'
        if not path.exists():
            out.append(fail(f'emulator-{i}: log missing'))
            continue
        text = path.read_text(errors='replace')
        if not text:
            # Bridge server runs silently — empty log is normal during active training
            out.append(ok(f'emulator-{i}: running (silent — bridge logs via socket)'))
            continue
        lines = text.splitlines()
        errors = [l for l in lines if any(k in l.lower() for k in ('error', 'fatal', 'exception', 'crashed'))]
        frame_fails = text.count('frame step failed')
        last = lines[-1][:100] if lines else ''
        if errors and frame_fails == 0:
            out.append(warn(f'emulator-{i}: {len(errors)} error lines  last="{last}"'))
        elif frame_fails > 0:
            out.append(warn(f'emulator-{i}: {frame_fails} frame-step-failures (recovered)  last="{last}"'))
        else:
            out.append(ok(f'emulator-{i}: {len(text)} bytes  last="{last}"'))
    return out


# ─── heartbeat ────────────────────────────────────────────────────────────────

def check_heartbeat(run_id: str) -> list[str]:
    hb = LOG_DIR / f'learner_heartbeat_{run_id}'
    if not hb.exists():
        return [fail('heartbeat missing — learner crashed at init or never started')]
    try:
        age = time.time() - float(hb.read_text().strip())
    except Exception:
        age = time.time() - hb.stat().st_mtime
    if age < 30:
        return [ok(f'heartbeat {age:.0f}s ago — learner alive')]
    if age < 120:
        return [warn(f'heartbeat {age:.0f}s ago — slow update or stuck')]
    return [fail(f'heartbeat {age:.0f}s ago — learner likely DEAD')]


# ─── training JSONL ───────────────────────────────────────────────────────────

def check_training_log(run_id: str) -> list[str]:
    path = LOG_DIR / f'mk4_training_log_{run_id}.jsonl'
    if not path.exists():
        return [fail('training log missing — no episodes completed yet')]

    eps = []
    for line in path.read_text().splitlines():
        try:
            eps.append(json.loads(line))
        except Exception:
            pass

    n = len(eps)
    if n == 0:
        return [fail('training log empty')]

    out = []
    wins     = sum(1 for e in eps if e.get('won'))
    wr       = wins / n * 100
    rewards  = [e['reward'] for e in eps]
    steps    = [e.get('steps', 0) for e in eps]
    tkind    = Counter(e.get('terminal_kind') for e in eps)
    avg_r    = sum(rewards) / n
    avg_step = sum(steps) / n

    out.append(ok(f'{n} episodes  win={wr:.1f}%  avg_reward={avg_r:+.1f}  avg_steps={avg_step:.0f}'))
    out.append(info(f'terminals: {dict(tkind)}'))

    # Reward trend
    if n >= 10:
        first10 = sum(rewards[:10]) / 10
        last10  = sum(rewards[-10:]) / 10
        delta   = last10 - first10
        if delta > 5:
            out.append(ok(f'reward trend: {first10:+.1f} → {last10:+.1f} (+{delta:.1f}) IMPROVING'))
        elif delta < -10:
            out.append(warn(f'reward trend: {first10:+.1f} → {last10:+.1f} ({delta:.1f}) DECLINING'))
        else:
            out.append(info(f'reward trend: {first10:+.1f} → {last10:+.1f} (flat so far)'))

    # SPAM ratio — the big one
    recent = eps[-min(20, n):]
    avg_spam  = sum(e.get('r_spam', 0) for e in recent) / len(recent)
    avg_dealt_r = sum(e.get('r_dealt', 0) for e in recent) / len(recent)
    avg_dealt_hp = sum(e.get('dealt_hp', 0) for e in recent) / len(recent)
    avg_taken_hp = sum(e.get('taken_hp', 0) for e in recent) / len(recent)
    avg_approach = sum(e.get('r_approach', 0) for e in recent) / len(recent)

    if avg_dealt_hp > 0:
        spam_ratio = abs(avg_spam) / avg_dealt_hp
        spam_msg   = f'spam={avg_spam:+.1f}  dealt_hp={avg_dealt_hp:.1f}  ratio={spam_ratio:.0%}'
        if spam_ratio > 0.8:
            out.append(fail(f'SPAM eating {spam_ratio:.0%} of damage — button mashing: {spam_msg}'))
        elif spam_ratio > 0.5:
            out.append(warn(f'spam high ({spam_ratio:.0%} of damage): {spam_msg}'))
        else:
            out.append(ok(f'spam ratio OK ({spam_ratio:.0%}): {spam_msg}'))
    else:
        out.append(warn(f'avg dealt_hp=0 — agent not landing hits yet'))

    out.append(info(f'last-20 avg: dealt_hp={avg_dealt_hp:.1f}  taken_hp={avg_taken_hp:.1f}  '
                    f'approach={avg_approach:+.2f}  spam={avg_spam:+.2f}'))

    # Hit rate from recent episodes
    recent_hit = [e for e in recent if 'r_dealt' in e]
    if recent_hit:
        all_positive_r = sum(1 for e in recent_hit if e.get('r_dealt', 0) > 0)
        out.append(info(f'eps with damage dealt: {all_positive_r}/{len(recent_hit)} of last {len(recent_hit)}'))

    # Zero step episodes
    bad = sum(1 for s in steps if s < 2)
    if bad:
        out.append(fail(f'{bad} zero-step episodes — rollout collection broken'))

    # Win rate concern
    if n >= 20 and wr < 20:
        out.append(warn(f'win rate {wr:.1f}% after {n} eps (expected — random policy early on)'))
    elif wr > 60 and n >= 20:
        out.append(ok(f'win rate {wr:.1f}% — agent dominating self-play'))

    # Always-negative reward warning
    positive_eps = sum(1 for r in rewards if r > 0)
    if positive_eps == 0 and n >= 5:
        out.append(warn(f'ALL {n} episodes have negative reward — spam penalty overwhelming signal'))
        out.append(info('This is normal early in training — PPO uses advantages, not raw rewards'))

    return out


# ─── PPO stats ────────────────────────────────────────────────────────────────

def check_stats(run_id: str) -> list[str]:
    path = CKPT_DIR / f'mk4_training_stats_{run_id}.jsonl'
    if not path.exists():
        return [warn('no PPO stats yet — need first batch to complete')]
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    if not lines:
        return [warn('stats file empty')]
    try:
        last = json.loads(lines[-1])
        return [ok(f'{len(lines)} PPO updates — last: update={last.get("update")}  '
                   f'ep={last.get("episode")}  batch={last.get("batch_eps")}')]
    except Exception:
        return [warn('stats file unreadable')]


# ─── checkpoint ───────────────────────────────────────────────────────────────

def check_checkpoint(run_id: str, agent: str) -> list[str]:
    stem  = CKPT_STEMS.get(agent, f'mk4_{agent}')
    ckpt  = CKPT_DIR / f'{stem}_{run_id}.pt'
    if not ckpt.exists():
        return [warn(f'no checkpoint yet (saved every 10 updates)')]
    kb  = ckpt.stat().st_size / 1024
    age = time.time() - ckpt.stat().st_mtime
    return [ok(f'{ckpt.name}  {kb:.0f}KB  saved {age:.0f}s ago')]


# ─── processes ────────────────────────────────────────────────────────────────

def check_processes(run_id: str, n_workers: int) -> list[str]:
    out = []
    try:
        ps = subprocess.check_output(['ps', 'aux'], text=True)
        bridges = sum(1 for l in ps.splitlines() if 'run_bridge_server' in l and run_id in l)
        # Learner is the main mk4_train_parallel.py process (spawns subprocesses via multiprocessing)
        trainer = sum(1 for l in ps.splitlines() if 'mk4_train_parallel' in l and f'run-id {run_id}' in l)
        # mupen64plus binary instances for this run_id (exclude python bridge server processes)
        mupen   = sum(1 for l in ps.splitlines()
                      if 'mupen64plus' in l and f'train-{run_id}' in l and 'python' not in l)

        lvl = ok if bridges == n_workers else warn
        out.append(lvl(f'bridge servers: {bridges}/{n_workers}'))
        out.append(ok(f'mupen64plus instances: {mupen}/{n_workers}'))
        if trainer:
            out.append(ok(f'trainer process: running (pid visible)'))
        else:
            out.append(warn(f'trainer process: not found in ps (may have finished or crashed)'))
    except Exception as e:
        out.append(warn(f'could not check processes: {e}'))
    return out


# ─── main ─────────────────────────────────────────────────────────────────────

def run_checks(run_ids: list[str], agents: list[str], n_workers: int) -> int:
    fails = warns = 0
    ts    = time.strftime('%Y-%m-%d %H:%M:%S')
    w     = 68

    print(f'\n{BOLD}{"═"*w}{X}')
    print(f'{BOLD}  MK4 Training Check  {ts}{X}')
    print(f'{BOLD}  runs={run_ids}  workers_each={n_workers}{X}')
    print(f'{BOLD}{"═"*w}{X}')

    for run_id, agent in zip(run_ids, agents):
        print(f'\n{BOLD}{B}── {run_id} ({agent}) ──────────────────────────────{X}')

        sections = [
            ('Processes',    check_processes(run_id, n_workers)),
            ('Emulators',    check_emulator_logs(run_id, n_workers)),
            ('Heartbeat',    check_heartbeat(run_id)),
            ('Episodes',     check_training_log(run_id)),
            ('PPO Updates',  check_stats(run_id)),
            ('Checkpoint',   check_checkpoint(run_id, agent)),
        ]
        for title, items in sections:
            print(f'  {BOLD}{title}:{X}')
            for item in items:
                print(f'    {item}')
                if item.startswith(f'{R}FAIL'):  fails += 1
                if item.startswith(f'{Y}WARN'):  warns += 1

    print(f'\n{BOLD}{"═"*w}{X}')
    if fails > 0:
        print(f'{R}{BOLD}  {fails} FAILURES  {warns} warnings{X}')
    elif warns > 0:
        print(f'{Y}{BOLD}  0 failures  {warns} warnings{X}')
    else:
        print(f'{G}{BOLD}  All checks passed{X}')
    print(f'{BOLD}{"═"*w}{X}\n')

    return 1 if fails > 0 else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-ids', default='lstm,disc_rssm,obj_belief',
                    help='Comma-separated run-ids (default: lstm,disc_rssm,obj_belief)')
    ap.add_argument('--agents',  default='lstm,disc_rssm,obj_belief',
                    help='Comma-separated agent types matching --run-ids order')
    ap.add_argument('--workers', type=int, default=2,
                    help='Workers per run (default: 2)')
    ap.add_argument('--live',    action='store_true',
                    help='Refresh every 5s until Ctrl+C')
    args = ap.parse_args()

    run_ids = [r.strip() for r in args.run_ids.split(',') if r.strip()]
    agents  = [a.strip() for a in args.agents.split(',')  if a.strip()]

    # Pad agents list if shorter than run_ids
    while len(agents) < len(run_ids):
        agents.append(agents[-1] if agents else 'lstm')

    if args.live:
        try:
            while True:
                print('\033[2J\033[H', end='')
                run_checks(run_ids, agents, args.workers)
                print('(live mode — Ctrl+C to stop, refreshes every 5s)')
                time.sleep(5)
        except KeyboardInterrupt:
            pass
    else:
        sys.exit(run_checks(run_ids, agents, args.workers))


if __name__ == '__main__':
    main()
