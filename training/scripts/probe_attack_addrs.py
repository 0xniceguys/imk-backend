#!/usr/bin/env python3
"""
probe_attack_addrs.py — Per-frame attack state address verification.

NOTE: Use p1p2state.st (VS mode, both players human-controlled) NOT
arcade_training_scorpion.st (CPU drives P2 — can't neutralize P2).
With p1p2state.st, P2 ctrl file is never written so P2 holds completely
neutral, giving us clean P1-only attack isolation.

Tests the following candidate addresses by reading them EVERY FRAME
during a scripted punch sequence, printing a full timeline:

  P1_HITSTUN     0x800FE310   should be non-zero ONLY during P1's active attack frames
  P1_ACTION_FULL 0x800FE308   companion hitbox register (PERFECT ARC candidate)
  P1_ATTACK_TYPE 0x800FE090   unique value per attack type (LP/HP/LK/HK)
  P1_ACTION_ST   0x800FE08C   action state (0=idle, 4=active)

  P2_HITSTUN     0x80126F9C   should be non-zero ONLY during P2's active attack frames
  P2_ACTION_ST   0x80126EC0   P2 anim pointer (PERFECT ARC on jump+punch)
  P2_ATTACK_TYPE 0x80126E94   unique value per P2 attack type

Cross-player coupling test:
  We run P1-only attacks first (P2 held neutral via emulator control) and
  check that P2 registers stay at their idle values — confirming isolation.
  Then we trigger P2 attacks (P1 held neutral) and check P1 registers stay idle.

Usage:
    python3 training/scripts/probe_attack_addrs.py

Requirements:
    - A running bridge server at mk4-probe.sock  (started via run_bridge_server.py)
    - OR this script can boot its own emulator (see --boot flag)

Run as:
    python3 training/scripts/probe_attack_addrs.py --boot
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

N64_ROOT  = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))
sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
SOCK_PATH  = BRIDGE_DIR / 'mk4-probe.sock'
LOG_DIR    = N64_ROOT / 'training/data/logs'
ROM_PATH   = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN   = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB    = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN     = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR   = '/opt/homebrew/lib/mupen64plus'

CTRL_PATH  = '/tmp/mk4_ctrl_probe'
SAVE_PATH  = N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st'

# ── Candidate addresses ────────────────────────────────────────────────────────
# P1 struct base: ~0x800FE000
P1_HITSTUN_A    = 0x800FE310  # PERFECT ARC 0→0x84→0  (smallest, clearest)
P1_HITSTUN_B    = 0x800FE30C  # PERFECT ARC 0→0xFE2→0 (companion)
P1_HITSTUN_C    = 0x800FE308  # PERFECT ARC 0→0xB20→0 (companion)
P1_ACTION_ST    = 0x800FE08C  # idle=0, jump/attack=4
P1_ATTACK_TYPE  = 0x800FE090  # LP=69422, HP=67956, HK=68606 (from early scans)

# P2 struct base: ~0x80126E00
P2_HITSTUN      = 0x80126F9C  # 0=idle, 2=attack (punch-only reported)
P2_ACTION_ST    = 0x80126EC0  # anim pointer (PERFECT ARC on jump+punch)
P2_ATTACK_TYPE  = 0x80126E94  # unique per P2 attack type

# Confirmed references (should be stable during attack sequences)
P1_HEALTH       = 0x800FE0D8
P2_HEALTH       = 0x80126F54
P1_GROUND       = 0x800FE0F8  # 4=ground, 1=air

# ── Button masks (plugin.c layout) ────────────────────────────────────────────
BTN_A       = 1 << 7   # LOW PUNCH
BTN_B       = 1 << 6   # HIGH PUNCH
BTN_C_RIGHT = 1 << 8   # LOW KICK
BTN_C_UP    = 1 << 11  # HIGH KICK
BTN_D_RIGHT = 1 << 0   # move right

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'


# ── Socket helpers ─────────────────────────────────────────────────────────────

def send_cmd(cmd, payload=None, timeout=15.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCK_PATH))
    req = {"id": "probe", "command": cmd, "payload": payload or {}}
    s.sendall((json.dumps(req) + "\n").encode())
    resp = json.loads(s.makefile('r').readline())
    s.close()
    if not resp.get('ok'):
        msg = resp.get('error', {}).get('message', 'unknown')
        raise RuntimeError(f"Bridge error on {cmd}: {msg}")
    return resp.get('payload', {})


def dbg(cmd, timeout=15.0):
    resp = send_cmd("DEBUGGER_COMMAND", {"command": cmd, "timeout_sec": timeout}, timeout=timeout + 5)
    return resp.get('output', '')


def read_u32(addr: int) -> int:
    import re
    out = dbg(f"mem /1w 0x{addr:08x}")
    for line in out.strip().split('\n'):
        line = line.strip()
        # Skip debugger prompt, PC dumps, empty lines, AND the command echo
        # ("mem /1w 0x800FE310" contains the address itself — parsing it would
        # return the address instead of the value, giving wrong results)
        if (line.startswith('(dbg)') or line.startswith('PC at')
                or line.startswith('mem ') or not line):
            continue
        # Match 2-16 hex chars (validate_addresses.py uses same range)
        tokens = re.findall(r'[0-9A-Fa-f]{2,16}', line)
        if tokens:
            return int(tokens[-1], 16) & 0xFFFFFFFF
    raise ValueError(f"Could not parse u32 from addr 0x{addr:08x}: {out!r}")


def step(n: int):
    out = dbg(f"frame {n}", timeout=max(15, n * 2))
    if f'M64P_FRAME_OK frames={n}' not in out:
        print(f"  {YELLOW}WARN frame step: {out[-120:]}{RESET}")


# ── Controller helpers ─────────────────────────────────────────────────────────

def write_ctrl(buttons: int = 0):
    if not os.path.exists(CTRL_PATH):
        with open(CTRL_PATH, 'w+b') as f:
            f.write(b'\x00' * 4)
    with open(CTRL_PATH, 'r+b') as f:
        m = mmap.mmap(f.fileno(), 4)
        m.seek(0)
        m.write(struct.pack('<Hbb', buttons & 0xFFFF, 0, 0))
        m.flush()
        m.close()


# ── Per-frame snapshot ─────────────────────────────────────────────────────────

WATCH_ADDRS = {
    'P1_HITSTUN_A':   P1_HITSTUN_A,
    'P1_HITSTUN_B':   P1_HITSTUN_B,
    'P1_HITSTUN_C':   P1_HITSTUN_C,
    'P1_ACTION_ST':   P1_ACTION_ST,
    'P1_ATTACK_TYPE': P1_ATTACK_TYPE,
    'P2_HITSTUN':     P2_HITSTUN,
    'P2_ACTION_ST':   P2_ACTION_ST,
    'P2_ATTACK_TYPE': P2_ATTACK_TYPE,
    'P1_HEALTH':      P1_HEALTH,
    'P2_HEALTH':      P2_HEALTH,
}


def snap() -> dict[str, int]:
    return {name: read_u32(addr) for name, addr in WATCH_ADDRS.items()}


def print_snap(label: str, s: dict[str, int], baseline: dict[str, int] | None = None):
    parts = [f"  {CYAN}{label:30s}{RESET}"]
    for name, val in s.items():
        changed = baseline is not None and val != baseline.get(name)
        colour  = GREEN if changed else ''
        parts.append(f"  {colour}{name}=0x{val:08X}{RESET}")
    print(' '.join(parts))


# ── Sequence runner ────────────────────────────────────────────────────────────

def run_attack_sequence(label: str, button: int, hold_frames: int = 8,
                         release_frames: int = 20, repeats: int = 3,
                         baseline: dict[str, int] | None = None):
    """
    Press button for hold_frames, release for release_frames, N repeats.
    Print per-rep snapshot and flag registers that change vs baseline.
    """
    print(f"\n  {BOLD}── {label} ──{RESET}")
    for i in range(repeats):
        # Press
        write_ctrl(button)
        step(hold_frames)
        mid = snap()
        # Release
        write_ctrl(0)
        step(release_frames)
        end = snap()

        print(f"    rep {i+1}  DURING ({hold_frames}f hold):")
        print_snap('', mid, baseline)
        print(f"    rep {i+1}  AFTER  ({release_frames}f release):")
        print_snap('', end, baseline)

    return mid, end


# ── Main ──────────────────────────────────────────────────────────────────────

def boot_emulator(log_path: Path) -> subprocess.Popen:
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    cfg_dir = N64_ROOT / '.m64p/instances/probe/config'
    import shutil
    if cfg_dir.exists():
        shutil.rmtree(str(cfg_dir))
    cfg_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path',           str(SOCK_PATH),
        '--instance-id',           'probe',
        '--memory-reader',         'debugger-dump',
        '--rom-path',              ROM_PATH,
        '--debugger-ui-binary',    M64P_BIN,
        '--debugger-corelib',      CORELIB,
        '--debugger-plugindir',    PLUG_DIR,
        '--debugger-configdir',    str(cfg_dir),
        '--debugger-datadir',      '/opt/homebrew/share/mupen64plus',
        '--debugger-dump-dir',     str(N64_ROOT / 'training/data/bridge/debugger_dumps/probe'),
        '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'dummy',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode',      '0',
        '--speed-mode',            'DEBUG_VISIBLE',
        '--log-path',              str(log_path),
    ]
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = CTRL_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lf = open(log_path, 'w')
    proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True)
    lf.close()
    return proc


def wait_for_bridge(timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if SOCK_PATH.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(str(SOCK_PATH))
                s.close()
                return True
            except Exception:
                pass
        time.sleep(1)
    return False


def main():
    ap = argparse.ArgumentParser(description='Per-frame attack address verifier')
    ap.add_argument('--boot', action='store_true',
                    help='Boot the emulator (otherwise connects to existing bridge at mk4-probe.sock)')
    ap.add_argument('--hold-frames', type=int, default=6,
                    help='Frames to hold each attack input (default: 6)')
    ap.add_argument('--release-frames', type=int, default=25,
                    help='Frames to release between repeats (default: 25)')
    ap.add_argument('--repeats', type=int, default=3,
                    help='Repeats per action (default: 3)')
    ap.add_argument('--between-actions', type=int, default=30,
                    help='Extra settle frames between actions (default: 30)')
    ap.add_argument('--walk-frames', type=int, default=150,
                    help='Frames to walk P1 into range before tests (default: 150)')
    ap.add_argument(
        '--actions',
        default='lp,hp,lk,hk',
        help='Comma-separated subset/order of actions: lp,hp,lk,hk (default: all)',
    )
    args = ap.parse_args()

    proc = None
    if args.boot:
        if not SAVE_PATH.exists():
            print(f"{RED}Savestate not found: {SAVE_PATH}{RESET}")
            sys.exit(1)
        print(f"{BOLD}Booting emulator...{RESET}")
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        proc = boot_emulator(LOG_DIR / 'probe_attack_addrs.log')
        print("Waiting for bridge...")
        if not wait_for_bridge():
            print(f"{RED}Bridge never became ready{RESET}")
            if proc:
                proc.kill()
            sys.exit(1)
        print(f"{GREEN}Bridge ready{RESET}\n")

    try:
        # Pause + load savestate
        dbg("pause")
        time.sleep(0.3)
        if args.boot:
            print(f"Loading savestate: {SAVE_PATH.name}")
            send_cmd("LOAD_SAVESTATE", {"savestate_path": str(SAVE_PATH)}, timeout=45)
            time.sleep(0.5)

        # Let VS animation settle (2s of game time)
        print("Settling (120 frames)...")
        step(120)
        time.sleep(0.3)

        # Walk P1 close to P2 so punches land
        print(f"Walking P1 toward P2 ({max(1, args.walk_frames)} frames D_RIGHT)...")
        write_ctrl(BTN_D_RIGHT)
        step(max(1, args.walk_frames))
        write_ctrl(0)
        step(20)

        # ── BASELINE ─────────────────────────────────────────────────────────
        print(f"\n{BOLD}{'═'*70}{RESET}")
        print(f"{BOLD}  BASELINE (P1 idle, after walk into range){RESET}")
        print(f"{BOLD}{'═'*70}{RESET}")
        baseline = snap()
        for name, val in baseline.items():
            print(f"  {name:20s} = 0x{val:08X} ({val})")

        # ── P1 ATTACK SEQUENCES ───────────────────────────────────────────────
        print(f"\n{BOLD}{'═'*70}{RESET}")
        print(f"{BOLD}  P1 ATTACK SEQUENCES — P2 regs must stay at baseline{RESET}")
        print(f"{BOLD}{'═'*70}{RESET}")

        action_map = {
            'lp': (BTN_A, 'P1 LOW PUNCH (A)'),
            'hp': (BTN_B, 'P1 HIGH PUNCH (B)'),
            'lk': (BTN_C_RIGHT, 'P1 LOW KICK (C_RIGHT)'),
            'hk': (BTN_C_UP, 'P1 HIGH KICK (C_UP)'),
        }
        requested = [tok.strip().lower() for tok in args.actions.split(',') if tok.strip()]
        selected = [tok for tok in requested if tok in action_map]
        if not selected:
            selected = ['lp', 'hp', 'lk', 'hk']

        print(
            f"Attack params: hold={max(1, args.hold_frames)} "
            f"release={max(1, args.release_frames)} repeats={max(1, args.repeats)} "
            f"between={max(0, args.between_actions)} actions={selected}"
        )

        for key in selected:
            btn, label = action_map[key]
            run_attack_sequence(
                label,
                btn,
                hold_frames=max(1, args.hold_frames),
                release_frames=max(1, args.release_frames),
                repeats=max(1, args.repeats),
                baseline=baseline,
            )
            if args.between_actions > 0:
                step(args.between_actions)  # extra settle between attack types

        print(f"\n{BOLD}{'═'*70}{RESET}")
        print(f"{BOLD}  CROSS-PLAYER COUPLING CHECK{RESET}")
        print(f"{BOLD}  (if P2_HITSTUN / P2_ACTION_ST changed above → coupled){RESET}")
        print(f"{BOLD}{'═'*70}{RESET}")

        after_p1_atk = snap()
        p2_hitstun_changed = after_p1_atk['P2_HITSTUN'] != baseline['P2_HITSTUN']
        p2_act_changed     = after_p1_atk['P2_ACTION_ST'] != baseline['P2_ACTION_ST']

        def verdict(ok: bool, msg: str):
            tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL (cross-coupled){RESET}"
            print(f"  {tag}  {msg}")

        print()
        verdict(not p2_hitstun_changed,
                f"P2_HITSTUN stayed 0x{baseline['P2_HITSTUN']:08X} during P1 attacks"
                + (f" → jumped to 0x{after_p1_atk['P2_HITSTUN']:08X}" if p2_hitstun_changed else ""))
        verdict(not p2_act_changed,
                f"P2_ACTION_ST unchanged during P1 attacks"
                + (f" → changed from 0x{baseline['P2_ACTION_ST']:08X} to 0x{after_p1_atk['P2_ACTION_ST']:08X}"
                   if p2_act_changed else ""))

        p1h_start = baseline['P1_HEALTH']
        p1h_now   = after_p1_atk['P1_HEALTH']
        p2h_start = baseline['P2_HEALTH']
        p2h_now   = after_p1_atk['P2_HEALTH']

        print()
        verdict(p2h_now < p2h_start,
                f"P2 took damage: 0x{p2h_start:X} → 0x{p2h_now:X} (confirms P1 punches landed)")
        print(f"  {'INFO':4s}  P1 health: 0x{p1h_start:X} → 0x{p1h_now:X} (CPU attacked back?)")

        print()
        print(f"{BOLD}  P1 attack registers during attacks:{RESET}")
        for name in ('P1_HITSTUN_A', 'P1_HITSTUN_B', 'P1_HITSTUN_C',
                     'P1_ACTION_ST', 'P1_ATTACK_TYPE'):
            b = baseline[name]
            a = after_p1_atk[name]
            changed = b != a
            clr = GREEN if changed else YELLOW
            print(f"  {clr}{name:20s}{RESET}  idle=0x{b:08X}  →  after=0x{a:08X}"
                  + (f"  {GREEN}VARIED{RESET}" if changed else f"  {YELLOW}static{RESET}"))

        print(f"\n  {BOLD}Recommendation:{RESET}")
        print("  P1_HITSTUN_A (0x800FE310): non-zero ONLY during attack frames = usable as binary flag")
        print("  P1_ATTACK_TYPE (0x800FE090): unique per attack = usable for attack-type obs")
        print("  P2_HITSTUN (0x80126F9C): should vary when CPU punches (check if P1_HEALTH dropped)")
        print()
        print("  If P2_HITSTUN stayed 0 even though P2 clearly attacked (P1 HP dropped),")
        print("  the P2 addresses may still need a dedicated P2-triggers-P1 probe.")

    finally:
        write_ctrl(0)
        if proc:
            print("\nShutting down emulator...")
            try:
                send_cmd("TERMINATE", timeout=3)
            except Exception:
                pass
            try:
                import signal
                import os as _os
                pgid = _os.getpgid(proc.pid)
                _os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            if SOCK_PATH.exists():
                SOCK_PATH.unlink()
            print("Done.")


if __name__ == '__main__':
    main()
