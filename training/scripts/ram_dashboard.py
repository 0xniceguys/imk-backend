#!/usr/bin/env python3
"""
ram_dashboard.py — Live RAM signal dashboard for visual verification.

Launches the emulator with visible window, loads p1p2state.st,
then continuously prints all 14 observation signals as a live dashboard.

YOU play the game (keyboard / controller) and watch the screen.
The dashboard shows what the AI "sees" — verify it matches what's on screen.

Usage:
    python3 training/scripts/ram_dashboard.py

Press Ctrl+C to stop.
"""
import subprocess, sys, time, os, mmap, struct as S, signal
from pathlib import Path

N64 = Path('/Users/ichiropractic/code/n64')
sys.path.insert(0, str(N64 / 'training/src'))

from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper

SOCK  = str(N64 / 'training/data/bridge/mk4-visible.sock')
STATE = N64 / 'training/data/savestates/mk4_arcade/p1p2state.st'
CTRL  = '/tmp/mk4_ctrl'

CMD = [
    'python3', str(N64 / 'training/scripts/run_bridge_server.py'),
    '--socket-path', SOCK, '--instance-id', 'reverse-visible',
    '--memory-reader', 'debugger-dump',
    '--rom-path', str(N64 / 'Mortal Kombat 4 (USA).z64'),
    '--debugger-ui-binary', str(N64 / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus'),
    '--debugger-corelib', str(N64 / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib'),
    '--debugger-plugindir', '/opt/homebrew/lib/mupen64plus',
    '--debugger-configdir', str(N64 / '.m64p/instances/reverse-visible/config'),
    '--debugger-datadir', '/opt/homebrew/share/mupen64plus',
    '--debugger-gfx-plugin', 'mupen64plus-video-rice.dylib',
    '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
    '--debugger-input-plugin', str(N64 / 'vendor/n64train-input/n64train-input.dylib'),
    '--debugger-rsp-plugin', 'mupen64plus-rsp-hle.dylib',
    '--debugger-emumode', '0',
]

CHARS = ['Scorpion','Raiden','Sonya','Liu Kang','Sub-Zero','Fujin',
         'Shinnok','Reiko','Quan Chi','Tanya','Reptile','Kai',
         'Jarek','Jax','Johnny Cage','Goro','Kitana','Noob Saibot']

# ── Helper ────────────────────────────────────────────────────────────────────

def u32s(h, addr):
    """Read u32, return (raw, signed_int32)."""
    v = h.read_u32(addr)
    return v, (v if v < 0x80000000 else v - 0x100000000)

def s16hi(h, addr):
    v = h.read_u32(addr)
    hi = (v >> 16) & 0xFFFF
    return hi if hi < 0x8000 else hi - 0x10000

def read_all(h):
    """Read every signal and return a dict."""
    d = {}
    d['p1_hp']    = h.read_u8(0x8036E729)
    d['p2_hp']    = h.read_u8(0x8036E72E)
    d['timer']    = h.read_u8(0x80105118)
    d['p1_x']     = s16hi(h, 0x800F87F8)
    d['p2_x']     = s16hi(h, 0x8006A060)
    d['dist']     = abs(d['p2_x'] - d['p1_x'])
    d['facing']   = 1.0 if d['p2_x'] >= d['p1_x'] else -1.0

    # P1 scan-verified signals
    d['p1_act'],  _ = u32s(h, 0x800FE08C)
    d['p1_gnd'],  _ = u32s(h, 0x800FE0F8)
    _, d['p1_yv']   = u32s(h, 0x800FE90C)
    d['p1_hit'],  _ = u32s(h, 0x800FE310)

    # P2 — try both the inferred offset AND a wider scan
    d['p2_act'],  _ = u32s(h, 0x80126F18)
    d['p2_gnd'],  _ = u32s(h, 0x80126F84)
    _, d['p2_yv']   = u32s(h, 0x80127798)
    d['p2_hit'],  _ = u32s(h, 0x8012719C)

    # Char IDs via u32 word (LSB = char)
    w1 = h.read_u32(0x800FE290)
    w2 = h.read_u32(0x80126E8C)
    d['p1_char'] = w1 & 0xFF
    d['p2_char'] = w2 & 0xFF

    return d

def fmt_bar(val, maxval, width=10):
    """Simple ASCII bar."""
    frac = max(0, min(1, val / maxval)) if maxval != 0 else 0
    filled = int(frac * width)
    return '█' * filled + '░' * (width - filled)

def print_dashboard(d, frame):
    """Print a single dashboard frame (overwrites previous)."""
    c1 = CHARS[d['p1_char']] if d['p1_char'] < len(CHARS) else f"?{d['p1_char']}"
    c2 = CHARS[d['p2_char']] if d['p2_char'] < len(CHARS) else f"?{d['p2_char']}"

    p1_state = 'IDLE' if d['p1_act'] == 0 else f'ACTIVE({d["p1_act"]})'
    p1_air   = 'AIR' if d['p1_gnd'] == 1 else ('GROUND' if d['p1_gnd'] == 4 else f'?{d["p1_gnd"]}')
    p1_hst   = f'HIT({d["p1_hit"]})' if d['p1_hit'] > 0 else 'none'

    lines = [
        f'  ╔══════════════════ FRAME {frame:06d} ══════════════════╗',
        f'  ║  P1: {c1:<12s}  vs  P2: {c2:<12s}       ║',
        f'  ╠══════════════════════════════════════════════════╣',
        f'  ║  HP   P1: {fmt_bar(d["p1_hp"],160)} {d["p1_hp"]:3d}/160            ║',
        f'  ║       P2: {fmt_bar(d["p2_hp"],160)} {d["p2_hp"]:3d}/160            ║',
        f'  ║  Timer: {d["timer"]:2d}   Dist: {d["dist"]:3d}   Face: {"→" if d["facing"]>0 else "←"}        ║',
        f'  ║  Pos   P1_X: {d["p1_x"]:+4d}   P2_X: {d["p2_x"]:+4d}                ║',
        f'  ╠═══ P1 SIGNALS ════════════════════════════════════╣',
        f'  ║  Action:   {p1_state:<20s}                  ║',
        f'  ║  Airborne: {p1_air:<20s}                  ║',
        f'  ║  Y-Vel:    {d["p1_yv"]:>10d}                            ║',
        f'  ║  Hitstun:  {p1_hst:<20s}                  ║',
        f'  ╠═══ P2 SIGNALS ════════════════════════════════════╣',
        f'  ║  Action:   {d["p2_act"]:<10d} Gnd: {d["p2_gnd"]:<10d}     ║',
        f'  ║  Y-Vel:    {d["p2_yv"]:>10d}  Hit: {d["p2_hit"]:<10d}     ║',
        f'  ╚══════════════════════════════════════════════════╝',
    ]

    # Move cursor up to overwrite (ANSI escape)
    if frame > 0:
        sys.stdout.write(f'\033[{len(lines)}A')
    sys.stdout.write('\n'.join(lines) + '\n')
    sys.stdout.flush()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Kill old instances
    os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
    time.sleep(1)
    try: os.remove(SOCK)
    except: pass

    print('[*] Starting emulator (window will appear)...')
    proc = subprocess.Popen(CMD)

    for _ in range(30):
        if os.path.exists(SOCK): break
        time.sleep(1)
    else:
        print('[!] Timeout waiting for socket'); proc.kill(); return

    time.sleep(2)
    print('[*] Connecting...')
    b = SocketEmulatorBridge(SOCK, timeout_sec=30)
    b.connect()
    h = Mk4BridgeHelper(b)

    # Load fight state
    h.run(); time.sleep(1.5); h.pause(); time.sleep(0.5)
    try:
        b.load_savestate_path(STATE)
    except:
        h.run(); time.sleep(1); h.pause(); time.sleep(0.5)
        b.load_savestate_path(STATE)

    time.sleep(0.5)
    h.run()
    time.sleep(1)

    print('[*] Fight loaded! Dashboard starting...')
    print('[*] Use the emulator window to play. Watch the dashboard below.')
    print('[*] Press Ctrl+C to stop.\n')

    frame = 0
    running = True

    def handle_sigint(sig, f):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, handle_sigint)

    while running:
        try:
            # Brief pause to read, then resume
            h.pause()
            time.sleep(0.05)
            d = read_all(h)
            h.run()
            print_dashboard(d, frame)
            frame += 1
            time.sleep(0.15)  # ~5 reads/sec (game keeps running between reads)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f'\n[!] Read error: {e}')
            time.sleep(1)

    print('\n[*] Shutting down...')
    try:
        h.run()
        b.close()
    except: pass
    proc.terminate()
    print('[*] Done.')


if __name__ == '__main__':
    main()
