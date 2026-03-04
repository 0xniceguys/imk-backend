#!/usr/bin/env python3
"""
find_fight_addrs.py — MK4 Fight-State RAM Scanner (v2, fast dumpmem)
───────────────────────────────────────────────────────────────────────
Uses the debugger's `dumpmem` command to dump full RDRAM to a binary
file in one shot (fast, no timeout), then diffs two snapshots to find
health and timer candidates.

Important:
  - Byte-diff scans tend to surface animated HUD health bytes first.
  - The canonical internal MK4 health words are:
      P1 = 0x800FE0D8
      P2 = 0x80126F54
    where full health = 0x00010000.

Usage:
    # 1. Start emulator + load fight state manually first
    # 2. Run this script — it will pause, dump, run for N seconds, dump again, diff
    python3 training/scripts/find_fight_addrs.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

SOCK     = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
TEST_ST  = str(N64_ROOT / 'training/data/savestates/mk4_arcade/test.st')
DUMP_DIR = Path('/tmp/mk4_ram_dumps')

RDRAM_BASE  = 0x80000000
RDRAM_SIZE  = 4 * 1024 * 1024   # 4 MB (but dumpmem typically gives 8MB)
HEALTH_MAX  = 0xA0               # 160
TIMER_MAX   = 99
FIGHT_SECS  = 8.0


def connect():
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    b = SocketEmulatorBridge(SOCK, timeout_sec=15)
    h = Mk4BridgeHelper(b)
    return b, h


def dump_rdram(b, label: str) -> Path:
    """Use bridge's debugger_command to call dumpmem, return path to binary file."""
    DUMP_DIR.mkdir(exist_ok=True)
    out_path = DUMP_DIR / f'{label}.bin'
    resp = b.debugger_command(
        f'dumpmem 80000000 0x400000 {out_path}',
        timeout_sec=60,
        output_tail_chars=512,
    )
    output = str(resp.get('output', ''))
    if 'M64P_DUMPMEM_OK' not in output:
        raise RuntimeError(f'dumpmem failed: {output[-300:]}')
    data = out_path.read_bytes()
    print(f'  dumped {len(data)//1024}KB → {out_path}')
    return out_path


def diff_and_rank(before: bytes, after: bytes, elapsed: float) -> dict:
    size = min(len(before), len(after))
    health_cands, timer_cands = [], []

    for offset in range(size):
        b0 = before[offset]
        b1 = after[offset]
        if b0 == b1:
            continue

        addr = RDRAM_BASE + (offset ^ 3)   # N64 byte-swap

        # Health: was [5, 160], decreased, still alive
        if 5 <= b0 <= HEALTH_MAX and 0 < b1 < b0:
            health_cands.append({'address': f'0x{addr:08X}', 'before': b0, 'after': b1,
                                  'drop': b0 - b1, 'offset': offset})

        # Timer: was [10, 99], decreased by amount consistent with elapsed time
        if 10 <= b0 <= TIMER_MAX and 0 < b1 < b0:
            drop = b0 - b1
            if 1 <= drop <= int(elapsed * 7 + 5):
                timer_cands.append({'address': f'0x{addr:08X}', 'before': b0, 'after': b1,
                                     'drop': drop, 'offset': offset})

    health_cands.sort(key=lambda x: -x['drop'])
    timer_cands.sort(key=lambda x: x['drop'])
    return {'health': health_cands[:25], 'timer': timer_cands[:15]}


def cmd(fn):
    """Open a fresh bridge connection, run fn(b, h), close."""
    b, h = connect()
    try:
        return fn(b, h)
    finally:
        try: b.close()
        except: pass


def main():
    print('[scan] Step 1: Load state + pause + dump BEFORE…')

    def _load_and_before(b, h):
        b.load_savestate_path(Path(TEST_ST))
        time.sleep(0.4)
        h.pause()
        time.sleep(0.15)
        return dump_rdram(b, 'before')

    before_path = cmd(_load_and_before)
    before = before_path.read_bytes()
    print(f'[scan] BEFORE: {len(before)//1024}KB dumped')

    print('[scan] Step 2: Run fight…')
    cmd(lambda b, h: h.run())

    t0 = time.time()
    time.sleep(FIGHT_SECS)
    elapsed = time.time() - t0
    print(f'[scan] Fight ran {elapsed:.1f}s')

    print('[scan] Step 3: Pause + dump AFTER…')

    def _pause_and_after(b, h):
        h.pause()
        time.sleep(0.15)
        return dump_rdram(b, 'after')

    after_path = cmd(_pause_and_after)
    after = after_path.read_bytes()

    total_changed = sum(1 for i in range(min(len(before), len(after))) if before[i] != after[i])
    print(f'[scan] Bytes changed: {total_changed}')

    results = diff_and_rank(before, after, elapsed)

    print(f'\n=== HEALTH CANDIDATES (top {min(15, len(results["health"]))}) ===')
    for c in results['health'][:15]:
        print(f'  {c["address"]}  {c["before"]:3d} → {c["after"]:3d}  drop={c["drop"]}')

    print(f'\n=== TIMER CANDIDATES (top {min(10, len(results["timer"]))}) ===')
    for c in results['timer'][:10]:
        print(f'  {c["address"]}  {c["before"]:3d} → {c["after"]:3d}  drop={c["drop"]}')

    out = Path('/tmp/mk4_fight_addrs.json')
    out.write_text(json.dumps({'elapsed': elapsed, 'total_changed': total_changed,
                               'health': results['health'], 'timer': results['timer']}, indent=2))
    print(f'\n[scan] Results saved → {out}')


if __name__ == '__main__':
    main()
