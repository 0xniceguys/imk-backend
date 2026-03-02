#!/usr/bin/env python3
"""
mk4_arcade_run.py — MK4 Arcade savestate builder.

Modes:
  python3 mk4_arcade_run.py                          # full boot -> save1 -> save2 (Kai)
  python3 mk4_arcade_run.py --character=Scorpion     # full boot -> save1 -> save2 (Scorpion)
  python3 mk4_arcade_run.py --from-save1             # load save1 -> save2 (Kai)
  python3 mk4_arcade_run.py --from-save1 --character=SubZero
  python3 mk4_arcade_run.py --all                    # generate save2 for ALL 15 characters

Character grid (row, col) — cursor starts at Kai (0,0):
  Row 0: Kai(0,0)  Raiden(0,1)  Shinnok(0,2)  LiuKang(0,3)  Reptile(0,4)
  Row 1: Scorpion(1,0)  Jax(1,1)  Reiko(1,2)  JohnnyCage(1,3)  Jarek(1,4)
  Row 2: Tanya(2,0)  Fujin(2,1)  SubZero(2,2)  QuanChi(2,3)  Sonya(2,4)

Post-save1 navigation: FRAME-ADVANCE only (deterministic, nospeedlimit-immune).
  - Joystick x=-80/+80 for Left/Right, y=-80/+80 for Down/Up
  - 3-frame tap + 45-frame settle per input
"""
import sys, os, time, glob, shutil, struct, mmap as mmap_mod, subprocess, argparse
sys.path.insert(0, '/Users/ichiropractic/code/n64/training/src')
from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
from pathlib import Path

# ── Character grid ─────────────────────────────────────────────────────────────
# (row, col) from Kai's starting position (0,0)
CHAR_GRID = {
    'kai':        (0, 0),
    'raiden':     (0, 1),
    'shinnok':    (0, 2),
    'liukang':    (0, 3),
    'reptile':    (0, 4),
    'scorpion':   (1, 0),
    'jax':        (1, 1),
    'reiko':      (1, 2),
    'johnnycage': (1, 3),
    'jarek':      (1, 4),
    'tanya':      (2, 0),
    'fujin':      (2, 1),
    'subzero':    (2, 2),
    'quanchi':    (2, 3),
    'sonya':      (2, 4),
}
ALL_CHARS = list(CHAR_GRID.keys())

def char_key(name: str) -> str:
    """Normalize character name to CHAR_GRID key."""
    k = name.lower().replace('-', '').replace(' ', '').replace('_', '')
    if k not in CHAR_GRID:
        raise ValueError(f'Unknown character: {name!r}. Valid: {ALL_CHARS}')
    return k

# ── N64 button constants ───────────────────────────────────────────────────────
BTN_START  = (1 << 4)
BTN_A      = (1 << 7)
BTN_B      = (1 << 6)
BTN_RIGHT  = (1 << 0)
BTN_LEFT   = (1 << 1)
BTN_DOWN   = (1 << 2)
BTN_UP     = (1 << 3)

# ── Config ─────────────────────────────────────────────────────────────────────
SOCK         = '/Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock'
ROM          = '/Users/ichiropractic/code/n64/Mortal Kombat 4 (USA).z64'
SHOT_DIR     = '/Users/ichiropractic/code/n64/.m64p/data/screenshots'
OUT_DIR      = '/Users/ichiropractic/code/n64/training/data/mk4_run_out'
STATE_DIR    = '/Users/ichiropractic/code/n64/training/data/savestates/mk4_arcade'
INST         = 'reverse-visible'
CFG_DIR      = f'/Users/ichiropractic/code/n64/.m64p/instances/{INST}/config'
DATA_DIR     = '/opt/homebrew/share/mupen64plus'
PLUG_DIR     = '/opt/homebrew/lib/mupen64plus'
CUSTOM_INPUT = '/Users/ichiropractic/code/n64/vendor/n64train-input/n64train-input.dylib'
M64P_BIN     = '/Users/ichiropractic/code/n64/vendor/mupen64plus-ui-console/projects/unix/mupen64plus'
CORELIB      = '/Users/ichiropractic/code/n64/vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib'
CTRL_FILE    = '/tmp/mk4_ctrl'
SAVE1_PATH   = f'{STATE_DIR}/mk4_arcade_charselect_base.st'

def save2_path(char_key: str) -> str:
    return f'{STATE_DIR}/mk4_arcade_{char_key}_round_start.st'

BRIDGE_CMD = [
    'python3',
    '/Users/ichiropractic/code/n64/training/scripts/run_bridge_server.py',
    '--socket-path', SOCK, '--instance-id', INST,
    '--memory-reader', 'debugger-dump', '--rom-path', ROM,
    '--debugger-ui-binary', M64P_BIN, '--debugger-corelib', CORELIB,
    '--debugger-plugindir', PLUG_DIR, '--debugger-configdir', CFG_DIR,
    '--debugger-datadir', DATA_DIR,
    '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
    '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
    '--debugger-input-plugin', CUSTOM_INPUT,
    '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
    '--debugger-emumode', '0',
]

# ── Virtual N64 controller ─────────────────────────────────────────────────────
class N64Controller:
    def __init__(self):
        with open(CTRL_FILE, 'w+b') as f:
            f.write(b'\x00' * 4)
        self._f   = open(CTRL_FILE, 'r+b')
        self._mem = mmap_mod.mmap(self._f.fileno(), 4)
        self._set(0)

    def _set(self, buttons, x=0, y=0):
        self._mem.seek(0)
        self._mem.write(struct.pack('<Hbb', buttons & 0xFFFF, x, y))
        self._mem.flush()

    def press(self, buttons):  self._set(buttons)
    def release(self):          self._set(0)

    def close(self):
        self.release()
        self._mem.close()
        self._f.close()


# ── Emulator / bridge helpers ──────────────────────────────────────────────────
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
            print(f'[up] Bridge pid={proc.pid}', flush=True)
            return proc
        time.sleep(0.5)
    raise RuntimeError('Bridge socket never appeared')

def connect():
    b = SocketEmulatorBridge(SOCK, timeout_sec=20)
    h = Mk4BridgeHelper(b)
    return b, h

def wait_ready(timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            b, h = connect()
            h.pause(); h.run(); b.close()
            print('[ready] Emulator ready.', flush=True)
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError('Emulator never became ready')

_n = [0]
def screenshot(b, h, label):
    """Take screenshot while game is RUNNING (no pause)."""
    before = set(glob.glob(f'{SHOT_DIR}/*.png'))
    h.run()
    time.sleep(1.5)
    b.debugger_command('screenshot', timeout_sec=5, output_tail_chars=20)
    time.sleep(0.5)
    after = set(glob.glob(f'{SHOT_DIR}/*.png'))
    new = sorted(after - before, key=os.path.getmtime)
    if new:
        dst = f'{OUT_DIR}/{_n[0]:02d}_{label}.png'
        shutil.copy2(new[-1], dst); _n[0] += 1
        print(f'  [ss] {dst}', flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# EMULATOR CONTROL HELPERS
#
# h.pause() is a TOGGLE in mupen64plus debugger:
#   - emulator running  → h.pause() PAUSES  it  ✅  (correct)
#   - emulator paused   → h.pause() RESUMES it  ❌  (wrong!)
#
# Pattern: h.run() ONCE → inputs in live mode → h.pause() ONCE at the end.
# Never call h.pause() between individual button presses.
# ─────────────────────────────────────────────────────────────────────────────

def live_tap(ctrl, buttons, hold=0.08, settle=0.18):
    """Press+release a d-pad/button tap while emulator is RUNNING.
    Do NOT call h.run()/h.pause() here — caller manages emulator state."""
    ctrl.press(buttons)
    time.sleep(hold)
    ctrl.release()
    time.sleep(settle)

def advance_frames(b, h, frames, timeout_sec=120):
    """Step exactly N frames. Emulator must be RUNNING before this call.
    This calls h.pause() to stop it, then uses the debugger 'frame' command."""
    h.pause()           # running → paused  (single correct toggle)
    time.sleep(0.05)    # let pause take effect
    b.debugger_command(f'frame {frames}', timeout_sec=timeout_sec, output_tail_chars=20)

def advance_chunked(b, h, total_frames, chunk=600, timeout_sec=120):
    """Advance total_frames in chunks. Emulator must be RUNNING before first call."""
    h.pause()
    time.sleep(0.05)
    remaining = total_frames
    while remaining > 0:
        n = min(remaining, chunk)
        b.debugger_command(f'frame {n}', timeout_sec=timeout_sec, output_tail_chars=20)
        remaining -= n
        print(f'    ...{total_frames - remaining}/{total_frames} frames', flush=True)

def save_state(b, h, path):
    """Save state. Works whether emulator is running or paused.
    Uses h.run()+h.pause() to guarantee we enter from running→paused state."""
    h.run()           # ensure running (no-op if already running)
    time.sleep(0.02)
    h.pause()         # running → paused (correct toggle direction)
    time.sleep(0.05)
    resp = b.save_savestate_path(Path(path))
    ok = resp.get('saved', False)
    print(f'  [save] {"OK" if ok else "FAIL"} -> {path}', flush=True)

def btn(ctrl, b, h, buttons, frames=60):
    """Button press for BOOT MODE where emulator is already RUNNING.
    h.pause() here is correct: running → paused, then frame N steps."""
    h.pause()           # running → paused (correct direction in boot mode)
    time.sleep(0.05)
    ctrl.press(buttons)
    b.debugger_command(f'frame {frames}', timeout_sec=30, output_tail_chars=20)
    ctrl.release()
    b.debugger_command('frame 10', timeout_sec=15, output_tail_chars=20)
    h.run()             # return to running after each boot-menu press



# ── Character navigation ──────────────────────────────────────────────────────
def nav_to_char(ctrl, b, h, char: str):
    """
    Navigate from Kai (cursor start) to target character.

    CORRECT PATTERN:
      h.run() ONCE → all taps via live_tap() → emulator stays RUNNING
      NO h.pause() between presses (it is a toggle: would RESUME emulator!)
      Caller (nav_to_save2) calls advance_frames() which pauses correctly.
    """
    row, col = CHAR_GRID[char]
    print(f'  navigating to {char} (row={row}, col={col})', flush=True)
    if row == 0 and col == 0:
        return  # Kai: no nav needed, emulator stays in debugger-paused state

    h.run()  # start live mode — stay running until advance_frames() pauses

    for i in range(row):
        print(f'    Down {i+1}/{row}', flush=True)
        live_tap(ctrl, BTN_DOWN)

    for i in range(col):
        print(f'    Right {i+1}/{col}', flush=True)
        live_tap(ctrl, BTN_RIGHT)
    # emulator is still RUNNING when we return


# ── Save2 navigation (charselect base → fight round start) ────────────────────
def nav_to_save2(ctrl, b, h, char: str):
    """
    State machine:
      stateload      → debugger-paused (start)
      nav_to_char    → h.run() inside, all taps live, still running
      A press        → live_tap, still running
      advance_frames → h.pause() ONCE [running→paused], then frame 300
      h.run()        → resume for Right x4
      Right x4       → live_tap x4, still running
      A press        → live_tap, still running
      advance_frames → h.pause() ONCE [running→paused], then frame 900
      save           → already paused, save directly
    """
    # Step 1: nav to character (leaves emulator RUNNING)
    nav_to_char(ctrl, b, h, char)

    # Step 2: confirm character (still running)
    print(f'\n[4] A -> select {char}', flush=True)
    live_tap(ctrl, BTN_A, hold=0.08, settle=0.15)

    # Step 3: wait for ladder screen (advance_frames pauses then steps)
    print('  advancing 300 frames -> ladder screen', flush=True)
    advance_frames(b, h, 300)          # h.pause() inside → frame 300

    # Step 4: max difficulty Right x4 (resume, tap, stay running)
    print('[5] Right x4 -> max difficulty', flush=True)
    h.run()                            # advance_frames left us paused
    for _ in range(4):
        live_tap(ctrl, BTN_RIGHT)

    # Step 5: confirm difficulty (still running)
    print('[6] A -> confirm difficulty', flush=True)
    live_tap(ctrl, BTN_A, hold=0.08, settle=0.15)

    # Step 6: advance to round start (advance_frames pauses then steps)
    print('  advancing 900 frames -> round start', flush=True)
    advance_frames(b, h, 900, timeout_sec=120)   # h.pause() inside → frame 900

    # Step 7: save (emulator already paused after advance_frames)
    print(f'\n[save2] Saving {char} round start state...', flush=True)
    resp = b.save_savestate_path(Path(save2_path(char)))
    ok = resp.get('saved', False)
    print(f'  [save] {"OK" if ok else "FAIL"} -> {save2_path(char)}', flush=True)


# ── Full boot mode ─────────────────────────────────────────────────────────────
def run_full_boot(ctrl, b, h):
    print('\n[boot] Waiting 30s for splash screens...', flush=True)
    h.run(); b.close()
    time.sleep(30)
    b, h = connect()

    print('[boot] Start -> dismiss controller pak warning', flush=True)
    btn(ctrl, b, h, BTN_START, frames=60)

    print('[boot] Start -> dismiss rumble pak warning', flush=True)
    btn(ctrl, b, h, BTN_START, frames=60)

    h.run(); b.close(); time.sleep(2); b, h = connect()
    screenshot(b, h, '00_top_menu')

    print('\n[1] A -> select Arcade', flush=True)
    btn(ctrl, b, h, BTN_A, frames=60)
    screenshot(b, h, '01_after_A_arcade')

    print('[2] Start -> dismiss rumble pak warning', flush=True)
    btn(ctrl, b, h, BTN_START, frames=60)
    screenshot(b, h, '02_after_start_rumble')

    print('[3] A -> select 1 on 1 (P1)', flush=True)
    btn(ctrl, b, h, BTN_A, frames=60)
    screenshot(b, h, '03_after_A_p1')

    print('\n[save1] Saving charselect base state...', flush=True)
    save_state(b, h, SAVE1_PATH)
    screenshot(b, h, '04_savestate1')

    return b, h


# ── From-save1 mode ────────────────────────────────────────────────────────────
def run_from_save1(ctrl, b, h):
    if not os.path.exists(SAVE1_PATH):
        raise RuntimeError(f'save1 not found: {SAVE1_PATH}\nRun without --from-save1 first.')
    print(f'\n[load] Loading charselect state from {SAVE1_PATH}', flush=True)
    b.load_savestate_path(Path(SAVE1_PATH))
    time.sleep(0.5)
    # NO screenshot here — h.run() inside screenshot() breaks frame-advance nav
    return b, h



# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='MK4 Arcade savestate builder')
    parser.add_argument('--from-save1', action='store_true',
                        help='Skip full boot; load existing charselect savestate')
    parser.add_argument('--character', default='kai',
                        help=f'Character to select. One of: {ALL_CHARS}')
    parser.add_argument('--all', action='store_true',
                        help='Generate save2 for all 15 characters (uses --from-save1 for each)')
    args = parser.parse_args()

    kill()
    start_bridge()
    wait_ready()
    ctrl = N64Controller()
    b, h = connect()
    h.pause()

    if args.all:
        # Full boot once for save1, then loop all chars from save1
        print('Mode: --all (boot once, generate save2 for all 15 characters)', flush=True)
        b, h = run_full_boot(ctrl, b, h)
        for char in ALL_CHARS:
            print(f'\n══ Character: {char} ══', flush=True)
            b.load_savestate_path(Path(SAVE1_PATH))
            time.sleep(0.5)
            nav_to_save2(ctrl, b, h, char)
    else:
        char = char_key(args.character)
        if args.from_save1:
            print(f'Mode: --from-save1, character={char}', flush=True)
            b, h = run_from_save1(ctrl, b, h)
        else:
            print(f'Mode: full boot, character={char}', flush=True)
            b, h = run_full_boot(ctrl, b, h)
        nav_to_save2(ctrl, b, h, char)

    ctrl.close()
    h.run()
    b.close()
    print(f'\n Done!\n  Screenshots -> {OUT_DIR}\n  Savestates  -> {STATE_DIR}', flush=True)


if __name__ == '__main__':
    main()
