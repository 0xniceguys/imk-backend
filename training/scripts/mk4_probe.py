#!/usr/bin/env python3
"""Live MK4 screen probe — run while navigating manually in the emulator.
Prints state/cursor/signature every 2 seconds.
Hit Ctrl+C when done."""
import sys, time
sys.path.insert(0, '/Users/ichiropractic/code/n64/training/src')
from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper

SOCK = '/Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock'
b = SocketEmulatorBridge(SOCK, timeout_sec=10)
h = Mk4BridgeHelper(b)

print("Probing every 2s — navigate the emulator while watching these values.")
print("Format: state | cursor | sig(a,b,c)\n")
prev = None
while True:
    try:
        h.pause()
        s  = h.get_menu_screen_state()['value']
        c  = h.read_u8(0x8011D810)
        sa = h.read_u8(0x800546D0)
        sb = h.read_u8(0x8005472E)
        sc = h.read_u8(0x8005472F)
        h.run()
        row = f"state={s:3d}  cursor={c:3d}  sig=({sa},{sb},{sc})"
        if row != prev:
            print(row, flush=True)
            prev = row
        time.sleep(2)
    except KeyboardInterrupt:
        print("\nDone.")
        b.close()
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(2)
