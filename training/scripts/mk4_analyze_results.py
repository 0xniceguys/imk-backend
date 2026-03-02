#!/usr/bin/env python3
"""
mk4_analyze_results.py — Analyze training logs and stress test output.

Modes:
  --mode stress   : Analyze stress test logs (training/data/logs/stress_test/)
  --mode train    : Analyze main training log (mk4_training_log.jsonl)
  --mode all      : Both + checkpoint inventory

Usage:
    python3 training/scripts/mk4_analyze_results.py --mode stress
    python3 training/scripts/mk4_analyze_results.py --mode train
    python3 training/scripts/mk4_analyze_results.py --mode all
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from collections import defaultdict

N64_ROOT  = Path(__file__).resolve().parents[2]
STRESS_DIR= N64_ROOT / 'training/data/logs/stress_test'
TRAIN_LOG = N64_ROOT / 'training/data/logs/mk4_training_log.jsonl'
CKPT_DIR  = N64_ROOT / 'training/data/checkpoints'

SEP = '─' * 60


def read_jsonl(path: Path, max_lines: int = 10000) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    out = []
    for l in lines[-max_lines:]:
        try:
            out.append(json.loads(l))
        except:
            pass
    return out


# ── STRESS TEST ANALYSIS ──────────────────────────────────────────────────────

def parse_stress_log(log_path: Path) -> dict:
    """Parse per-arch stress log file, extract key metrics."""
    result = {
        'arch':        log_path.stem.replace('arch-', ''),
        'log_path':    str(log_path),
        'size_bytes':  log_path.stat().st_size if log_path.exists() else 0,
        'errors':      [],
        'episodes':    0,
        'workers_done': 0,
        'emulators_started': 0,
        'socket_ready': 0,
        'learner_started': False,
        'fatal': False,
    }
    if not log_path.exists() or result['size_bytes'] == 0:
        result['errors'].append('LOG MISSING OR EMPTY')
        result['fatal'] = True
        return result

    text = log_path.read_text()

    # Count emulator socket confirmations
    result['socket_ready'] = text.count('socket ready')
    result['emulators_started'] = text.count('pid=')
    result['learner_started'] = 'learner' in text.lower() and 'ready' in text.lower()

    # Count completed episodes
    ep_matches = re.findall(r'ep=(\d+)/\d+.*steps=(\d+)', text)
    result['episodes'] = len(ep_matches)

    # Check for workers done
    result['workers_done'] = text.count('finished all')

    # Pull episode reward lines
    rewards = re.findall(r'r=([+-]?\d+\.\d+)', text)
    if rewards:
        rews = [float(r) for r in rewards]
        result['avg_reward'] = round(sum(rews) / len(rews), 3)
        result['min_reward'] = round(min(rews), 3)
        result['max_reward'] = round(max(rews), 3)

    # Detect errors
    for line in text.splitlines():
        lower = line.lower()
        if any(k in lower for k in ['error', 'exception', 'traceback', 'timeout', 'refused', 'failed']):
            result['errors'].append(line.strip()[:120])

    if result['errors']:
        result['fatal'] = any('traceback' in e.lower() for e in result['errors'])

    return result


def analyze_stress():
    print(f'\n{SEP}')
    print('  STRESS TEST ANALYSIS')
    print(f'{SEP}')

    if not STRESS_DIR.exists():
        print('  ❌ No stress test logs found. Run mk4_stress_test.sh first.')
        return

    summary_path = STRESS_DIR / 'summary.log'
    if summary_path.exists():
        print('\n  Test config:')
        for l in summary_path.read_text().splitlines():
            print(f'    {l}')

    archs = ['mlp', 'lstm', 'gru', 'cont_rssm', 'disc_rssm',
             'transformer', 'obj_belief', 'latent_planner']

    print(f'\n  {"ARCH":<20} {"SOCKETS":>8} {"EPISODES":>9} {"AVG_R":>8} {"ERRORS":>7} {"STATUS"}')
    print(f'  {"-"*20} {"-"*8} {"-"*9} {"-"*8} {"-"*7} {"-"*10}')

    total_eps = 0; total_errors = 0; full_success = 0
    for arch in archs:
        log = STRESS_DIR / f'arch-{arch}.log'
        r = parse_stress_log(log)

        if r['size_bytes'] == 0:
            status = '❌ NO OUTPUT'
        elif r['fatal']:
            status = '💥 CRASHED'
        elif r['episodes'] > 0 and r['workers_done'] > 0:
            status = '✅ COMPLETE'
            full_success += 1
        elif r['socket_ready'] > 0:
            status = '⚠️  PARTIAL (emulators up)'
        else:
            status = '⏳ STARTING...'

        avg_r = r.get('avg_reward', '—')
        avg_r_str = f'{avg_r:+.1f}' if isinstance(avg_r, float) else '—'
        print(f'  {arch:<20} {r["socket_ready"]:>8} {r["episodes"]:>9}'
              f' {avg_r_str:>8} {len(r["errors"]):>7}  {status}')
        total_eps += r['episodes']
        total_errors += len(r['errors'])

    print(f'\n  {SEP}')
    print(f'  Architectures fully completed : {full_success}/8')
    print(f'  Total episodes collected      : {total_eps}')
    print(f'  Total error lines             : {total_errors}')

    # Error details for failed archs
    any_errors = False
    for arch in archs:
        log = STRESS_DIR / f'arch-{arch}.log'
        r = parse_stress_log(log)
        if r['errors']:
            if not any_errors:
                print(f'\n  {SEP}')
                print('  ERROR DETAILS')
                any_errors = True
            print(f'\n  [{arch}] {len(r["errors"])} error lines:')
            for e in r['errors'][:5]:
                print(f'    ⚠  {e}')

    # Recommendation
    print(f'\n  {SEP}')
    print('  RECOMMENDATION')
    if full_success >= 6:
        print('  ✅ Mac handled 16 emulators well! Safe to train with --workers-per-arch 2.')
    elif full_success >= 4:
        print('  ⚠️  Some archs struggled. Recommend --workers-per-arch 1 (8 total emulators).')
    else:
        print('  ❌ Too many failures. Recommend --workers-per-arch 1 or train one arch at a time.')
        print('     Single-process command: python3 training/scripts/mk4_train.py --agent lstm --episodes 99999')


# ── TRAINING LOG ANALYSIS ─────────────────────────────────────────────────────

def analyze_train():
    print(f'\n{SEP}')
    print('  TRAINING LOG ANALYSIS')
    print(f'{SEP}')

    eps = read_jsonl(TRAIN_LOG)
    if not eps:
        print('  No training episodes logged yet.')
        return

    # Group by agent
    by_agent: dict[str, list] = defaultdict(list)
    for e in eps:
        by_agent[e.get('agent', 'unknown')].append(e)

    for agent, data in sorted(by_agent.items()):
        rewards = [d['reward'] for d in data if 'reward' in d]
        wins    = [d for d in data if d.get('won')]
        print(f'\n  [{agent}]  {len(data)} episodes  wins={len(wins)}  win%={len(wins)/max(1,len(data))*100:.1f}%')
        if rewards:
            print(f'    Reward — avg: {sum(rewards)/len(rewards):+.2f}'
                  f'  best: {max(rewards):+.2f}'
                  f'  worst: {min(rewards):+.2f}')
            # Last 10 trend
            last10 = rewards[-10:]
            trend = '📈' if last10[-1] > last10[0] else '📉'
            print(f'    Last 10 avg: {sum(last10)/len(last10):+.2f}  {trend}')

        # Component breakdown
        dealt_list = [d.get('r_dealt', 0) for d in data]
        taken_list = [d.get('r_taken', 0) for d in data]
        spam_list  = [d.get('r_spam',  0) for d in data]
        if dealt_list:
            print(f'    dealt avg: {sum(dealt_list)/len(dealt_list):+.1f}'
                  f'  taken avg: {sum(taken_list)/len(taken_list):+.1f}'
                  f'  spam avg: {sum(spam_list)/len(spam_list):+.1f}')


# ── CHECKPOINT INVENTORY ──────────────────────────────────────────────────────

def analyze_checkpoints():
    print(f'\n{SEP}')
    print('  CHECKPOINT INVENTORY')
    print(f'{SEP}')
    if not CKPT_DIR.exists():
        print('  No checkpoints found.'); return

    ckpt_files = sorted(CKPT_DIR.glob('*.pt'))
    if not ckpt_files:
        print('  No .pt files found.'); return

    print(f'  {"FILE":<35} {"SIZE":>8}  AGENT')
    AGENT_MAP = {
        'mk4_policy.pt':       'mlp',
        'mk4_lstm_policy.pt':  'lstm',
        'mk4_gru.pt':          'gru',
        'mk4_cont_rssm.pt':    'cont_rssm',
        'mk4_disc_rssm.pt':    'disc_rssm',
        'mk4_transformer.pt':  'transformer',
        'mk4_obj_belief.pt':   'obj_belief',
        'mk4_latent_planner.pt': 'latent_planner',
    }
    found_agents = set()
    for f in ckpt_files:
        kb = f.stat().st_size // 1024
        agent = AGENT_MAP.get(f.name, '?')
        if agent != '?': found_agents.add(agent)
        print(f'  {f.name:<35} {kb:>6}KB  {agent}')

    missing = set(AGENT_MAP.values()) - found_agents
    if missing:
        print(f'\n  ⚠️  Missing checkpoints: {", ".join(sorted(missing))}')
    else:
        print(f'\n  ✅ All 8 architecture checkpoints present')


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['stress', 'train', 'all'], default='all')
    args = ap.parse_args()

    print(f'\n{"═"*60}')
    print('  MK4 TRAINING RESULTS ANALYZER')
    print(f'{"═"*60}')

    if args.mode in ('stress', 'all'):
        analyze_stress()
    if args.mode in ('train', 'all'):
        analyze_train()
    if args.mode in ('all',):
        analyze_checkpoints()

    print(f'\n{"═"*60}\n')


if __name__ == '__main__':
    main()
