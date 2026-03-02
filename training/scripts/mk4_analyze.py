#!/usr/bin/env python3
"""
mk4_analyze.py — Training Log Analyzer

Reads training/data/logs/mk4_training_log.jsonl and prints:
  - Episode stats summary
  - Reward term breakdown (what's driving rewards)
  - Win rate trend
  - Health delta trend (is agent dealing/taking damage over time?)

Usage:
    python3 training/scripts/mk4_analyze.py
    python3 training/scripts/mk4_analyze.py --last 100    # last 100 episodes
    python3 training/scripts/mk4_analyze.py --plot        # print ASCII charts
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, stdev

N64_ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = N64_ROOT / 'training/data/logs/mk4_training_log.jsonl'


def load_log(n: int | None = None) -> list[dict]:
    if not LOG_FILE.exists():
        print(f'No log file at {LOG_FILE}')
        sys.exit(1)
    rows = [json.loads(l) for l in LOG_FILE.read_text().splitlines() if l.strip()]
    if n:
        rows = rows[-n:]
    return rows


def bar(val: float, lo: float, hi: float, width: int = 20) -> str:
    """Tiny ASCII bar chart: maps val in [lo,hi] to a bar of `width` chars."""
    if hi == lo:
        return '─' * width
    frac = max(0.0, min(1.0, (val - lo) / (hi - lo)))
    filled = round(frac * width)
    return '█' * filled + '░' * (width - filled)


def fmt(val: float | None, signed: bool = True) -> str:
    if val is None:
        return '   N/A'
    return f'{val:+7.2f}' if signed else f'{val:7.2f}'


def analyze(rows: list[dict], show_plot: bool) -> None:
    n = len(rows)
    if n == 0:
        print('No episodes in log.')
        return

    eps   = [r['episode'] for r in rows]
    total = [r['reward'] for r in rows]
    won   = [r['won'] for r in rows]

    # Reward terms (may be absent in old logs)
    def get(r, k): return r.get(k, 0.0) or 0.0
    dealt  = [get(r,'r_dealt')    for r in rows]
    taken  = [get(r,'r_taken')    for r in rows]
    appr   = [get(r,'r_approach') for r in rows]
    dist   = [get(r,'r_dist_pen') for r in rows]
    surv   = [get(r,'r_survival') for r in rows]
    wins_r = [get(r,'r_win')      for r in rows]
    loss_r = [get(r,'r_loss')     for r in rows]

    hp1_end = [r.get('p1_health') for r in rows]
    hp2_end = [r.get('p2_health') for r in rows]
    steps   = [r.get('steps', 0) for r in rows]

    win_rate = sum(won) / n * 100
    avg_r    = mean(total)
    std_r    = stdev(total) if n > 1 else 0.0

    print('=' * 65)
    print(f'  MK4 Training Log Analysis  —  {n} episodes  (ep {eps[0]}–{eps[-1]})')
    print('=' * 65)

    print(f'\n{"SUMMARY":─<65}')
    print(f'  Episodes:       {n}')
    print(f'  Win rate:       {win_rate:.1f}%  ({sum(won)} wins)')
    print(f'  Avg reward:     {avg_r:+.2f}  ±{std_r:.2f}')
    print(f'  Avg steps/ep:   {mean(steps):.0f}')
    print(f'  Min reward:     {min(total):+.2f}  (ep {eps[total.index(min(total))]})')
    print(f'  Max reward:     {max(total):+.2f}  (ep {eps[total.index(max(total))]})')

    print(f'\n{"REWARD BREAKDOWN (episode averages)":─<65}')
    terms = [
        ('damage_dealt', dealt,  '💥 Agent dealt damage to P2'),
        ('damage_taken', taken,  '🩸 P1 took damage (penalty)'),
        ('approach',     appr,   '📏 Closed distance to P2'),
        ('dist_penalty', dist,   '🏃 Camped far away (penalty)'),
        ('survival',     surv,   '⏱  Stayed alive per step'),
        ('win_bonus',    wins_r, '🏆 Round wins'),
        ('loss_penalty', loss_r, '💀 Round losses'),
    ]
    for name, vals, label in terms:
        m = mean(vals)
        pct = (m / avg_r * 100) if avg_r != 0 else 0.0
        print(f'  {name:<14}  {m:+8.3f}  ({pct:+5.1f}%)  {label}')

    # Diagnosis
    print(f'\n{"DIAGNOSIS":─<65}')
    surv_pct = mean(surv) / avg_r * 100 if avg_r != 0 else 0
    dealt_pct = mean(dealt) / avg_r * 100 if avg_r != 0 else 0

    if surv_pct > 80:
        print('  ⚠️  SURVIVAL DOMINATES reward (>80%) — agent not attacking.')
        print('       Fix: reduce survival_per_step or skew actions toward attacks.')
    elif dealt_pct > 50:
        print('  ✅ DAMAGE DEALING dominates reward — agent is fighting.')

    if abs(mean(taken)) < 1.0:
        print('  ⚠️  ALMOST NO DAMAGE TAKEN — CPU not hitting P1 (unusual).')
        print('       Check: P2 health address is correct, agent not camping.')

    if win_rate == 0:
        print('  ℹ️  0% win rate — expected for random agent.')
    elif win_rate > 20:
        print(f'  ✅ {win_rate:.0f}% win rate — agent learning!')

    hp1_valid = [h for h in hp1_end if h is not None]
    hp2_valid = [h for h in hp2_end if h is not None]
    if hp1_valid and hp2_valid:
        print(f'  P1 health at ep end: avg={mean(hp1_valid):.0f}  P2: avg={mean(hp2_valid):.0f}')
        if mean(hp2_valid) > 150:
            print('  ⚠️  P2 health barely changes — agent not landing hits.')

    # ASCII trend chart
    if show_plot and n >= 5:
        print(f'\n{"REWARD TREND (rolling avg)":─<65}')
        window = max(1, n // 20)
        rollmean = []
        for i in range(n):
            chunk = total[max(0,i-window):i+1]
            rollmean.append(mean(chunk))
        lo, hi = min(rollmean), max(rollmean)
        print(f'  Range: [{lo:+.2f}, {hi:+.2f}]')
        cols = 55
        for i in range(0, n, max(1, n // cols)):
            b = bar(rollmean[i], lo, hi, 40)
            print(f'  ep{eps[i]:4d} {rollmean[i]:+6.2f} |{b}|')

    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--last',  type=int, default=None, help='Analyse last N episodes')
    ap.add_argument('--plot',  action='store_true', help='Show ASCII reward trend')
    args = ap.parse_args()
    rows = load_log(args.last)
    analyze(rows, args.plot)


if __name__ == '__main__':
    main()
