#!/usr/bin/env python3
"""
mk4_charselect_handoff.py

Starts the game, lets YOU navigate to the character selection screen,
then takes over:
  - Saves state 1 (charselect base)
  - Navigates cursor to desired character
  - Selects it, sets max difficulty, saves state 2 (round start)

Grid (row, col):
  Row 0: Kai(0)      Raiden(1)  Shinnok(2)   Liu Kang(3) Reptile(4)
  Row 1: Scorpion(0) Jax(1)     Reiko(2)     J. Cage(3)  Jarek(4)
  Row 2: Tanya(0)    Fujin(1)   Sub-Zero(2)  Quan Chi(3) Sonya(4)

Usage:
  python3 training/scripts/mk4_charselect_handoff.py          # Kai
  python3 training/scripts/mk4_charselect_handoff.py --row 1 --col 2  # Reiko
"""
import sys, os, time, struct, mmap as mmap_mod, subprocess, glob, shutil, argparse
sys.path.insert(0, '/Users/ichiropractic/code/n64/training/src')
from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
from pathlib import Path

BTN_A     = (1 << 7)
BTN_RIGHT = (1 << 0)
BTN_DOWN  = (1 << 2)

SOCK         = '/Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock'
ROM          = '/Users/ichiropractic/code/n64/Mortal Kombat 4 (USA).z64'
SHOT_DIR     = '/Users/ichiropractic/code/n64/.m64p/data/screenshots'
OUT_DIR      = '/Users/ichiropractic/code/n64/training/data/mk4_run_out'
STATE_DIR    = '/Users/ichiropractic/code/n64/training/data/savestates/mk4_arcade'
INST         = 'reverse-visible'
CFG_DIR      = f'/Users/ichiropractic/code/n64/.m64p/instances/{INST}/config'
CUSTOM_INPUT = '/Users/ichiropractic/code/n64/vendor/n64train-input/n64train-input.dylib'
M64P_BIN     = '/Users/ichiropractic/code/n64/vendor/mupen64plus-ui-console/projects/unix/mupen64plus'
CORELIB      = '/Users/ichiropractic/code/n64/vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib'
CTRL_FILE    = '/tmp/mk4_ctrl'

BRIDGE_CMD = [
    'python3', '/Users/ichiropractic/code/n64/training/scripts/run_bridge_server.py',
    '--socket-path', SOCK, '--instance-id', INST,
    '--memory-reader', 'debugger-dump', '--rom-path', ROM,
    '--debugger-ui-binary', M64P_BIN, '--debugger-corelib', CORELIB,
    '--debugger-plugindir', '/opt/homebrew/lib/mupen64plus',
    '--debugger-configdir', CFG_DIR, '--debugger-datadir', '/opt/homebrew/share/mupen64plus',
    '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
    '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
    '--debugger-input-plugin', CUSTOM_INPUT,
    '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
    '--debugger-emumode', '0',
]

CHAR_NAMES = [
    ['Kai', 'Raiden', 'Shinnok', 'Liu Kang', 'Reptile'],
    ['Scorpion', 'Jax', 'Reiko', 'Johnny Cage', 'Jarek'],
    ['Tanya', 'Fujin', 'Sub-Zero', 'Quan Chi', 'Sonya'],
]

class N64Controller:
    def __init__(self):
        with open(CTRL_FILE, 'w+b') as f:
            f.write(b'\x00' * 4)
        self._f   = open(CTRL_FILE, 'r+b')
        self._mem = mmap_mod.mmap(self._f.fileno(), 4)
        self._set(0)

    def _set(self, buttons: int):
        self._mem.seek(0)
        self._mem.write(struct.pack('<Hbb', buttons & 0xFFFF, 0, 0))
        self._mem.flush()

    def press(self, buttons: int): self._set(buttons)
    def release(self):             self._set(0)
    def close(self):
        self.release(); self._mem.close(); self._f.close()

def kill():
    os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
    time.sleep(2)
    try: os.remove(SOCK)
    except: pass

def start_bridge():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    proc = subprocess.Popen(BRIDGE_CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline:
        if os.path.exists(SOCK):
            print(f'  Bridge pid={proc.pid}')
            return proc
        time.sleep(0.5)
    raise RuntimeError('Bridge socket never appeared')

def connect():
    return SocketEmulatorBridge(SOCK, timeout_sec=20), None

def connect_full():
    b = SocketEmulatorBridge(SOCK, timeout_sec=20)
    h = Mk4BridgeHelper(b)
    return b, h

_n = [0]
def screenshot(b, h, label):
    before = set(glob.glob(f'{SHOT_DIR}/*.png'))
    h.run(); time.sleep(0.4)
    b.debugger_command('screenshot', timeout_sec=5, output_tail_chars=20)
    time.sleep(0.8); h.pause()
    after = set(glob.glob(f'{SHOT_DIR}/*.png'))
    new = sorted(after - before, key=os.path.getmtime)
    if new:
        dst = f'{OUT_DIR}/{_n[0]:02d}_{label}.png'
        shutil.copy2(new[-1], dst); _n[0] += 1
        print(f'  [ss] {dst}')

def btn(ctrl, b, h, buttons, frames=60):
    h.pause()
    ctrl.press(buttons)
    b.debugger_command(f'frame {frames}', timeout_sec=30, output_tail_chars=20)
    ctrl.release()
    b.debugger_command('frame 10', timeout_sec=10, output_tail_chars=20)

def save_st(b, h, path):
    h.pause()
    resp = b.save_savestate_path(Path(path))
    ok = resp.get('saved', False)
    print(f'  [save] {"OK ✅" if ok else "FAIL ❌"} → {path}')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--col', type=int, default=0)
    args = parser.parse_args()
    char = CHAR_NAMES[args.row][args.col]

    print(f'Target character: {char} (row={args.row}, col={args.col})\n')

    kill()
    print('Starting bridge + emulator...')
    start_bridge()

    # Connect to start emulator, then let game run freely
    b = SocketEmulatorBridge(SOCK, timeout_sec=20)
    h = Mk4BridgeHelper(b)
    h.run()   # ← game is now running, you can play
    b.close()
    print('✅ Emulator running — game window should be visible.')

    print('\n' + '='*60)
    print('🎮  Navigate to the CHARACTER SELECTION SCREEN:')
    print('    Arcade → skip rumble → 1 on 1 → char select grid')
    print('    Then come back here and press ENTER.')
    print('='*60)
    input('\n>>> Press ENTER when you are at char select: ')

    print('\nTaking over...')
    ctrl = N64Controller()
    b, h = connect_full()

    h.pause()
    s = h.get_menu_screen_state()['value']
    print(f'  state={s}')
    screenshot(b, h, '00_charselect')

    # Save state 1: charselect base
    print('\n[1] Saving charselect base state...')
    save_st(b, h, f'{STATE_DIR}/mk4_arcade_charselect_base.st')

    # Navigate cursor to character
    print(f'\n[2] Moving cursor to {char} (↓×{args.row} →×{args.col})')
    for _ in range(args.row):
        btn(ctrl, b, h, BTN_DOWN, frames=15)
    for _ in range(args.col):
        btn(ctrl, b, h, BTN_RIGHT, frames=15)
    screenshot(b, h, f'01_{char.lower().replace(" ","_")}')

    # Confirm character
    print(f'[3] A → select {char}')
    btn(ctrl, b, h, BTN_A, frames=60)
    screenshot(b, h, f'02_{char.lower().replace(" ","_")}_selected')

    # Max difficulty: Right x4
    print('[4] Right ×4 → max difficulty')
    for _ in range(4):
        btn(ctrl, b, h, BTN_RIGHT, frames=20)
    screenshot(b, h, '03_max_difficulty')

    # Confirm → fight loads
    print('[5] A → confirm fight (waiting 8s)...')
    btn(ctrl, b, h, BTN_A, frames=60)
    ctrl.release(); b.close()
    time.sleep(8)

    b, h = connect_full()
    screenshot(b, h, '04_fight_start')

    # Save state 2: round start
    slug = char.lower().replace(' ', '_')
    print(f'\n[6] Saving round start state...')
    save_st(b, h, f'{STATE_DIR}/mk4_arcade_{slug}_round_start.st')
    screenshot(b, h, '05_savestate2')

    ctrl.close(); h.run(); b.close()
    print(f'\n✅ Done!')
    print(f'  Screenshots → {OUT_DIR}')
    print(f'  Savestates  → {STATE_DIR}')

if __name__ == '__main__':
    main()
