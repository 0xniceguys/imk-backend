#!/usr/bin/env python3
"""
check_training.py — Quick training status snapshot.
Run anytime: python3 training/scripts/check_training.py
"""
import json, subprocess, sys
from pathlib import Path
from collections import defaultdict

N64_ROOT = Path(__file__).resolve().parents[2]
LOG      = N64_ROOT / 'training/data/logs/mk4_training_log.jsonl'
ARCH_LOGS = N64_ROOT / 'training/data/logs'

AGENTS = ['lstm', 'obj_belief', 'transformer', 'disc_rssm']

# ── Episode stats from JSONL ──────────────────────────────────────────────────
eps = defaultdict(list)
if LOG.exists():
    for line in LOG.read_text().splitlines():
        try:
            e = json.loads(line)
            eps[e.get('agent')].append(e)
        except Exception:
            pass

print("=" * 65)
print("  TRAINING STATUS")
print("=" * 65)

for ag in AGENTS:
    data = eps[ag]
    if not data:
        print(f"  {ag:15s}  no data yet")
        continue
    last20  = data[-20:]
    prev20  = data[-40:-20]
    avg_r   = sum(e.get('reward', 0) for e in last20) / len(last20)
    avg_d   = sum(e.get('r_dealt', 0) for e in last20) / len(last20)
    wins    = sum(1 for e in last20 if e.get('won'))
    trend   = avg_r - (sum(e.get('reward', 0) for e in prev20) / max(len(prev20), 1))
    icon    = '📈' if trend > 2 else ('📉' if trend < -2 else '➡')
    print(f"  {icon} {ag:15s}  n={len(data):5d}  "
          f"r={avg_r:+6.1f}  dealt={avg_d:+6.1f}  "
          f"wins={wins}/20  trend={trend:+.1f}")

# ── Process health ─────────────────────────────────────────────────────────────
print()
try:
    procs = subprocess.check_output(
        ['ps', 'aux'], text=True
    ).splitlines()
    trainers = [p for p in procs if 'mk4_train_parallel' in p and 'grep' not in p]
    emulators = [p for p in procs if 'mupen64plus' in p and 'grep' not in p]
    print(f"  Processes: {len(trainers)} trainers  {len(emulators)} emulators")
except Exception:
    print("  (process check failed)")

# ── Recent errors ─────────────────────────────────────────────────────────────
print()
print("  Recent errors (last 5 per agent):")
for ag in AGENTS:
    log_path = ARCH_LOGS / f'arch-{ag}.log'
    if not log_path.exists():
        continue
    lines = log_path.read_text().splitlines()
    errors = [l for l in lines if any(k in l for k in ['BrokenPipe', 'FATAL', 'bridge attempt', 'Error'])]
    if errors:
        print(f"    {ag}: {errors[-1][:80]}")

print("=" * 65)
