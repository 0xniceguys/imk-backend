#!/usr/bin/env python3
"""
mk4_probe.py — Modular MK4 RAM address verification tool.

Each run tests EXACTLY ONE action so there is no cross-contamination.
Every test function loads the savestate fresh before doing anything.
The probe auto-detects controller routing (`p1`/`p2`) each run and aborts
when the requested attacker channel cannot be isolated.

Usage:
    python3 training/scripts/mk4_probe.py --boot --test health_verify  --action lp
    python3 training/scripts/mk4_probe.py --boot --test punch_animation --action hp
    python3 training/scripts/mk4_probe.py --boot --test addr_scan       --action lk  --start 0x800FE5C0 --end 0x800FE640
    python3 training/scripts/mk4_probe.py --boot --test p2_attack       --action p2_lp

Available tests:
    health_verify   — Confirm P1/P2 health by executing the chosen P1 action
    punch_animation — Frame-by-frame animation register dump for one action
    addr_scan       — Before/after scan of any address range during one action
    p2_attack       — P2 executes the chosen action, check P2 struct + P1 damage

Available actions (--action):
    P1: lp (low punch)  hp (high punch)  lk (low kick)  hk (high kick)
    P2: p2_lp           p2_hp            p2_lk          p2_hk

Savestate: p1p2state.st (VS mode — full P1 + P2 controller access, no CPU AI)
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

N64_ROOT   = Path(__file__).resolve().parents[2]
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
RUNS_ROOT  = N64_ROOT / 'training/data/reverse/probe_runs'
ROM_PATH   = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN   = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB    = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN     = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR   = '/opt/homebrew/lib/mupen64plus'
DEFAULT_SAVE_PATH = N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st'
SAVE_PATH  = DEFAULT_SAVE_PATH
P1_CTRL    = '/tmp/mk4_ctrl_probe_v2_p1'
P2_CTRL    = '/tmp/mk4_ctrl_probe_v2_p2'
# Per-run mutable paths (set by configure_run_paths).
SOCK_PATH  = Path('/tmp/mk4_probe_v2.sock')
LOG_PATH   = RUNS_ROOT / 'latest' / 'probe.log'
DUMP_DIR   = N64_ROOT / 'training/data/bridge/debugger_dumps/probe_v2'
CFG_DIR    = N64_ROOT / '.m64p/instances/probe_v2/config'
RUN_DIR    = RUNS_ROOT / 'latest'

# ── Confirmed addresses ────────────────────────────────────────────────────────
CONFIRMED = {
    'P1_HP':        0x800FE0D8,
    'P2_HP':        0x80126F54,
    'P1_ACTION_ST': 0x800FE08C,
    'P1_GROUND':    0x800FE0F8,
    'P1_Y_VEL':     0x800FE90C,
    'P2_GROUND':    0x80126F78,
    'P2_ACTION_ST': 0x80126EC0,
    'P2_HITSTUN':   0x80126F9C,   # known-wrong (constant 2)
}

G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'
C = '\033[96m'; B = '\033[1m';  RST = '\033[0m'

# ── Button masks ───────────────────────────────────────────────────────────────
BTN_A        = 1 << 7   # LOW PUNCH
BTN_B        = 1 << 6   # HIGH PUNCH
BTN_C_RIGHT  = 1 << 8   # LOW KICK
BTN_C_UP     = 1 << 11  # HIGH KICK
BTN_D_RIGHT  = 1 << 0
BTN_D_LEFT   = 1 << 1

# Map --action name → (p1_button, p2_button, label)
ACTIONS: dict[str, tuple[int, int, str]] = {
    'lp':    (BTN_A,       0,           'P1 LOW PUNCH  (A)'),
    'hp':    (BTN_B,       0,           'P1 HIGH PUNCH (B)'),
    'lk':    (BTN_C_RIGHT, 0,           'P1 LOW KICK   (C_RIGHT)'),
    'hk':    (BTN_C_UP,   0,           'P1 HIGH KICK  (C_UP)'),
    'p2_lp': (0,           BTN_A,       'P2 LOW PUNCH  (A)'),
    'p2_hp': (0,           BTN_B,       'P2 HIGH PUNCH (B)'),
    'p2_lk': (0,           BTN_C_RIGHT, 'P2 LOW KICK   (C_RIGHT)'),
    'p2_hk': (0,           BTN_C_UP,   'P2 HIGH KICK  (C_UP)'),
}


def _slug(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        if ch.isalnum() or ch in ('-', '_'):
            chars.append(ch)
        else:
            chars.append('_')
    out = ''.join(chars).strip('_')
    return out or 'probe'


def configure_run_paths(test: str, action: str, run_tag: str | None = None) -> str:
    """Set isolated per-run artifact paths to avoid cross-contaminated captures."""
    global SOCK_PATH, LOG_PATH, DUMP_DIR, CFG_DIR, RUN_DIR
    base_tag = f'{time.strftime("%Y%m%d_%H%M%S")}_{test}_{action}'
    tag = _slug(run_tag) if run_tag else _slug(base_tag)
    RUN_DIR = RUNS_ROOT / tag
    LOG_PATH = RUN_DIR / 'probe.log'
    DUMP_DIR = RUN_DIR / 'debugger_dumps'
    CFG_DIR = N64_ROOT / f'.m64p/instances/probe_v2_{tag}/config'
    # Keep socket path short to stay under UNIX path-length limits.
    SOCK_PATH = Path(f'/tmp/mk4_probe_{tag[:36]}.sock')

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        'tag': tag,
        'test': test,
        'action': action,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'socket_path': str(SOCK_PATH),
        'log_path': str(LOG_PATH),
        'dump_dir': str(DUMP_DIR),
        'cfg_dir': str(CFG_DIR),
    }
    (RUN_DIR / 'run_meta.json').write_text(json.dumps(meta, indent=2) + '\n')
    return tag


# ── Bridge ─────────────────────────────────────────────────────────────────────

def send_cmd(cmd, payload=None, timeout=15.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCK_PATH))
    req = {"id": "prb", "command": cmd, "payload": payload or {}}
    s.sendall((json.dumps(req) + "\n").encode())
    resp = json.loads(s.makefile('r').readline())
    s.close()
    if not resp.get('ok'):
        raise RuntimeError(f"Bridge[{cmd}]: {resp.get('error',{}).get('message','?')}")
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
    raise ValueError(f"read_u32 0x{addr:08x}: {out!r}")


def read_s16hi(addr: int) -> int:
    w = read_u32(addr)
    hi = (w >> 16) & 0xFFFF
    return hi if hi < 0x8000 else hi - 0x10000


def step(n: int = 1):
    dbg(f"frame {n}", timeout=max(15, n + 5))


def ctrl(p1: int = 0, p2: int = 0):
    for path, btns in [(P1_CTRL, p1), (P2_CTRL, p2)]:
        if not os.path.exists(path):
            with open(path, 'w+b') as f: f.write(b'\x00' * 4)
        with open(path, 'r+b') as f:
            m = mmap.mmap(f.fileno(), 4)
            m.seek(0)
            m.write(struct.pack('<Hbb', btns & 0xFFFF, 0, 0))
            m.flush(); m.close()


def snap(addrs: dict[str, int]) -> dict[str, int]:
    return {name: read_u32(addr) for name, addr in addrs.items()}


def load_state():
    send_cmd("LOAD_SAVESTATE", {"savestate_path": str(SAVE_PATH)}, timeout=45)
    time.sleep(0.5)


def settle():
    step(120)


def _set_channel(channel: str, buttons: int) -> None:
    if channel == 'p1':
        ctrl(p1=buttons)
    elif channel == 'p2':
        ctrl(p2=buttons)
    else:
        raise ValueError(f'unknown channel: {channel}')


def _detect_control_routes() -> dict[str, str]:
    """Detect which fighter each controller channel actually drives."""
    routes: dict[str, str] = {}
    for channel in ('p1', 'p2'):
        load_state()
        settle()
        x1_a = read_s16hi(0x800F87F8)
        x2_a = read_s16hi(0x8006A060)
        _set_channel(channel, BTN_D_RIGHT)
        step(40)
        ctrl()
        step(10)
        x1_b = read_s16hi(0x800F87F8)
        x2_b = read_s16hi(0x8006A060)
        d1 = abs(x1_b - x1_a)
        d2 = abs(x2_b - x2_a)
        if d1 == 0 and d2 == 0:
            routes[channel] = 'none'
        elif d1 >= d2:
            routes[channel] = 'p1'
        else:
            routes[channel] = 'p2'
    return routes


def _resolve_attack_channel(is_p2: bool) -> tuple[str | None, dict[str, str]]:
    desired = 'p2' if is_p2 else 'p1'
    routes = _detect_control_routes()
    for ch in ('p1', 'p2'):
        if routes.get(ch) == desired:
            return ch, routes
    return None, routes


def move_attacker_into_range(*, channel: str, is_p2: bool, frames: int = 240):
    """Move only the attacking side into contact range. Defender stays neutral."""
    if is_p2:
        _set_channel(channel, BTN_D_LEFT)
    else:
        _set_channel(channel, BTN_D_RIGHT)
    step(frames)
    ctrl()
    step(45)


def _pass(msg: str, ok: bool):
    tag = f"{G}PASS{RST}" if ok else f"{R}FAIL{RST}"
    print(f"  {tag}  {msg}")


# ── Test: health_verify ────────────────────────────────────────────────────────

def test_health_verify(action: str):
    p1_btn, p2_btn, label = ACTIONS[action]
    is_p2 = p2_btn != 0
    attack_btn = p2_btn if is_p2 else p1_btn
    attacker = 'P2' if is_p2 else 'P1'
    victim   = 'P1' if is_p2 else 'P2'
    print(f"\n{B}═══ health_verify  action={action} ═══{RST}")
    print(f"  {label} × 5 — {victim}_HP should drop, {attacker}_HP unchanged\n")

    channel, routes = _resolve_attack_channel(is_p2)
    print(f"  Control routes: p1->{routes.get('p1')}  p2->{routes.get('p2')}")
    if channel is None:
        print(f"  {R}Could not isolate {attacker} control channel; aborting this run.{RST}")
        return
    print(f"  Using channel: {channel}")

    load_state()
    settle()
    move_attacker_into_range(channel=channel, is_p2=is_p2)

    p1s = read_u32(CONFIRMED['P1_HP'])
    p2s = read_u32(CONFIRMED['P2_HP'])
    print(f"  Start   P1_HP=0x{p1s:08X}  P2_HP=0x{p2s:08X}")

    for _ in range(5):
        _set_channel(channel, attack_btn); step(8)
        ctrl();                       step(25)

    p1e = read_u32(CONFIRMED['P1_HP'])
    p2e = read_u32(CONFIRMED['P2_HP'])
    print(f"  After   P1_HP=0x{p1e:08X}  P2_HP=0x{p2e:08X}")

    victim_start = p1s if is_p2 else p2s
    victim_end   = p1e if is_p2 else p2e
    attacker_start = p2s if is_p2 else p1s
    attacker_end   = p2e if is_p2 else p1e

    _pass(f"{victim}_HP decreased", victim_end < victim_start)
    _pass(f"{attacker}_HP unchanged (opponent neutral)", attacker_end == attacker_start)
    if victim_end < victim_start:
        print(f"  {victim} lost {victim_start - victim_end} HP "
              f"({(victim_start - victim_end)/victim_start*100:.1f}%)")


# ── Test: punch_animation ──────────────────────────────────────────────────────

def test_punch_animation(action: str):
    p1_btn, p2_btn, label = ACTIONS[action]
    is_p2 = p2_btn != 0
    attack_btn = p2_btn if is_p2 else p1_btn

    print(f"\n{B}═══ punch_animation  action={action} ═══{RST}")
    print(f"  {label} — scanning 0x800FE5C0-0x800FE640 frame-by-frame\n")

    channel, routes = _resolve_attack_channel(is_p2)
    print(f"  Control routes: p1->{routes.get('p1')}  p2->{routes.get('p2')}")
    if channel is None:
        print(f"  {R}Could not isolate attacker control channel; aborting this run.{RST}")
        return
    print(f"  Using channel: {channel}")

    anim = {f"P1_ANIM_{a:08X}": a for a in range(0x800FE5C0, 0x800FE640, 4)}
    watch = {**anim, **{k: v for k, v in CONFIRMED.items()}}

    # Fresh load every time — no state from previous tests
    load_state()
    settle()
    move_attacker_into_range(channel=channel, is_p2=is_p2)

    ref = snap(watch)
    non_zero = {n: v for n, v in ref.items() if v}
    print(f"  Baseline non-zero ({len(non_zero)}):")
    for n, v in non_zero.items():
        print(f"    {n} = 0x{v:08X}")

    print(f"\n  HOLD {label} 12 frames:")
    _set_channel(channel, attack_btn)
    for f in range(12):
        step(1)
        s = snap(watch)
        chg = {n: v for n, v in s.items() if v != ref[n]}
        if chg:
            parts = '  '.join(f"{n}=0x{v:08X}" for n, v in chg.items())
            print(f"    {C}HOLD {f+1:>2}{RST}: {parts}")

    ctrl()
    print(f"\n  POST release 20 frames:")
    for f in range(20):
        step(1)
        s = snap(watch)
        chg = {n: v for n, v in s.items() if v != ref[n]}
        if chg:
            parts = '  '.join(f"{n}=0x{v:08X}" for n, v in chg.items())
            print(f"    {Y}POST {f+1:>2}{RST}: {parts}")


# ── Test: addr_scan ────────────────────────────────────────────────────────────

def test_addr_scan(action: str, start: int, end: int):
    p1_btn, p2_btn, label = ACTIONS[action]
    is_p2 = p2_btn != 0
    attack_btn = p2_btn if is_p2 else p1_btn
    count = (end - start) // 4

    print(f"\n{B}═══ addr_scan  action={action}  0x{start:08X}–0x{end:08X} ({count} words) ═══{RST}")
    print(f"  {label}")

    channel, routes = _resolve_attack_channel(is_p2)
    print(f"  Control routes: p1->{routes.get('p1')}  p2->{routes.get('p2')}")
    if channel is None:
        print(f"  {R}Could not isolate attacker control channel; aborting this run.{RST}")
        return
    print(f"  Using channel: {channel}")

    addrs = {f"0x{a:08X}": a for a in range(start, end, 4)}
    addrs.update({'P1_HP': CONFIRMED['P1_HP'], 'P2_HP': CONFIRMED['P2_HP']})

    # Fresh load — one action only, no prior state
    load_state()
    settle()
    move_attacker_into_range(channel=channel, is_p2=is_p2)

    ref = snap(addrs)
    _set_channel(channel, attack_btn); step(12)
    ctrl();                       step(20)
    after = snap(addrs)

    changed = [(n, ref[n], after[n]) for n in addrs if after[n] != ref[n]]
    if not changed:
        print(f"  {Y}No addresses changed.{RST}")
    else:
        print(f"  {G}{len(changed)} changed:{RST}")
        for name, bef, aft in sorted(changed):
            sb = bef if bef < 0x80000000 else bef - 0x100000000
            sa = aft if aft < 0x80000000 else aft - 0x100000000
            print(f"    {name}: 0x{bef:08X}({sb:+d}) → 0x{aft:08X}({sa:+d})")


# ── Test: p2_attack ────────────────────────────────────────────────────────────

def test_p2_attack(action: str):
    if not action.startswith('p2_'):
        print(f"  {Y}p2_attack requires a p2_* action (e.g. --action p2_lp){RST}")
        return
    _p1_btn, p2_btn, label = ACTIONS[action]
    channel, routes = _resolve_attack_channel(is_p2=True)
    print(f"  Control routes: p1->{routes.get('p1')}  p2->{routes.get('p2')}")
    if channel is None:
        print(f"  {R}Could not isolate P2 control channel; aborting this run.{RST}")
        return
    print(f"  Using channel: {channel}")

    print(f"\n{B}═══ p2_attack  action={action} ═══{RST}")
    print(f"  {label} × 5, P1 neutral")
    print(f"  Scanning P2 struct block 0x80126E00-0x80126F40\n")

    p2_block = {f"P2_{a:08X}": a for a in range(0x80126E00, 0x80126F40, 4)}
    p2_block.update({'P1_HP': CONFIRMED['P1_HP'], 'P2_HP': CONFIRMED['P2_HP']})

    # Fresh load — only this P2 action, nothing before it.
    load_state()
    settle()
    move_attacker_into_range(channel=channel, is_p2=True)

    ref = snap(p2_block)
    print(f"  Start  P1_HP=0x{ref['P1_HP']:08X}  P2_HP=0x{ref['P2_HP']:08X}")

    ever_changed: dict[str, tuple] = {}
    for _ in range(5):
        _set_channel(channel, p2_btn); step(8)
        ctrl();           step(25)
        s = snap(p2_block)
        for n, v in s.items():
            if v != ref[n] and n not in ever_changed:
                ever_changed[n] = (ref[n], v)

    final = snap(p2_block)
    _pass("P1_HP decreased (P2 hit P1)", final['P1_HP'] < ref['P1_HP'])
    _pass("P2_HP unchanged (P1 neutral)", final['P2_HP'] == ref['P2_HP'])

    print(f"\n  Addresses that changed (P2 attacking):")
    interesting = {n: v for n, v in ever_changed.items() if n not in ('P1_HP', 'P2_HP')}
    if interesting:
        for name, (bef, aft) in sorted(interesting.items()):
            sb = bef if bef < 0x80000000 else bef - 0x100000000
            sa = aft if aft < 0x80000000 else aft - 0x100000000
            print(f"    {G}{name}: 0x{bef:08X}({sb:+d}) → 0x{aft:08X}({sa:+d}){RST}")
    else:
        print(f"    {Y}(none besides health){RST}")


# ── Boot / teardown ────────────────────────────────────────────────────────────

def boot_emulator():
    if SOCK_PATH.exists(): SOCK_PATH.unlink()
    import shutil
    if CFG_DIR.exists():
        shutil.rmtree(str(CFG_DIR))
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path', str(SOCK_PATH), '--instance-id', 'probe_v2',
        '--memory-reader', 'debugger-dump', '--rom-path', ROM_PATH,
        '--debugger-ui-binary', M64P_BIN, '--debugger-corelib', CORELIB,
        '--debugger-plugindir', PLUG_DIR, '--debugger-configdir', str(CFG_DIR),
        '--debugger-datadir', '/opt/homebrew/share/mupen64plus',
        '--debugger-dump-dir', str(DUMP_DIR),
        '--debugger-gfx-plugin', 'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'dummy',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin', 'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode', '0', '--speed-mode', 'DEBUG_VISIBLE',
        '--log-path', str(LOG_PATH),
    ]
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = P1_CTRL
    env['N64TRAIN_CTRL_P2'] = P2_CTRL
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lf = open(LOG_PATH, 'w')
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


def teardown(proc):
    ctrl()
    if proc:
        try: send_cmd("TERMINATE", timeout=3)
        except Exception: pass
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: pass
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
    if SOCK_PATH.exists(): SOCK_PATH.unlink()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global SAVE_PATH
    ap = argparse.ArgumentParser(description='MK4 modular RAM probe — one action per run')
    ap.add_argument('--boot',   action='store_true')
    ap.add_argument('--test',   required=True,
                    choices=['health_verify', 'punch_animation', 'addr_scan', 'p2_attack'])
    ap.add_argument('--action', required=True, choices=list(ACTIONS.keys()),
                    help='Which action to perform (lp/hp/lk/hk or p2_lp/p2_hp/p2_lk/p2_hk)')
    ap.add_argument('--run-tag', default=None,
                    help='Optional artifact tag (default: timestamp_test_action)')
    ap.add_argument('--savestate', default=str(DEFAULT_SAVE_PATH),
                    help='Savestate path to load (default: p1p2state.st)')
    ap.add_argument('--start',  type=lambda x: int(x, 16), default=0x800FE5C0,
                    help='Start address for addr_scan (hex)')
    ap.add_argument('--end',    type=lambda x: int(x, 16), default=0x800FE640,
                    help='End address for addr_scan (hex)')
    args = ap.parse_args()
    SAVE_PATH = Path(args.savestate)

    run_tag = configure_run_paths(args.test, args.action, args.run_tag)
    print(f"{B}Run tag:{RST} {run_tag}")
    print(f"{B}Run dir:{RST} {RUN_DIR}")
    print(f"{B}Socket :{RST} {SOCK_PATH}")

    proc = None
    if args.boot:
        if not SAVE_PATH.exists():
            print(f"Savestate not found: {SAVE_PATH}"); sys.exit(1)
        print(f"{B}Booting emulator (probe_v2) — action={args.action} test={args.test}...{RST}")
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        proc = boot_emulator()
        if not wait_for_bridge():
            print(f"{R}Bridge never ready{RST}")
            if proc: proc.kill()
            sys.exit(1)
        print(f"{G}Bridge ready{RST}")

    try:
        dbg("pause"); time.sleep(0.3)

        if   args.test == 'health_verify':    test_health_verify(args.action)
        elif args.test == 'punch_animation':   test_punch_animation(args.action)
        elif args.test == 'addr_scan':         test_addr_scan(args.action, args.start, args.end)
        elif args.test == 'p2_attack':         test_p2_attack(args.action)

        print(f"\n{G}Done.{RST}")

    finally:
        teardown(proc)
        print("Emulator shut down.")


if __name__ == '__main__':
    main()
