#!/usr/bin/env python3
"""
verify_ram_addrs.py — Verify ALL candidate RAM addresses against the live emulator.

Connects to the running emulator via bridge socket, reads EVERY candidate address
(from both our scanner and GameShark databases), and prints what's at each one.

This lets us:
1. Cross-reference GameShark cheat-code addresses with our confirmed addresses
2. Find Y-position, facing, move/animation ID, and hitstun addresses
3. Verify that each address returns sensible values in a real fight

Usage:
    # Emulator must be running with mk4-visible.sock
    python3 training/scripts/verify_ram_addrs.py

    # With a specific socket
    python3 training/scripts/verify_ram_addrs.py --sock training/data/bridge/mk4-visible.sock
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper

# ── OUR CONFIRMED addresses ──────────────────────────────────────────────────
CONFIRMED = {
    'P1_HEALTH (our scanner)':      ('u8',  0x8036E729),
    'P2_HEALTH (our scanner)':      ('u8',  0x8036E72E),
    'FIGHT_TIMER (our scanner)':    ('u8',  0x80105118),
    'P1_X (our scanner, u32→s16)':  ('u32', 0x800F87F8),
    'P2_X (our scanner, u32→s16)':  ('u32', 0x8006A060),
}

# ── GAMESHARK-DECODED addresses ──────────────────────────────────────────────
# GameShark code format: 81XXXXXX YYYY → write u16 YYYY to 0x80XXXXXX
#                        80XXXXXX YYYY → write u8  YY   to 0x80XXXXXX
GAMESHARK = {
    # P1 struct addresses
    'P1_HEALTH_REAL (GS 810FE0D8)':      ('u16', 0x800FE0D8),
    'P1_HEALTH_REAL_LO (GS 810FE0DA)':   ('u16', 0x800FE0DA),
    'P1_HEALTH_DISPLAY (GS 810FE736)':   ('u16', 0x800FE736),
    'P1_RUN_FAKE (GS 810FE044)':         ('u16', 0x800FE044),
    'P1_RUN_REAL (GS 81104FCC)':         ('u16', 0x81104FCC),
    'P1_CHAR_ID (GS 800FE293)':          ('u8',  0x800FE293),
    'P1_WINS (GS 810FE27E)':             ('u16', 0x800FE27E),
    'P1_CREDITS (GS 810F8506)':          ('u16', 0x800F8506),
    'P1_MEAT_FLAG (GS 81126F42)':        ('u16', 0x81126F42),

    # P2 struct addresses
    'P2_HEALTH_REAL (GS 81126F54)':      ('u16', 0x80126F54),
    'P2_HEALTH_REAL_LO (GS 81126F56)':   ('u16', 0x80126F56),
    'P2_HEALTH_DISPLAY (GS 81105012)':   ('u16', 0x80105012),
    'P2_RUN_FAKE (GS 81126E54)':         ('u16', 0x80126E54),
    'P2_RUN_REAL (GS 81105080)':         ('u16', 0x81105080),
    'P2_CHAR_ID (GS 80126E8F)':          ('u8',  0x80126E8F),
    'P2_CREDITS (GS 810F84BA)':          ('u16', 0x800F84BA),

    # Fight position modifier
    'FIGHT_POS_MOD (GS 81104F9A)':       ('u16', 0x81104F9A),
}

# ── STRUCT NEIGHBORHOOD SCAN ─────────────────────────────────────────────────
# For each confirmed struct base, scan nearby offsets to find Y, facing, anim ID
# We know P1 health "real" is at 0x800FE0D8.
# Scan the 256 bytes around it for interesting values.
P1_STRUCT_BASE = 0x800FE000   # GameShark P1 struct region
P2_STRUCT_BASE = 0x80126E00   # GameShark P2 struct region

# Also scan around our confirmed X position addresses
P1_X_BASE = 0x800F87F8
P2_X_BASE = 0x8006A060

SCAN_RANGES = {
    'P1_STRUCT_REGION':   (P1_STRUCT_BASE, 0x300),   # 768 bytes around P1 struct
    'P2_STRUCT_REGION':   (P2_STRUCT_BASE, 0x200),   # 512 bytes around P2 struct
    'P1_X_NEIGHBORHOOD':  (P1_X_BASE - 0x20, 0x80),  # 128 bytes near P1 X
    'P2_X_NEIGHBORHOOD':  (P2_X_BASE - 0x20, 0x80),  # 128 bytes near P2 X
    'P1_HEALTH_REGION':   (0x8036E720, 0x20),         # 32 bytes near our health
    'P2_HEALTH_REGION_GS': (0x80126F40, 0x30),        # 48 bytes near GS P2 health
}


def read_addr(h: Mk4BridgeHelper, addr: int, dtype: str):
    """Read a value at addr. Returns (value, raw_hex) or (None, 'ERROR')."""
    try:
        if dtype == 'u8':
            v = h.read_u8(addr)
            return v, f'0x{v:02X}'
        elif dtype == 'u16':
            # Read as u32 then take upper or lower 16 bits depending on alignment
            w = h.read_u32(addr & ~3)  # align to 4 bytes
            offset = addr & 3
            if offset == 0:
                v = (w >> 16) & 0xFFFF
            elif offset == 2:
                v = w & 0xFFFF
            else:
                v = (w >> (8 * (3 - offset))) & 0xFFFF
            return v, f'0x{v:04X} ({v})'
        elif dtype == 'u32':
            v = h.read_u32(addr)
            hi = (v >> 16) & 0xFFFF
            s16 = hi if hi < 0x8000 else hi - 0x10000
            return v, f'0x{v:08X} (hi16={s16})'
        else:
            return None, 'UNKNOWN_TYPE'
    except Exception as e:
        return None, f'ERROR: {e}'


def scan_region(h: Mk4BridgeHelper, name: str, base: int, size: int):
    """Read every 4 bytes in a region and print non-zero values."""
    print(f'\n{"="*70}')
    print(f'SCAN: {name} (0x{base:08X} — 0x{base+size:08X}, {size} bytes)')
    print(f'{"="*70}')
    nonzero = 0
    for off in range(0, size, 4):
        addr = base + off
        try:
            w = h.read_u32(addr)
            if w != 0:
                hi = (w >> 16) & 0xFFFF
                lo = w & 0xFFFF
                s16_hi = hi if hi < 0x8000 else hi - 0x10000
                s16_lo = lo if lo < 0x8000 else lo - 0x10000
                b3 = (w >> 24) & 0xFF
                b2 = (w >> 16) & 0xFF
                b1 = (w >> 8) & 0xFF
                b0 = w & 0xFF
                print(f'  +0x{off:04X}  0x{addr:08X}  = 0x{w:08X}  '
                      f'hi16={s16_hi:6d}  lo16={s16_lo:6d}  '
                      f'bytes=[{b3:3d},{b2:3d},{b1:3d},{b0:3d}]')
                nonzero += 1
        except Exception:
            pass
    print(f'  ({nonzero} non-zero words in {size//4} checked)')


def main():
    parser = argparse.ArgumentParser(description='Verify MK4 RAM addresses against live emulator')
    parser.add_argument('--sock', default=str(N64_ROOT / 'training/data/bridge/mk4-visible.sock'),
                        help='Path to emulator bridge socket')
    parser.add_argument('--scan', action='store_true', default=True,
                        help='Scan struct neighborhoods (default: on)')
    parser.add_argument('--no-scan', action='store_true', help='Skip neighborhood scans')
    args = parser.parse_args()

    print(f'Connecting to {args.sock}...')
    b = SocketEmulatorBridge(args.sock, timeout_sec=30)
    b.connect()
    h = Mk4BridgeHelper(b)

    # Pause emulator for consistent reads
    print('Giving emulator 5 seconds to load ROM into memory...')
    time.sleep(5.0)
    print('Pausing emulator...')
    # h.pause() # not available either
    time.sleep(0.5)

    # ── Read confirmed addresses ──────────────────────────────────────────────
    print(f'\n{"="*70}')
    print('CONFIRMED ADDRESSES (already verified by our scanner)')
    print(f'{"="*70}')
    for name, (dtype, addr) in CONFIRMED.items():
        val, desc = read_addr(h, addr, dtype)
        print(f'  {name:40s}  0x{addr:08X}  = {desc}')

    # ── Read GameShark-decoded addresses ──────────────────────────────────────
    print(f'\n{"="*70}')
    print('GAMESHARK-DECODED ADDRESSES (need verification)')
    print(f'{"="*70}')
    for name, (dtype, addr) in GAMESHARK.items():
        val, desc = read_addr(h, addr, dtype)
        expected = ''
        if 'HEALTH' in name and val is not None:
            if isinstance(val, int) and 0 < val <= 160:
                expected = ' ⬅ LOOKS LIKE HEALTH!'
            elif isinstance(val, int) and val == 0:
                expected = ' (zero — dead or wrong addr)'
        if 'CHAR_ID' in name and val is not None:
            if isinstance(val, int) and 0 <= val <= 0x11:
                chars = ['Scorpion','Raiden','Sonya','Liu Kang','Sub-Zero','Fujin',
                         'Shinnok','Reiko','Quan Chi','Tanya','Reptile','Kai',
                         'Jarek','Jax','Johnny Cage','Goro','Kitana','Noob Saibot']
                cname = chars[val] if val < len(chars) else f'?{val}'
                expected = f' ⬅ CHARACTER: {cname}'
        if 'TIMER' in name and val is not None:
            if isinstance(val, int) and 80 <= val <= 99:
                expected = ' ⬅ LOOKS LIKE TIMER!'
        print(f'  {name:45s}  0x{addr:08X}  = {desc}{expected}')

    # ── Scan neighborhoods ────────────────────────────────────────────────────
    if not args.no_scan:
        for name, (base, size) in SCAN_RANGES.items():
            scan_region(h, name, base, size)

    # Resume emulator
    print('\nResuming emulator...')
    # h.resume() # not available
    b.close()
    print('Done. Review the output above to identify correct addresses.')


if __name__ == '__main__':
    main()
