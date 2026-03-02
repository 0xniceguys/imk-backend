#!/usr/bin/env python3
"""
launch_and_load.py — Launch emulator with visible window and load a savestate.

No arguments needed. Just run:
    python3 training/scripts/launch_and_load.py

Press Ctrl+C to quit.
"""
import subprocess, sys, time, os
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))

SOCK         = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
STATE        = str(N64_ROOT / 'training/data/savestates/mk4_arcade/test.st')
INST         = 'reverse-visible'
ROM          = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
CFG_DIR      = str(N64_ROOT / f'.m64p/instances/{INST}/config')
M64P_BIN     = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB      = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
CUSTOM_INPUT = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')

BRIDGE_CMD = [
    'python3', str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
    '--socket-path', SOCK,
    '--instance-id', INST,
    '--memory-reader', 'debugger-dump',
    '--rom-path', ROM,
    '--debugger-ui-binary', M64P_BIN,
    '--debugger-corelib', CORELIB,
    '--debugger-plugindir', '/opt/homebrew/lib/mupen64plus',
    '--debugger-configdir', CFG_DIR,
    '--debugger-datadir', '/opt/homebrew/share/mupen64plus',
    '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
    '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
    '--debugger-input-plugin', CUSTOM_INPUT,
    '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
    '--debugger-emumode', '0',
]

def main():
    # Kill any old instance
    os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
    time.sleep(1)
    try: os.remove(SOCK)
    except: pass

    print('[*] Starting emulator (window will open shortly)…')
    # Inherit stdout/stderr so the SDL window process is "attached" to this terminal
    proc = subprocess.Popen(BRIDGE_CMD)

    # Wait for socket
    deadline = time.time() + 45
    while time.time() < deadline:
        if os.path.exists(SOCK):
            break
        time.sleep(0.5)
    else:
        print('[!] Timed out waiting for emulator')
        proc.terminate(); sys.exit(1)

    print('[*] Emulator ready. Loading savestate…')
    time.sleep(2)

    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper

    b = SocketEmulatorBridge(SOCK, timeout_sec=10)
    h = Mk4BridgeHelper(b)
    b.load_savestate_path(Path(STATE))
    time.sleep(0.3)
    h.run()
    b.close()

    print('[*] State loaded — Sonya round start is live.')
    print('[*] Emulator running. Press Ctrl+C to quit.')

    try:
        proc.wait()
    except KeyboardInterrupt:
        print('\n[*] Shutting down…')
        proc.terminate()

if __name__ == '__main__':
    main()
