#!/usr/bin/env python3
"""
mk4_ram_scan.py — RAM Address Discovery Tool for MK4 Training

Scans memory regions around known confirmed addresses to find:
  - P1/P2 Y-position (jumping state)
  - P2 animation ID (is opponent attacking right now?)
  - P1/P2 hitstun flag (is either player stunned / in hit recovery?)

Usage:
    # Baseline scan (record all values at round start)
    python3 training/tools/mk4_ram_scan.py --mode baseline

    # Jump scan (scan while P1 is jumping)
    python3 training/tools/mk4_ram_scan.py --mode compare --baseline baseline.json

    # Attack scan (scan while P2 is attacking)
    python3 training/tools/mk4_ram_scan.py --mode compare --baseline baseline.json --label p2_attack

Method:
  1. Run --mode baseline — saves snapshot of all candidate addresses
  2. Do an in-game event (jump, or let CPU attack)
  3. Run --mode compare — shows which addresses changed and by how much
  4. Repeat to narrow down candidates
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))

SOCK = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')

# ── Known confirmed addresses (for reference) ─────────────────────────────────
KNOWN = {
    'p1_health_word':  0x0FE0D8,  # u32 fixed-point (0x00010000 = full)
    'p2_health_word':  0x126F54,  # u32 fixed-point
    'p1_health_hud':   0x36E729,  # animated HUD byte
    'p2_health_hud':   0x36E72E,  # animated HUD byte
    'timer':      0x105118,   # byte
    'p1_x_hi':    0x0F87FC,   # int16 (hi word of 32-bit fixed-point)
    'p2_x_hi':    0x06A064,   # int16
}

# ── Scan windows — (description, base_addr, window_bytes, step) ──────────────
# Strategy: scan ±256 bytes around each known struct pointer
# Also scan a broad "player struct" region to catch Y and anim_id
SCAN_WINDOWS = [
    # P1 struct region — X confirmed at 0x0F87FC, scan ±128 words
    ('P1_struct',  0x0F8780, 256),
    # P2 struct region — X confirmed at 0x06A064, scan ±128 words
    ('P2_struct',  0x06A000, 256),
    # Internal health words and HUD bytes
    ('p1_health_word_rgn', 0x0FE0C0, 0x40),
    ('p2_health_word_rgn', 0x126F40, 0x40),
    ('health_hud_rgn', 0x36E700, 128),
    # Wild card — common MK4 state machine region
    ('state_rgn',  0x36E000, 512),
]

SCAN_DIR = N64_ROOT / 'training/data/ram_scans'


def read_region(bridge, base_addr: int, size: int) -> bytes | None:
    """Read `size` bytes starting at N64 RAM address `base_addr`."""
    try:
        # The bridge's GET_RAM_FEATURES reads a full dump; we extract the region
        features = bridge.get_ram_features()
        raw = features.get('raw_bytes')  # bytes object if available
        if raw and base_addr + size <= len(raw):
            return raw[base_addr:base_addr + size]
    except Exception:
        pass
    return None


def addr_label(addr: int) -> str:
    for name, a in KNOWN.items():
        if abs(a - addr) <= 2:
            return f'≈{name}'
    return ''


def baseline_scan(bridge) -> dict[str, int]:
    """Read all candidate addresses and save their values."""
    from n64train.runtime.bridge import SocketEmulatorBridge
    snapshot: dict[str, int] = {}

    for desc, base, size in SCAN_WINDOWS:
        print(f'  Scanning {desc} @ 0x{base:06X} ({size} bytes)...')
        region = read_region(bridge, base, size)
        if region is None:
            print(f'    ⚠ Could not read region')
            continue
        # Record every byte
        for off in range(size):
            addr = base + off
            snapshot[f'0x{addr:06X}'] = region[off]

    return snapshot


def compare_scan(bridge, baseline: dict[str, int], label: str) -> list[dict]:
    """Compare current RAM to baseline, return changed addresses."""
    current = baseline_scan(bridge)
    changes = []
    for addr_str, old_val in baseline.items():
        new_val = current.get(addr_str, old_val)
        if new_val != old_val:
            addr = int(addr_str, 16)
            changes.append({
                'addr':  addr_str,
                'old':   old_val,
                'new':   new_val,
                'delta': new_val - old_val,
                'label': addr_label(addr),
            })

    changes.sort(key=lambda x: abs(x['delta']), reverse=True)
    return changes[:50]  # top 50 by delta magnitude


def format_results(changes: list[dict]) -> None:
    if not changes:
        print('  No changes detected.')
        return
    print(f'\n  {"Address":<12}  {"Old":>5}  {"New":>5}  {"Delta":>7}  Note')
    print('  ' + '-' * 55)
    for c in changes:
        note = c['label']
        print(f'  {c["addr"]:<12}  {c["old"]:>5}  {c["new"]:>5}  {c["delta"]:>+7}  {note}')


def try_direct_reads(bridge) -> None:
    """Try reading specific offsets near known addresses via Mk4BridgeHelper."""
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    h = Mk4BridgeHelper(bridge)

    def _health160(word: int) -> int:
        return int(round(max(0.0, min(1.0, word / 0x00010000)) * 160))

    # Based on P1_X_ADDR = 0x800F87F8 and P2_X_ADDR = 0x8006A060
    # N64 character structs often layout: X (4 bytes), Y (4 bytes), Z (4 bytes), then state
    candidates = {
        # P1 struct offsets from P1_X (0x800F87F8)
        'P1_X     (known)':    0x800F87F8,
        'P1_Y_+4':             0x800F87FC,
        'P1_Y_+8':             0x800F8800,
        'P1_Y_-4':             0x800F87F4,
        'P1_anim_+12':         0x800F8804,
        'P1_anim_+16':         0x800F8808,
        'P1_state_+20':        0x800F880C,

        # P2 struct offsets from P2_X (0x8006A060)
        'P2_X     (known)':    0x8006A060,
        'P2_Y_+4':             0x8006A064,
        'P2_Y_+8':             0x8006A068,
        'P2_Y_-4':             0x8006A05C,
        'P2_anim_+12':         0x8006A06C,
        'P2_anim_+16':         0x8006A070,
        'P2_state_+20':        0x8006A074,

        # Internal health words plus HUD references
        'P1_health_word':      0x800FE0D8,
        'P2_health_word':      0x80126F54,
        'P1_health_hud':       0x8036E729,
        'P2_health_hud':       0x8036E72E,
        'hitstun_-3':          0x8036E726,
        'hitstun_-6':          0x8036E723,
        'hitstun_+5':          0x8036E72B,   # between P1+P2 health
        'hitstun_+9':          0x8036E730,
        'hitstun_+12':         0x8036E733,
        'p1_anim_near_health': 0x8036E720,
        'p2_anim_near_health': 0x8036E740,
    }

    print(f'\n  {"Name":<26}  {"Addr":<12}  {"byte":>5}  {"u32":>10}  {"hp":>5}  {"i16":>7}')
    print('  ' + '-' * 73)
    for name, addr in candidates.items():
        try:
            b_val = h.read_u8(addr)
            u32   = h.read_u32(addr)
            i16   = (u32 >> 16)
            if i16 >= 0x8000: i16 -= 0x10000
            hp160 = _health160(u32) if 'health_word' in name else ''
            print(f'  {name:<26}  0x{addr:08X}  {b_val:>5d}  {u32:>10d}  {str(hp160):>5s}  {i16:>+7d}')
        except Exception as e:
            print(f'  {name:<26}  0x{addr:08X}  ERR: {e}')


def main() -> None:
    ap = argparse.ArgumentParser(description='MK4 RAM Address Scanner')
    ap.add_argument('--mode', choices=['baseline', 'compare', 'direct'],
                    default='direct', help='Scan mode')
    ap.add_argument('--baseline', default=str(SCAN_DIR / 'baseline.json'),
                    help='Path to baseline JSON')
    ap.add_argument('--label', default='event',
                    help='Label for compare scan (e.g. jump, p2_attack)')
    args = ap.parse_args()

    SCAN_DIR.mkdir(parents=True, exist_ok=True)

    print(f'[scan] Connecting to bridge: {SOCK}')
    sys.path.insert(0, str(N64_ROOT / 'training/src'))
    from n64train.runtime.bridge import SocketEmulatorBridge
    b = SocketEmulatorBridge(SOCK, timeout_sec=10)
    print('[scan] Connected.\n')

    if args.mode == 'direct':
        print('[scan] Mode: DIRECT — reading candidate addresses immediately')
        try_direct_reads(b)
        print('\n[scan] Tip: run with --mode baseline first, then --mode compare')

    elif args.mode == 'baseline':
        print('[scan] Mode: BASELINE — saving snapshot')
        snap = baseline_scan(b)
        out = Path(args.baseline)
        out.write_text(json.dumps(snap, indent=2))
        print(f'[scan] Saved {len(snap)} addresses → {out}')

    elif args.mode == 'compare':
        bl_path = Path(args.baseline)
        if not bl_path.exists():
            print(f'[scan] No baseline found at {bl_path} — run --mode baseline first')
            sys.exit(1)
        baseline = json.loads(bl_path.read_text())
        print(f'[scan] Mode: COMPARE ({args.label}) — looking for changes vs baseline')
        changes = compare_scan(b, baseline, args.label)
        format_results(changes)

        out = SCAN_DIR / f'compare_{args.label}.json'
        out.write_text(json.dumps(changes, indent=2))
        print(f'\n[scan] Saved {len(changes)} changes → {out}')

    b.close()


if __name__ == '__main__':
    main()
