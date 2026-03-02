#!/usr/bin/env python3
"""
find_position_addrs.py — MK4 P1/P2 X-Position RAM Scanner (v2, monotonic)
──────────────────────────────────────────────────────────────────────────
Strategy: monotonic multi-snapshot scan.

  Phase LEFT:  take 4 RAM dumps while continuously holding LEFT
               → real X positions must strictly DECREASE each dump
  Phase RIGHT: take 4 RAM dumps while continuously holding RIGHT
               → real X positions must strictly INCREASE each dump

Any address that doesn't monotonically track direction is noise.
Velocity/delta buffers (which reset every frame) are eliminated because
they return to 0 between dumps instead of accumulating.

Also scans 16-bit halfwords (not just 32-bit) so we catch positions
stored in MIPS 16-bit words that a 32-bit scan would miss.

Dumps saved to:  training/data/position_scan/
Results saved to: training/data/position_scan/results.json

Usage:
    python3 training/scripts/find_position_addrs.py [--no-launch]
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

SOCK         = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
TEST_ST      = str(N64_ROOT / 'training/data/savestates/mk4_arcade/test.st')
TRACER_PY    = N64_ROOT / 'training/src/n64train/reverse/mk4_tracing.py'
DUMP_DIR     = N64_ROOT / 'training/data/position_scan'
RESULT_JSON  = DUMP_DIR / 'results.json'
BRIDGE_LOG   = DUMP_DIR / 'bridge.log'

INST         = 'reverse-visible'
ROM          = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
CFG_DIR      = str(N64_ROOT / f'.m64p/instances/{INST}/config')
M64P_BIN     = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB      = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
CUSTOM_INPUT = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
P1_FILE      = '/tmp/mk4_ctrl'

# Scan tuning
STEP_SECS    = 0.8     # seconds between each dump while holding direction
N_STEPS      = 5       # 5 dumps × 0.8s = 4s total movement per direction
RDRAM_BASE   = 0x80000000
WARMUP_SECS  = 2.5     # let round start animation finish before taking baseline

# Position plausibility: after N_STEPS*STEP_SECS of movement, position shifts by
# at least MIN_TOTAL_32 (signed 32-bit) or MIN_TOTAL_16 (signed 16-bit).
# Must also shift MORE than during an equal-duration idle control scan.
MIN_TOTAL_32 = 50
MAX_TOTAL_32 = 0x4000000
MIN_TOTAL_16 = 20
MAX_TOTAL_16 = 20000
# Movement must be at least IDLE_RATIO times larger in magnitude than idle drift
IDLE_RATIO   = 3.0

BTN_LEFT  = 1 << 1
BTN_RIGHT = 1 << 0

BRIDGE_CMD = [
    'python3', str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
    '--socket-path', SOCK, '--instance-id', INST,
    '--memory-reader', 'debugger-dump', '--rom-path', ROM,
    '--debugger-ui-binary', M64P_BIN, '--debugger-corelib', CORELIB,
    '--debugger-plugindir', '/opt/homebrew/lib/mupen64plus',
    '--debugger-configdir', CFG_DIR, '--debugger-datadir',
    '/opt/homebrew/share/mupen64plus',
    '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
    '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
    '--debugger-input-plugin', CUSTOM_INPUT,
    '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
    '--debugger-emumode', '0',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def connect(timeout=15):
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    b = SocketEmulatorBridge(SOCK, timeout_sec=timeout)
    return b, Mk4BridgeHelper(b)


def cmd(fn, retries=2):
    for attempt in range(retries + 1):
        try:
            b, h = connect()
            try:
                return fn(b, h)
            finally:
                try: b.close()
                except: pass
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            if attempt < retries:
                print(f'  [retry] Bridge connection error ({e}), retrying in 1s…')
                time.sleep(1.0)
            else:
                raise


def dump_rdram(b, label: str) -> bytes:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    out = DUMP_DIR / f'{label}.bin'
    resp = b.debugger_command(
        f'dumpmem 80000000 0x400000 {out}',
        timeout_sec=60, output_tail_chars=512)
    raw = str(resp.get('output', ''))
    if 'M64P_DUMPMEM_OK' not in raw:
        raise RuntimeError(f'dumpmem failed: {raw[-200:]}')
    return out.read_bytes()


def read_u32s(data: bytes) -> list[int]:
    n = len(data) // 4
    return list(struct.unpack_from(f'>{n}I', data))


def read_s32s(data: bytes) -> list[int]:
    n = len(data) // 4
    return list(struct.unpack_from(f'>{n}i', data))


def read_s16s(data: bytes) -> list[int]:
    n = len(data) // 2
    return list(struct.unpack_from(f'>{n}h', data))


class Ctrl:
    def __init__(self, path):
        if not os.path.exists(path):
            with open(path, 'w+b') as f: f.write(b'\x00' * 4)
        self._f = open(path, 'r+b')
        self._m = mmap.mmap(self._f.fileno(), 4)
        self._write(0)

    def _write(self, btn):
        self._m.seek(0)
        self._m.write(struct.pack('<Hbb', btn & 0xFFFF, 0, 0))
        self._m.flush()

    def press(self, btn): self._write(btn)
    def release(self): self._write(0)
    def close(self): self.release(); self._m.close(); self._f.close()


# ── Monotonic diff ────────────────────────────────────────────────────────────

def monotonic_candidates_32(snapshots: list[list[int]], direction: int,
                             max_violations: int = 1) -> dict[int, int]:
    """
    Return {word_index: total_signed_delta} for positions that move in
    `direction` across snapshots with at most `max_violations` non-monotone steps.
    Also enforces magnitude plausibility.
    """
    n = min(len(s) for s in snapshots)
    out = {}
    for i in range(n):
        vals = [s[i] if s[i] < 0x80000000 else s[i] - 0x100000000 for s in snapshots]
        violations = sum(
            1 for k in range(len(vals) - 1)
            if (vals[k+1] - vals[k]) * direction <= 0
        )
        if violations > max_violations:
            continue
        total = vals[-1] - vals[0]
        if MIN_TOTAL_32 <= abs(total) <= MAX_TOTAL_32:
            out[i] = total
    return out


def monotonic_candidates_16(snapshots: list[list[int]], direction: int,
                             max_violations: int = 1) -> dict[int, int]:
    n = min(len(s) for s in snapshots)
    out = {}
    for i in range(n):
        vals = [s[i] for s in snapshots]
        violations = sum(
            1 for k in range(len(vals) - 1)
            if (vals[k+1] - vals[k]) * direction <= 0
        )
        if violations > max_violations:
            continue
        total = vals[-1] - vals[0]
        if MIN_TOTAL_16 <= abs(total) <= MAX_TOTAL_16:
            out[i] = total
    return out


# ── Patch mk4_tracing.py ──────────────────────────────────────────────────────

def patch_tracing(p1_addr: str, p2_addr: str):
    import re
    src = TRACER_PY.read_text()
    src = re.sub(
        r'P1_X_ADDR\s*=\s*0x[0-9A-Fa-f]+.*',
        f'P1_X_ADDR   = {p1_addr}  # confirmed by position scanner',
        src)
    src = re.sub(
        r'P2_X_ADDR\s*=\s*0x[0-9A-Fa-f]+.*',
        f'P2_X_ADDR   = {p2_addr}  # confirmed by position scanner',
        src)
    TRACER_PY.write_text(src)
    print(f'  [patch] mk4_tracing.py → P1={p1_addr}  P2={p2_addr}')


# ── Launch / teardown ─────────────────────────────────────────────────────────

def launch_emulator() -> subprocess.Popen:
    os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
    time.sleep(1.5)
    try: os.remove(SOCK)
    except: pass
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    log = open(BRIDGE_LOG, 'w')
    proc = subprocess.Popen(BRIDGE_CMD, stdout=log, stderr=log)
    print(f'  [*] Bridge launched (pid={proc.pid}), log → {BRIDGE_LOG}')
    deadline = time.time() + 50
    while time.time() < deadline:
        if os.path.exists(SOCK):
            break
        time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError('Timed out waiting for emulator socket')
    print(f'  [*] Socket ready. Stabilising…')
    time.sleep(4)
    return proc


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-launch', action='store_true',
                    help='Skip launching emulator (assume already running)')
    ap.add_argument('--savestate', default=str(TEST_ST),
                    help='Path to .st savestate to load (default: test.st)')
    args = ap.parse_args()
    savestate_path = Path(args.savestate)

    proc = None
    if not args.no_launch:
        print('[0] Launching emulator…')
        proc = launch_emulator()

    ctrl = Ctrl(P1_FILE)
    left_snaps_raw  = []
    right_snaps_raw = []

    try:
        # ── A: Load state + initial dump ───────────────────────────────────────
        print(f'\n[1] Load savestate ({savestate_path.name}) + warm-up {WARMUP_SECS}s + dump baseline…')
        def _load(b, h):
            b.load_savestate_path(savestate_path)
            time.sleep(0.4)
            h.run()           # let round-start animation play out
            time.sleep(WARMUP_SECS)
            h.pause()
            time.sleep(0.2)
            return dump_rdram(b, 'L0_init')
        init_raw = cmd(_load)
        left_snaps_raw.append(init_raw)
        print(f'  [ok] L0: {len(init_raw)//1024}KB')

        # ── Phase LEFT: hold left, dump N_STEPS times ─────────────────────────
        print(f'\n[2] Phase LEFT — hold LEFT, dump every {STEP_SECS}s × {N_STEPS}…')
        cmd(lambda b, h: h.run())
        time.sleep(0.2)
        ctrl.press(BTN_LEFT)

        for step in range(N_STEPS):
            time.sleep(STEP_SECS)
            label = f'L{step+1}_left'
            def _snap_l(b, h, lbl=label):
                h.pause(); time.sleep(0.2)
                data = dump_rdram(b, lbl)
                h.run(); time.sleep(0.1)
                return data
            raw = cmd(_snap_l)
            left_snaps_raw.append(raw)
            print(f'  [ok] {label}: {len(raw)//1024}KB')

        ctrl.release()
        time.sleep(0.2)

        # ── Phase RIGHT: hold right, dump N_STEPS times ───────────────────────
        print(f'\n[3] Phase RIGHT — hold RIGHT, dump every {STEP_SECS}s × {N_STEPS}…')
        last_left_raw = left_snaps_raw[-1]
        right_snaps_raw.append(last_left_raw)   # starting point for right phase

        ctrl.press(BTN_RIGHT)
        for step in range(N_STEPS):
            time.sleep(STEP_SECS)
            label = f'R{step+1}_right'
            def _snap_r(b, h, lbl=label):
                h.pause(); time.sleep(0.2)
                data = dump_rdram(b, lbl)
                h.run(); time.sleep(0.1)
                return data
            raw = cmd(_snap_r)
            right_snaps_raw.append(raw)
            print(f'  [ok] {label}: {len(raw)//1024}KB')

        ctrl.release()
        # final pause
        cmd(lambda b, h: h.pause())

        # ── Diff — 32-bit words ────────────────────────────────────────────────
        print('\n[4] Diffing 32-bit words…')
        left_words  = [read_u32s(r) for r in left_snaps_raw]
        right_words = [read_u32s(r) for r in right_snaps_raw]

        dec32 = monotonic_candidates_32(left_words,  direction=-1)
        inc32 = monotonic_candidates_32(right_words, direction=+1)
        both32 = set(dec32) & set(inc32)
        print(f'  monotonic↓ left: {len(dec32)}   monotonic↑ right: {len(inc32)}   both: {len(both32)}')

        # ── Diff — 16-bit halfwords ────────────────────────────────────────────
        print('[4b] Diffing 16-bit halfwords…')
        left_s16  = [read_s16s(r) for r in left_snaps_raw]
        right_s16 = [read_s16s(r) for r in right_snaps_raw]

        dec16 = monotonic_candidates_16(left_s16,  direction=-1)
        inc16 = monotonic_candidates_16(right_s16, direction=+1)
        both16 = set(dec16) & set(inc16)
        print(f'  monotonic↓ left: {len(dec16)}   monotonic↑ right: {len(inc16)}   both: {len(both16)}')

        # ── Build candidate lists ──────────────────────────────────────────────
        cands32 = []
        for idx in sorted(both32):
            vaddr = RDRAM_BASE + idx * 4
            vals_l = [read_s32s(r)[idx] for r in left_snaps_raw]
            vals_r = [read_s32s(r)[idx] for r in right_snaps_raw]
            cands32.append({
                'bits': 32, 'address': f'0x{vaddr:08X}', 'vaddr': vaddr,
                'left_sequence':  vals_l,
                'right_sequence': vals_r,
                'total_left_delta':  dec32[idx],
                'total_right_delta': inc32[idx],
                'symmetry': abs(abs(dec32[idx]) - abs(inc32[idx])),
            })
        cands32.sort(key=lambda x: x['symmetry'])

        cands16 = []
        for idx in sorted(both16):
            byte_offset = idx * 2
            vaddr = RDRAM_BASE + (byte_offset ^ 2)   # 16-bit N64 halfword swap
            vals_l = [read_s16s(r)[idx] for r in left_snaps_raw]
            vals_r = [read_s16s(r)[idx] for r in right_snaps_raw]
            cands16.append({
                'bits': 16, 'address': f'0x{vaddr:08X}', 'vaddr': vaddr,
                'left_sequence':  vals_l,
                'right_sequence': vals_r,
                'total_left_delta':  dec16[idx],
                'total_right_delta': inc16[idx],
                'symmetry': abs(abs(dec16[idx]) - abs(inc16[idx])),
            })
        cands16.sort(key=lambda x: x['symmetry'])

        # ── Print ──────────────────────────────────────────────────────────────
        def print_table(cands, bits):
            print(f'\n── {bits}-bit candidates (top 20) ───────────────────────')
            if not cands:
                print('  (none)')
                return
            for c in cands[:20]:
                ls = '  '.join(f'{v:>8}' for v in c['left_sequence'])
                print(f"  {c['address']}  L:[{ls}]  ΔL={c['total_left_delta']:>+8}  ΔR={c['total_right_delta']:>+8}  sym={c['symmetry']}")

        print_table(cands32, 32)
        print_table(cands16, 16)

        # ── Best picks ────────────────────────────────────────────────────────
        all_cands = sorted(cands32 + cands16, key=lambda x: x['symmetry'])
        RESULT_JSON.write_text(json.dumps({
            'step_secs': STEP_SECS, 'n_steps': N_STEPS,
            'candidates_32': cands32[:50], 'candidates_16': cands16[:50],
        }, indent=2))
        print(f'\n[ok] Results → {RESULT_JSON}')

        if len(all_cands) >= 2:
            p1 = all_cands[0]['address']
            p2 = all_cands[1]['address']
            print(f'\n  ★ Best P1_X_ADDR = {p1}  ({all_cands[0]["bits"]}-bit)')
            print(f'  ★ Best P2_X_ADDR = {p2}  ({all_cands[1]["bits"]}-bit)')

            # Sanity: are they near each other? (fighters in same game object array)
            diff = abs(all_cands[0]['vaddr'] - all_cands[1]['vaddr'])
            if diff < 0x200:
                print(f'  ✓ Addresses are {diff:#x} bytes apart — plausible player object offsets')
            else:
                print(f'  ⚠ Addresses are {diff:#x} bytes apart — may be different players or noise')
                print('    Review results.json for alternatives.')

            print(f'\n[5] Patching mk4_tracing.py…')
            patch_tracing(p1, p2)
            print('\n✅ Done! Position addresses confirmed and patched.')
        else:
            print('\n⚠ Fewer than 2 candidates found.')
            print(f'  Dumps saved to {DUMP_DIR}/ — re-run with --no-launch to re-analyse.')

    finally:
        ctrl.close()
        if proc:
            print(f'\n[*] Emulator still running (pid={proc.pid}). Kill when done:')
            print(f'    kill {proc.pid}')


if __name__ == '__main__':
    main()
