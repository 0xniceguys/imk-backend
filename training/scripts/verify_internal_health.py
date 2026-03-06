#!/usr/bin/env python3
"""
verify_internal_health.py — Frame-accurate health address finder.

Uses pause/step/read cycle with button TOGGLING to trigger repeated attacks.
MK4 requires press-release-press for each punch (holding = one punch only).

Strategy:
  1. Load savestate, advance to init fight
  2. Walk right to close distance (10 steps, ~5s)
  3. Toggle Low Punch every 2 steps for 30 steps (~15s)
  4. Scan all regions for addresses that start at 160 and decrease

Usage:
    python3 training/scripts/verify_internal_health.py \
        --sock training/data/bridge/mk4-health-test.sock \
        --ctrl-path /tmp/mk4_ctrl_test_p1
"""
from __future__ import annotations

import argparse
import mmap
import os
import struct
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))
sys.path.insert(0, str(N64_ROOT / 'training' / 'scripts'))

from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
from n64train.runtime.actions import Button, ControllerState
from mk4_train import _BTN


def write_ctrl(ctrl_state: ControllerState, path: str) -> None:
    mask = 0
    for btn in ctrl_state.pressed:
        mask |= _BTN.get(btn, 0)
    x = int(ctrl_state.analog_x * 80) & 0xFF
    y = int(ctrl_state.analog_y * 80) & 0xFF
    if not os.path.exists(path):
        with open(path, 'w+b') as f:
            f.write(b'\x00' * 4)
    with open(path, 'r+b') as f:
        m = mmap.mmap(f.fileno(), 4)
        m.seek(0)
        m.write(struct.pack('<Hbb', mask & 0xFFFF, x, y))
        m.flush()
        m.close()


# ── Addresses to trace ────────────────────────────────────────────────────────
TRACE_ADDRS = {
    'DSP_P1':    ('u8',  0x8036E729),
    'DSP_P2':    ('u8',  0x8036E72E),
    'TIMER':     ('u8',  0x80105118),
    'P1_X':      ('s16', 0x800F87F8),
    'P2_X':      ('s16', 0x8006A060),
    # Attack state tracking
    'P1_ATK':    ('u32', 0x800FE090),   # 0=idle, 69422=LP, 67956=HP
    'P1_GND':    ('u32', 0x800FE0F8),   # 4=on_ground, 1=airborne
    'P1_HST':    ('u32', 0x800FE310),   # non-zero during active hitbox
    # GameShark health (u16) — for reference
    'GS_P1':     ('u16', 0x800FE0D8),
    'GS_P2':     ('u16', 0x80126F54),
    # Byte-level around GS health region
    'FE0D8':     ('u8',  0x800FE0D8),
    'FE0D9':     ('u8',  0x800FE0D9),
    'FE0DA':     ('u8',  0x800FE0DA),
    'FE0DB':     ('u8',  0x800FE0DB),
    # P2 equivalents
    '26ED8':     ('u8',  0x80126ED8),
    '26ED9':     ('u8',  0x80126ED9),
    '26EDA':     ('u8',  0x80126EDA),
    '26EDB':     ('u8',  0x80126EDB),
    # P2 GS health region bytes
    '26F54':     ('u8',  0x80126F54),
    '26F55':     ('u8',  0x80126F55),
    '26F56':     ('u8',  0x80126F56),
    '26F57':     ('u8',  0x80126F57),
}

# Scan regions — LARGE to catch health wherever it might be
# Focus on P1 and P2 struct regions with extended neighborhoods
SCAN_REGIONS = {
    'P1_STRUCT':  (0x800FE000, 0x1000),   # 4KB around P1 struct
    'P2_STRUCT':  (0x80126E00, 0x1000),   # 4KB around P2 struct
    'DISPLAY':    (0x8036E700, 0x50),      # Display region
    'TIMER_RGN':  (0x80104F00, 0x300),     # Timer neighborhood
}


def read_u8(h, addr):
    try: return h.read_u8(addr)
    except: return None

def read_u32(h, addr):
    try: return h.read_u32(addr)
    except: return None

def read_u16(h, addr):
    try:
        w = h.read_u32(addr & ~3)
        off = addr & 3
        if off == 0: return (w >> 16) & 0xFFFF
        elif off == 2: return w & 0xFFFF
        else: return (w >> (8 * (3 - off))) & 0xFFFF
    except: return None

def read_s16hi(h, addr):
    try:
        w = h.read_u32(addr)
        hi = (w >> 16) & 0xFFFF
        return hi if hi < 0x8000 else hi - 0x10000
    except: return None


def read_trace(h):
    result = {}
    for name, (dtype, addr) in TRACE_ADDRS.items():
        if dtype == 'u8':    result[name] = read_u8(h, addr)
        elif dtype == 'u16': result[name] = read_u16(h, addr)
        elif dtype == 'u32': result[name] = read_u32(h, addr)
        elif dtype == 's16': result[name] = read_s16hi(h, addr)
        else: result[name] = None
    return result


def dump_regions_u8(h):
    result = {}
    for _, (base, size) in SCAN_REGIONS.items():
        for off in range(size):
            v = read_u8(h, base + off)
            if v is not None:
                result[base + off] = v
    return result


def find_candidates(snapshots, min_start=150, max_start=170):
    if len(snapshots) < 3:
        return []
    first = snapshots[0]
    results = []
    for addr, start_val in first.items():
        if not (min_start <= start_val <= max_start):
            continue
        values = [snap.get(addr) for snap in snapshots]
        if any(v is None for v in values):
            continue
        end_val = values[-1]
        delta = end_val - start_val
        increases = sum(1 for i in range(1, len(values)) if values[i] > values[i-1])
        results.append({
            'addr': addr, 'start': start_val, 'end': end_val,
            'delta': delta, 'increases': increases,
            'values': values,
        })
    results.sort(key=lambda c: (c['delta'], c['increases']))
    return results


def frame_step(h, n_frames):
    try:
        resp = h.bridge.debugger_command(f'frame {n_frames}', timeout_sec=30.0)
        output = str(resp.get('output', ''))
        return 'M64P_FRAME_OK' in output or 'run' in output
    except Exception as e:
        print(f'    frame error: {e}')
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sock', default=str(N64_ROOT / 'training/data/bridge/mk4-health-test.sock'))
    parser.add_argument('--ctrl-path', default='/tmp/mk4_ctrl_test_p1')
    parser.add_argument('--savestate', default='')
    parser.add_argument('--fps', type=int, default=30, help='Frames per step')
    args = parser.parse_args()

    ctrl_path = args.ctrl_path
    fps = args.fps
    print(f'Ctrl path: {ctrl_path}')

    if args.savestate:
        save_path = Path(args.savestate)
    else:
        candidates = [
            N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st',
            N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st',
        ]
        save_path = next((p for p in candidates if p.exists()), None)
        if not save_path:
            print('ERROR: no savestate found'); sys.exit(1)
    print(f'Savestate: {save_path}')

    b = SocketEmulatorBridge(args.sock, timeout_sec=120)
    b.connect()
    h = Mk4BridgeHelper(b)
    print('Connected')

    # Wait for ROM
    for _ in range(30):
        try:
            h.read_u8(0x80105118)
            print('ROM OK')
            break
        except: time.sleep(1.0)

    # ── Load savestate ────────────────────────────────────────────────────────
    try: h.pause()
    except: pass
    time.sleep(0.5)

    for attempt in range(5):
        try:
            b.load_savestate_path(save_path)
            print(f'Savestate loaded (attempt {attempt+1})')
            break
        except Exception as e:
            print(f'Stateload attempt {attempt+1} failed: {e}')
            time.sleep(2.0)
    else:
        print('FATAL: stateload'); sys.exit(1)

    # Init: neutral, advance 120 frames
    write_ctrl(ControllerState(), ctrl_path)
    frame_step(h, 120)
    try: h.pause()
    except: pass
    time.sleep(0.3)

    baseline = read_trace(h)
    print(f'\nBASELINE:')
    for name, val in baseline.items():
        print(f'  {name:12s} = {val}')

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE: Toggle punches from starting position (dist=3, already in range)
    # P1 at x=-2, P2 at x=1. No walk needed — just punch!
    # Alternate: punch+walk_right (close gap) and release every 2 steps.
    # ══════════════════════════════════════════════════════════════════════════
    print(f'\n{"="*90}')
    print('PUNCH PHASE: Toggle punches from starting position (40 steps)')
    print(f'{"="*90}')

    # Take pre-attack snapshot
    pre_snap = dump_regions_u8(h)
    snapshots = [pre_snap]
    traces = [read_trace(h)]

    # Punch in place (no walk — stay in range). Alternate press/release.
    punch_on = ControllerState(pressed=frozenset([Button.A]))   # punch
    punch_off = ControllerState()                                # release to re-trigger

    # Compact header
    hdr = ['TIMER', 'DSP_P1', 'DSP_P2', 'P1_X', 'P2_X', 'P1_ATK', 'P1_HST',
           'FE0D8', 'FE0D9', 'FE0DA', 'FE0DB', '26ED8', '26ED9', '26EDA', '26EDB',
           '26F54', '26F55', '26F56', '26F57', 'GS_P1', 'GS_P2']
    print(f'  {"step":>4s}  {"input":>5s}  ' + '  '.join(f'{n:>7s}' for n in hdr))
    print(f'  {"-"*200}')

    for step in range(40):
        # Toggle: even steps = punch, odd steps = release
        if step % 2 == 0:
            ctrl = punch_on
            label = 'PUNCH'
        else:
            ctrl = punch_off
            label = '  ---'

        write_ctrl(ctrl, ctrl_path)
        try: h.run()
        except: pass
        frame_step(h, fps)
        try: h.pause()
        except: pass
        time.sleep(0.2)

        trace = read_trace(h)
        snap = dump_regions_u8(h)
        traces.append(trace)
        snapshots.append(snap)

        vals = '  '.join(f'{trace.get(n, "?"):>7}' for n in hdr)
        print(f'  {step:4d}  {label}  {vals}')

        # Early exit if fight ended
        if trace.get('DSP_P1') == 0 and trace.get('DSP_P2') == 0:
            print(f'  Fight ended at step {step}')
            break

    write_ctrl(ControllerState(), ctrl_path)

    # ══════════════════════════════════════════════════════════════════════════
    # ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    print(f'\n{"="*90}')
    print('ANALYSIS')
    print(f'{"="*90}')

    # Trace changes
    print(f'\n  TRACE CHANGES (attack phase):')
    for name in TRACE_ADDRS:
        values = [t.get(name) for t in traces]
        if all(v == values[0] for v in values):
            continue
        # Compact: show unique transitions
        compact = [values[0]]
        for v in values[1:]:
            if v != compact[-1]:
                compact.append(v)
        print(f'    {name:12s}: {" > ".join(str(v) for v in compact)}')

    # Scan candidates
    print(f'\n  SCAN CANDIDATES (start 150-170, decreased):')
    candidates = find_candidates(snapshots)
    if candidates:
        print(f'  {"addr":>12s}  {"start":>5s}  {"end":>5s}  {"delta":>5s}  {"inc":>3s}  values')
        print(f'  {"-"*80}')
        for c in candidates[:30]:
            vals = '>'.join(str(v) for v in c['values'][:15])
            marker = ' *** GOOD ***' if c['increases'] == 0 and c['delta'] < -5 else ''
            print(f'  0x{c["addr"]:08X}  {c["start"]:5d}  {c["end"]:5d}  {c["delta"]:+5d}  {c["increases"]:3d}  {vals}{marker}')
    else:
        print('  None (150-170)')
        broad = find_candidates(snapshots, min_start=100, max_start=200)
        if broad:
            print(f'\n  BROAD (100-200):')
            for c in broad[:20]:
                vals = '>'.join(str(v) for v in c['values'][:15])
                marker = ' *** GOOD ***' if c['increases'] == 0 and c['delta'] < -5 else ''
                print(f'  0x{c["addr"]:08X}  {c["start"]:5d}  {c["end"]:5d}  {c["delta"]:+5d}  {c["increases"]:3d}  {vals}{marker}')

    b.close()
    print('\nDone.')


if __name__ == '__main__':
    main()
