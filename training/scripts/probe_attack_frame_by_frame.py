#!/usr/bin/env python3
"""
probe_attack_frame_by_frame.py — Single-punch frame-by-frame register dump.

Walk P1 into contact range, then record:
  • PRE  (15 frames before button press)  — baseline
  • HOLD (12 frames with LOW PUNCH held)
  • POST (30 frames after button release)

Every frame prints ALL watched addresses. Any value that differs from the
very first PRE frame is highlighted so you can spot the exact frame each
register changes and in what direction.

Usage:
    python3 training/scripts/probe_attack_frame_by_frame.py --boot
"""
from __future__ import annotations
import argparse, json, mmap, os, re, signal, socket, struct, subprocess, sys, time
from pathlib import Path

N64_ROOT  = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))

BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
SOCK_PATH  = BRIDGE_DIR / 'mk4-frameby.sock'
LOG_DIR    = N64_ROOT / 'training/data/logs'
ROM_PATH   = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN   = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB    = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN     = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR   = '/opt/homebrew/lib/mupen64plus'
CTRL_PATH  = '/tmp/mk4_ctrl_frameby'
SAVE_PATH  = N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st'

# ── Addresses to watch every frame ────────────────────────────────────────────
# Health confirmed:
#   0x80126F54 = VISUAL P2's health (goes down when P1 punches — user confirmed visually)
#   0x800FE0D8 = Scorpion (P1) health
WATCH_ADDRS = {
    # Health
    'P1_HP':          0x800FE0D8,
    'P2_HP':          0x80126F54,
    # Animation IDs
    'P1_ANIM_CUR':    0x800FE600,   # current animation playing
    'P1_ANIM_PREV':   0x800FE604,   # previous animation
    'P1_ANIM_PREV2':  0x800FE608,   # two animations ago
    # Combat state
    'P1_ACTION_ST':   0x800FE08C,   # 0=idle, 2=combat
    # Hitbox block — scan all words 0x800FE304 through 0x800FE320
    'P1_HB_304':      0x800FE304,
    'P1_HB_308':      0x800FE308,
    'P1_HB_30C':      0x800FE30C,
    'P1_HB_310':      0x800FE310,
    'P1_HB_314':      0x800FE314,
    'P1_HB_318':      0x800FE318,
    'P1_HB_31C':      0x800FE31C,
    'P1_HB_320':      0x800FE320,
    # Attack type
    'P1_ATK_TYPE':    0x800FE090,
    # P2 side
    'P2_HITSTUN':     0x80126F9C,   # constant 2 so far — may be wrong
    'P2_ACTION_ST':   0x80126EC0,
    'P2_ANIM_CUR':    0x80127410,   # P2 equivalent animation offset (to verify)
}

GREEN  = '\033[92m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

BTN_A       = 1 << 7   # LOW PUNCH
BTN_D_RIGHT = 1 << 0   # walk right

PRE_FRAMES  = 15
HOLD_FRAMES = 12
POST_FRAMES = 30


def send_cmd(cmd, payload=None, timeout=15.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCK_PATH))
    req = {"id": "fbf", "command": cmd, "payload": payload or {}}
    s.sendall((json.dumps(req) + "\n").encode())
    resp = json.loads(s.makefile('r').readline())
    s.close()
    if not resp.get('ok'):
        raise RuntimeError(f"Bridge: {resp.get('error',{}).get('message','?')}")
    return resp.get('payload', {})


def dbg(cmd, timeout=15.0):
    return send_cmd("DEBUGGER_COMMAND",
                    {"command": cmd, "timeout_sec": timeout},
                    timeout=timeout + 5).get('output', '')


def read_u32(addr: int) -> int:
    out = dbg(f"mem /1w 0x{addr:08x}")
    for line in out.strip().split('\n'):
        line = line.strip()
        if line.startswith('(dbg)') or line.startswith('PC at') \
                or line.startswith('mem ') or not line:
            continue
        tokens = re.findall(r'[0-9A-Fa-f]{2,16}', line)
        if tokens:
            return int(tokens[-1], 16) & 0xFFFFFFFF
    raise ValueError(f"No u32 from 0x{addr:08x}: {out!r}")


def step1():
    out = dbg("frame 1", timeout=10)
    if 'M64P_FRAME_OK frames=1' not in out:
        print(f"  {YELLOW}WARN: {out[-60:]}{RESET}")


def stepN(n: int):
    dbg(f"frame {n}", timeout=max(15, n))


def write_ctrl(buttons: int = 0):
    if not os.path.exists(CTRL_PATH):
        with open(CTRL_PATH, 'w+b') as f: f.write(b'\x00' * 4)
    with open(CTRL_PATH, 'r+b') as f:
        m = mmap.mmap(f.fileno(), 4)
        m.seek(0)
        m.write(struct.pack('<Hbb', buttons & 0xFFFF, 0, 0))
        m.flush(); m.close()


def snap() -> dict[str, int]:
    return {name: read_u32(addr) for name, addr in WATCH_ADDRS.items()}


def print_frame(label: str, s: dict[str, int], ref: dict[str, int], colour: str):
    changed, same = [], []
    for name, val in s.items():
        signed = val if val < 0x80000000 else val - 0x100000000
        entry = f"  {name:<16} = 0x{val:08X}  ({signed:>+10d})"
        if val != ref.get(name):
            changed.append(entry)
        else:
            same.append(entry)

    print(f"\n{colour}{BOLD}{label}{RESET}")
    if changed:
        print(f"  {BOLD}── CHANGED from pre-frame-1 ──{RESET}")
        for e in changed:
            print(f"{GREEN}{e}{RESET}")
    print(f"  {BOLD}── unchanged ──{RESET}")
    for e in same:
        print(e)


def boot_emulator():
    if SOCK_PATH.exists(): SOCK_PATH.unlink()
    cfg_dir = N64_ROOT / '.m64p/instances/frameby/config'
    import shutil
    if cfg_dir.exists(): shutil.rmtree(str(cfg_dir))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / 'probe_fbf.log'
    cmd = [
        sys.executable, str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path', str(SOCK_PATH), '--instance-id', 'frameby',
        '--memory-reader', 'debugger-dump', '--rom-path', ROM_PATH,
        '--debugger-ui-binary', M64P_BIN, '--debugger-corelib', CORELIB,
        '--debugger-plugindir', PLUG_DIR, '--debugger-configdir', str(cfg_dir),
        '--debugger-datadir', '/opt/homebrew/share/mupen64plus',
        '--debugger-dump-dir',
        str(N64_ROOT / 'training/data/bridge/debugger_dumps/frameby'),
        '--debugger-gfx-plugin', 'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'dummy',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin', 'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode', '0', '--speed-mode', 'DEBUG_VISIBLE',
        '--log-path', str(log_path),
    ]
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = CTRL_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = open(log_path, 'w')
    proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             env=env, start_new_session=True)
    lf.close()
    return proc


def wait_for_bridge(timeout=90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if SOCK_PATH.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2); s.connect(str(SOCK_PATH)); s.close()
                return True
            except Exception: pass
        time.sleep(1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--boot', action='store_true')
    args = ap.parse_args()

    proc = None
    if args.boot:
        if not SAVE_PATH.exists():
            print(f"Savestate not found: {SAVE_PATH}"); sys.exit(1)
        print(f"{BOLD}Booting emulator...{RESET}")
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        proc = boot_emulator()
        if not wait_for_bridge():
            print("Bridge never ready")
            if proc: proc.kill()
            sys.exit(1)
        print(f"{GREEN}Bridge ready{RESET}\n")

    try:
        dbg("pause"); time.sleep(0.3)
        if args.boot:
            print(f"Loading {SAVE_PATH.name}")
            send_cmd("LOAD_SAVESTATE", {"savestate_path": str(SAVE_PATH)}, timeout=45)
            time.sleep(0.5)

        print("Settling 120 frames..."); stepN(120); time.sleep(0.3)

        print("Walking P1 right 240 frames...")
        write_ctrl(BTN_D_RIGHT); stepN(240)
        write_ctrl(0);           stepN(20)

        print(f"\n{BOLD}{'═'*65}{RESET}")
        print(f"{BOLD}  SINGLE LOW PUNCH — PRE / HOLD / POST capture{RESET}")
        print(f"  {YELLOW}  PRE{RESET}  = {PRE_FRAMES} frames idle before punch")
        print(f"  {CYAN} HOLD{RESET}  = {HOLD_FRAMES} frames with LOW PUNCH (A) held")
        print(f"  {GREEN} POST{RESET}  = {POST_FRAMES} frames after release")
        print(f"  {GREEN}GREEN row = changed from first PRE-frame value{RESET}")
        print(f"{BOLD}{'═'*65}{RESET}")

        ref: dict[str, int] = {}

        # PRE phase
        write_ctrl(0)
        for f in range(PRE_FRAMES):
            step1(); s = snap()
            if f == 0: ref = dict(s)          # lock reference at frame 1
            print_frame(f"[PRE  {f+1:>2}/{PRE_FRAMES}]", s, ref, YELLOW)

        # HOLD phase
        write_ctrl(BTN_A)
        for f in range(HOLD_FRAMES):
            step1(); s = snap()
            print_frame(f"[HOLD {f+1:>2}/{HOLD_FRAMES}]", s, ref, CYAN)

        # POST phase
        write_ctrl(0)
        for f in range(POST_FRAMES):
            step1(); s = snap()
            print_frame(f"[POST {f+1:>2}/{POST_FRAMES}]", s, ref, GREEN)

        print(f"\n{BOLD}{'═'*65}{RESET}")
        print(f"{BOLD}  Reading guide:{RESET}")
        print(f"  Pulse register  → GREEN in HOLD, back to unchanged in POST")
        print(f"  Latching state  → GREEN from some HOLD frame, stays GREEN thru POST")
        print(f"  Noise register  → Randomly GREEN throughout all phases")

    finally:
        write_ctrl(0)
        if proc:
            print("\nShutting down...")
            try: send_cmd("TERMINATE", timeout=3)
            except Exception: pass
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception: pass
            try: proc.wait(timeout=5)
            except Exception: proc.kill()
            if SOCK_PATH.exists(): SOCK_PATH.unlink()
            print("Done.")


if __name__ == '__main__':
    main()
