#!/usr/bin/env python3
"""
mk4_vs_savestate.py — Build a 2-Player VS mode round-start savestate for MK4.

In 2P VS mode, the game reads BOTH controller ports from GetKeys() — P2 input
actually works, unlike Arcade mode where P2 is CPU-controlled.

Usage:
    python3 training/scripts/mk4_vs_savestate.py

Produces:
    training/data/savestates/mk4_arcade/vs_sonya_vs_kai_round_start.st
    (P1 = Sonya, P2 = Kai — easy to swap via --p1 / --p2)

Boot sequence from title screen:
  Title → A (select Arcade) → Down → A (select 2 on 2) → charselect
  P1 selects character → P2 selects character → fight loads → save
"""
from __future__ import annotations

import argparse
import mmap
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

N64_ROOT = Path('/Users/ichiropractic/code/n64')
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

SOCK         = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
STATE_DIR    = N64_ROOT / 'training/data/savestates/mk4_arcade'
CHARSELECT_BASE = str(STATE_DIR / 'mk4_arcade_charselect_base.st')
INST         = 'reverse-visible'
ROM          = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
CFG_DIR      = str(N64_ROOT / f'.m64p/instances/{INST}/config')
M64P_BIN     = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB      = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
CUSTOM_INPUT = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
P1_FILE      = '/tmp/mk4_ctrl'
P2_FILE      = '/tmp/mk4_ctrl_p2'

# Character grid — (row, col) from Kai at (0,0)
CHAR_GRID = {
    'kai':        (0, 0), 'raiden':     (0, 1), 'shinnok':    (0, 2),
    'liukang':    (0, 3), 'reptile':    (0, 4), 'scorpion':   (1, 0),
    'jax':        (1, 1), 'reiko':      (1, 2), 'johnnycage': (1, 3),
    'jarek':      (1, 4), 'tanya':      (2, 0), 'fujin':      (2, 1),
    'subzero':    (2, 2), 'quanchi':    (2, 3), 'sonya':      (2, 4),
}

BTN_START = 1 << 4
BTN_A     = 1 << 7
BTN_B     = 1 << 6
BTN_RIGHT = 1 << 0
BTN_LEFT  = 1 << 1
BTN_DOWN  = 1 << 2
BTN_UP    = 1 << 3

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


# ── Controller mmap ───────────────────────────────────────────────────────────

class Ctrl:
    def __init__(self, path):
        with open(path, 'w+b') as f: f.write(b'\x00' * 4)
        self._f = open(path, 'r+b')
        self._m = mmap.mmap(self._f.fileno(), 4)
        self._set(0)

    def _set(self, btn, x=0, y=0):
        self._m.seek(0)
        self._m.write(struct.pack('<Hbb', btn & 0xFFFF, x, y))
        self._m.flush()

    def press(self, btn): self._set(btn)
    def release(self): self._set(0)
    def tap(self, btn, hold=0.08, settle=0.18):
        self.press(btn); time.sleep(hold); self.release(); time.sleep(settle)
    def close(self): self.release(); self._m.close(); self._f.close()


# ── Bridge helpers ────────────────────────────────────────────────────────────

def connect():
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    b = SocketEmulatorBridge(SOCK, timeout_sec=20)
    return b, Mk4BridgeHelper(b)


def frame_step(b, n, timeout=60):
    b.debugger_command(f'frame {n}', timeout_sec=timeout, output_tail_chars=20)


def btn_frame(ctrl, b, h, btn, frames=60):
    """Press btn, step exactly `frames` frames. Emulator ends up paused."""
    h.pause(); time.sleep(0.05)
    ctrl.press(btn)
    frame_step(b, frames)
    ctrl.release()
    frame_step(b, 10)
    h.run()   # return to running after each menu press


def launch() -> subprocess.Popen:
    os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
    time.sleep(1.5)
    try: os.remove(SOCK)
    except: pass
    log = open(N64_ROOT / 'training/data/position_scan/bridge.log', 'w')
    proc = subprocess.Popen(BRIDGE_CMD, stdout=log, stderr=log)
    deadline = time.time() + 50
    while time.time() < deadline:
        if os.path.exists(SOCK): break
        time.sleep(0.5)
    else:
        proc.terminate(); raise RuntimeError('Emulator socket never appeared')
    print(f'  [*] Emulator up (pid={proc.pid})')
    # Wait until bridge responds
    for _ in range(20):
        try:
            b, h = connect(); h.pause(); h.run(); b.close(); break
        except: time.sleep(2)
    time.sleep(2)
    return proc


def nav_charselect(ctrl, b, h, char: str, player: int):
    """Navigate P`player` cursor to `char` on the character select screen."""
    row, col = CHAR_GRID[char.lower()]
    print(f'  P{player} navigating to {char} (row={row}, col={col})')
    for _ in range(row): ctrl.tap(BTN_DOWN)
    for _ in range(col): ctrl.tap(BTN_RIGHT)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--p1', default='sonya', help='P1 character (default: sonya)')
    ap.add_argument('--p2', default='kai',   help='P2 character (default: kai)')
    ap.add_argument('--no-launch', action='store_true',
                    help='Skip emulator launch (assumes already running at charselect state)')
    args = ap.parse_args()

    p1_char = args.p1.lower().replace(' ', '').replace('-', '')
    p2_char = args.p2.lower().replace(' ', '').replace('-', '')
    if p1_char not in CHAR_GRID: raise ValueError(f'Unknown P1 char: {args.p1}')
    if p2_char not in CHAR_GRID: raise ValueError(f'Unknown P2 char: {args.p2}')

    out_path = STATE_DIR / f'vs_{p1_char}_vs_{p2_char}_round_start.st'
    print(f'\n[MK4 VS Savestate Builder]')
    print(f'  P1={p1_char}  P2={p2_char}')
    print(f'  Output → {out_path}\n')

    ctrl1 = Ctrl(P1_FILE)
    ctrl2 = Ctrl(P2_FILE)
    proc  = None

    try:
        if not args.no_launch:
            print('[0] Launching emulator…')
            proc = launch()

        b, h = connect()
        h.pause()

        if not args.no_launch:
            # ── Boot sequence from title screen ───────────────────────────────
            print('[1] Waiting 30s for splash screens…')
            h.run(); b.close()
            time.sleep(30)
            b, h = connect()

            print('[2] START → dismiss controller pak warning')
            btn_frame(ctrl1, b, h, BTN_START)

            print('[3] START → dismiss rumble pak warning')
            btn_frame(ctrl1, b, h, BTN_START)

            time.sleep(1)

            print('[4] A → select Arcade mode')
            btn_frame(ctrl1, b, h, BTN_A, frames=90)

            print('[5] DOWN → move cursor down to "Arcade 2 on 2"')
            btn_frame(ctrl1, b, h, BTN_DOWN, frames=60)

            print('[6] A → select 2 on 2 (VS mode)')
            btn_frame(ctrl1, b, h, BTN_A, frames=90)
        else:
            # Assume already at charselect with a 2P state loaded
            print('[*] --no-launch: assuming 2P charselect is already loaded')
            h.run(); time.sleep(0.5)

        # ── Character select (2P simultaneous) ────────────────────────────────
        print(f'\n[7] Character select:  P1={p1_char}  P2={p2_char}')
        h.run(); time.sleep(0.3)

        # Navigate P1 cursor
        nav_charselect(ctrl1, b, h, p1_char, player=1)

        # Navigate P2 cursor (P2 starts at Kai too in 2P mode)
        nav_charselect(ctrl2, b, h, p2_char, player=2)

        time.sleep(0.2)

        # Confirm both characters simultaneously
        print('[8] Both P1+P2 press A to confirm characters')
        ctrl1.press(BTN_A); ctrl2.press(BTN_A)
        time.sleep(0.15)
        ctrl1.release(); ctrl2.release()
        time.sleep(0.3)

        # Let fight load (1500 frames ≈ 25s at 60fps covers intro + FIGHT!)
        print('[9] Waiting for fight to load (advancing 1500 frames)…')
        h.pause(); time.sleep(0.05)
        frame_step(b, 1500, timeout=180)

        # Save
        print(f'\n[10] Saving VS round-start state → {out_path}')
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        resp = b.save_savestate_path(out_path)
        ok = resp.get('saved', False)
        print(f'  [{"✅ OK" if ok else "❌ FAIL"}] {out_path}')

        if ok:
            print(f'\n✅ VS savestate created!')
            print(f'   P2 controller will now work — emulator reads both ports in 2P mode.')
            print(f'   To use in scanning:')
            print(f'     python3 training/scripts/find_position_addrs.py \\')
            print(f'       --savestate training/data/savestates/mk4_arcade/vs_{p1_char}_vs_{p2_char}_round_start.st')

        h.run(); b.close()

    finally:
        ctrl1.close(); ctrl2.close()
        if proc:
            print(f'\n[*] Emulator running (pid={proc.pid}). Kill when done: kill {proc.pid}')


if __name__ == '__main__':
    main()
