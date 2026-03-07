#!/usr/bin/env python3
"""
mk4_hitstun_scan.py — Find the real hitstun/recovery RAM address.

Strategy:
  1. Boot mupen64plus with --debug --savestate (VS mode, both players controllable)
  2. Take idle baseline of defender's full struct
  3. Move attacker into range
  4. Execute attack while doing frame-by-frame scanning on the DEFENDER
  5. Find addresses that:
     - Were 0 in baseline (idle)
     - Become non-zero AFTER health drops (victim is reeling = hitstun)
     - Return to 0 after recovery ends (PERFECT ARC)
  6. Cross-verify: P1→P2 and P2→P1

Usage:
    python3 training/scripts/mk4_hitstun_scan.py --boot
    python3 training/scripts/mk4_hitstun_scan.py --boot --scan-side p2
    python3 training/scripts/mk4_hitstun_scan.py --boot --scan-side both
    python3 training/scripts/mk4_hitstun_scan.py --boot --scan-side both --attacks 5
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import re
import signal
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

N64_ROOT   = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))

ROM_PATH   = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
SAVE_PATH  = N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st'
LOG_DIR    = N64_ROOT / 'training/data/logs'
DUMP_DIR   = N64_ROOT / 'training/data/bridge/debugger_dumps/hitstun_scan'
CFG_DIR    = N64_ROOT / '.m64p/instances/hitstun_scan/config'

# Controller paths
P1_CTRL    = '/tmp/mk4_ctrl_hitstun_p1'
P2_CTRL    = '/tmp/mk4_ctrl_hitstun_p2'

# ── Platform detection ────────────────────────────────────────────────────────
_IS_LINUX = sys.platform.startswith('linux')
if _IS_LINUX:
    M64P_BIN   = '/usr/games/mupen64plus'
    CORELIB    = '/usr/lib/x86_64-linux-gnu/libmupen64plus.so.2'
    PLUG_DIR   = '/usr/lib/x86_64-linux-gnu/mupen64plus'
    DATA_DIR   = '/usr/share/mupen64plus'
    PLUGIN     = str(N64_ROOT / 'vendor/n64train-input/n64train-input.so')
    GFX_PLUGIN = 'mupen64plus-video-rice.so'
    RSP_PLUGIN = 'mupen64plus-rsp-hle.so'
else:
    M64P_BIN   = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
    CORELIB    = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
    PLUG_DIR   = '/opt/homebrew/lib/mupen64plus'
    DATA_DIR   = '/opt/homebrew/share/mupen64plus'
    PLUGIN     = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
    GFX_PLUGIN = 'mupen64plus-video-rice.dylib'
    RSP_PLUGIN = 'mupen64plus-rsp-hle.dylib'

# ── Confirmed addresses ───────────────────────────────────────────────────────
P1_HP_ADDR   = 0x800FE0D8
P2_HP_ADDR   = 0x80126F54
P1_X_ADDR    = 0x800F87F8
P2_X_ADDR    = 0x8006A060

P1_BASE = 0x800FE000
P2_BASE = 0x80126E00

# Scan ranges
P1_SCAN_START = 0x800FE000
P1_SCAN_END   = 0x800FEA00
P2_SCAN_START = 0x80126E00
P2_SCAN_END   = 0x80127800

# ── Button masks ──────────────────────────────────────────────────────────────
BTN_A        = 1 << 7
BTN_B        = 1 << 6
BTN_C_RIGHT  = 1 << 8
BTN_C_UP     = 1 << 11
BTN_D_RIGHT  = 1 << 0
BTN_D_LEFT   = 1 << 1

# ── Colours ───────────────────────────────────────────────────────────────────
G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'
C = '\033[96m'; B = '\033[1m';  M = '\033[95m'; RST = '\033[0m'


# ──────────────────────────────────────────────────────────────────────────────
# Direct debugger session (no bridge — boots mupen with --savestate)
# ──────────────────────────────────────────────────────────────────────────────

class DirectDebugger:
    """Directly controls mupen64plus via PTY debugger CLI."""

    def __init__(self):
        self._session = None

    def start(self, savestate_path: str | None = None):
        from n64train.runtime.debugger_cli import DebuggerCliConfig, DebuggerCliSession
        import shutil

        if CFG_DIR.exists():
            shutil.rmtree(str(CFG_DIR))
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        DUMP_DIR.mkdir(parents=True, exist_ok=True)

        config = DebuggerCliConfig(
            ui_binary=Path(M64P_BIN),
            corelib=Path(CORELIB),
            rom_path=Path(ROM_PATH),
            plugindir=Path(PLUG_DIR),
            configdir=CFG_DIR,
            datadir=Path(DATA_DIR),
            workdir=N64_ROOT,
            gfx_plugin=GFX_PLUGIN,
            audio_plugin='dummy',
            input_plugin=PLUGIN,
            rsp_plugin=RSP_PLUGIN,
            nospeedlimit=False,
            emumode=0,
            dump_dir=DUMP_DIR,
            startup_timeout_s=60.0,
        )

        # We'll monkey-patch the session to inject --savestate into the launch command
        self._session = DebuggerCliSession(config)

        # Override the start method to add --savestate
        if savestate_path:
            _original_start = self._session.start
            session = self._session
            cfg = config

            def _patched_start():
                """Start with --savestate injected into the command line."""
                import pty
                import subprocess
                import selectors

                if session.is_alive():
                    return

                cmd = [str(cfg.ui_binary)]
                cmd += ["--debug", "--corelib", str(cfg.corelib)]
                if cfg.plugindir is not None:
                    cmd += ["--plugindir", str(cfg.plugindir)]
                if cfg.configdir is not None:
                    cmd += ["--configdir", str(cfg.configdir)]
                if cfg.datadir is not None:
                    cmd += ["--datadir", str(cfg.datadir)]
                cmd += ["--gfx", cfg.gfx_plugin]
                cmd += ["--audio", cfg.audio_plugin]
                cmd += ["--input", cfg.input_plugin]
                cmd += ["--rsp", cfg.rsp_plugin]
                if cfg.nospeedlimit:
                    cmd += ["--nospeedlimit"]
                cmd += ["--emumode", str(cfg.emumode)]
                # ── Add savestate loading ──
                cmd += ["--savestate", savestate_path]
                cmd += [str(cfg.rom_path)]

                print(f"  CMD: {' '.join(cmd[:6])} ... --savestate {savestate_path}")

                master_fd, slave_fd = pty.openpty()
                try:
                    session._proc = subprocess.Popen(
                        cmd,
                        cwd=str(cfg.workdir or cfg.rom_path.parent),
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                        bufsize=0,
                        close_fds=True,
                        env={**os.environ, 'N64TRAIN_CTRL_P1': P1_CTRL, 'N64TRAIN_CTRL_P2': P2_CTRL},
                    )
                finally:
                    os.close(slave_fd)
                session._master_fd = master_fd
                session._selector = selectors.DefaultSelector()
                session._selector.register(master_fd, selectors.EVENT_READ)
                session._read_until_prompt(timeout_s=cfg.startup_timeout_s)

            self._session.start = _patched_start

        self._session.start()

    def command(self, cmd: str, timeout_s: float | None = None) -> str:
        return self._session.command(cmd, timeout_s=timeout_s)

    def close(self):
        if self._session:
            self._session.close()

    def is_alive(self) -> bool:
        return self._session is not None and self._session.is_alive()


# Global debugger instance
_dbg: DirectDebugger | None = None


def dbg(cmd: str, timeout: float = 15.0) -> str:
    return _dbg.command(cmd, timeout_s=timeout)


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
            with open(path, 'w+b') as f:
                f.write(b'\x00' * 4)
        with open(path, 'r+b') as f:
            m = mmap.mmap(f.fileno(), 4)
            m.seek(0)
            m.write(struct.pack('<Hbb', btns & 0xFFFF, 0, 0))
            m.flush()
            m.close()


def snap_range(start: int, end: int) -> dict[int, int]:
    """Read all u32 words in [start, end) and return {addr: value}."""
    result = {}
    for addr in range(start, end, 4):
        try:
            result[addr] = read_u32(addr)
        except Exception:
            result[addr] = 0xDEADDEAD
    return result


def settle(frames=120):
    step(frames)


# ──────────────────────────────────────────────────────────────────────────────
# Controller routing detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_routes() -> dict[str, str]:
    """Detect which fighter each controller channel drives.
    Since we can't reload savestate, just test both channels from current state."""
    routes = {}
    # Take baseline positions
    x1_a = read_s16hi(P1_X_ADDR)
    x2_a = read_s16hi(P2_X_ADDR)

    # Test P1 channel
    ctrl(p1=BTN_D_RIGHT)
    step(40)
    ctrl()
    step(10)
    x1_b = read_s16hi(P1_X_ADDR)
    x2_b = read_s16hi(P2_X_ADDR)
    d1_p1ch = abs(x1_b - x1_a)
    d2_p1ch = abs(x2_b - x2_a)
    if d1_p1ch == 0 and d2_p1ch == 0:
        routes['p1'] = 'none'
    elif d1_p1ch >= d2_p1ch:
        routes['p1'] = 'p1_fighter'
    else:
        routes['p1'] = 'p2_fighter'

    # Reset positions by walking back
    ctrl(p1=BTN_D_LEFT)
    step(40)
    ctrl()
    step(10)

    # Test P2 channel
    x1_a2 = read_s16hi(P1_X_ADDR)
    x2_a2 = read_s16hi(P2_X_ADDR)
    ctrl(p2=BTN_D_RIGHT)
    step(40)
    ctrl()
    step(10)
    x1_b2 = read_s16hi(P1_X_ADDR)
    x2_b2 = read_s16hi(P2_X_ADDR)
    d1_p2ch = abs(x1_b2 - x1_a2)
    d2_p2ch = abs(x2_b2 - x2_a2)
    if d1_p2ch == 0 and d2_p2ch == 0:
        routes['p2'] = 'none'
    elif d1_p2ch >= d2_p2ch:
        routes['p2'] = 'p1_fighter'
    else:
        routes['p2'] = 'p2_fighter'

    # Walk P2 back
    ctrl(p2=BTN_D_LEFT)
    step(40)
    ctrl()
    step(10)

    return routes


# ──────────────────────────────────────────────────────────────────────────────
# Core scan: one side attacks, other side's struct is scanned
# ──────────────────────────────────────────────────────────────────────────────

def scan_hitstun(
    *,
    attacker_channel: str,
    attacker_btn: int,
    attacker_walk_btn: int,
    defender_hp_addr: int,
    defender_scan_start: int,
    defender_scan_end: int,
    defender_label: str,
    attack_label: str,
    num_attacks: int = 3,
    pre_frames: int = 10,
    hold_frames: int = 12,
    post_frames: int = 40,
) -> dict:
    """Run multiple attacks and find hitstun candidates via frame-by-frame scanning."""

    print(f"\n{B}{'═'*70}{RST}")
    print(f"{B}  HITSTUN SCAN: {attack_label} → {defender_label} is the victim{RST}")
    print(f"{B}{'═'*70}{RST}\n")

    n_words = (defender_scan_end - defender_scan_start) // 4
    print(f"  Scan range: 0x{defender_scan_start:08X} – 0x{defender_scan_end:08X} ({n_words} words)")

    # ── Phase 1: Walk attacker into range ─────────────────────────────────
    print(f"  Walking attacker into range (180 frames)...")
    if attacker_channel == 'p1':
        ctrl(p1=attacker_walk_btn)
    else:
        ctrl(p2=attacker_walk_btn)
    step(180)
    ctrl()
    step(30)

    # ── Phase 2: Idle baseline ────────────────────────────────────────────
    print(f"  Taking idle baseline...")
    baseline = snap_range(defender_scan_start, defender_scan_end)
    hp_before = read_u32(defender_hp_addr)
    print(f"  Baseline {defender_label}_HP = 0x{hp_before:08X}")

    baseline_zero = {addr for addr, val in baseline.items() if val == 0}
    baseline_nonzero = {addr for addr, val in baseline.items() if val != 0}
    print(f"  Zero addrs: {len(baseline_zero)}, non-zero: {len(baseline_nonzero)}")

    # Track per-address timeline
    addr_timeline: dict[int, list] = defaultdict(list)
    ever_activated: dict[int, list[int]] = defaultdict(list)
    health_dropped = False

    # ── Phase 3: Attack loop ──────────────────────────────────────────────
    for atk_idx in range(num_attacks):
        hp_start = read_u32(defender_hp_addr)
        print(f"\n  {C}Attack {atk_idx+1}/{num_attacks}{RST}: HP before = 0x{hp_start:08X}")

        # PRE: both idle
        print(f"    PRE  ({pre_frames}f):", end='', flush=True)
        ctrl()
        for f in range(pre_frames):
            step(1)
            s = snap_range(defender_scan_start, defender_scan_end)
            changed = [a for a in baseline_zero if s.get(a, 0) != 0]
            if changed:
                print(f" [{f}:Δ{len(changed)}]", end='', flush=True)
                for addr in changed:
                    addr_timeline[addr].append((atk_idx, 'pre', f, s[addr]))
        print()

        # HOLD: attacker punches
        print(f"    HOLD ({hold_frames}f):", end='', flush=True)
        if attacker_channel == 'p1':
            ctrl(p1=attacker_btn)
        else:
            ctrl(p2=attacker_btn)
        for f in range(hold_frames):
            step(1)
            s = snap_range(defender_scan_start, defender_scan_end)
            hp = read_u32(defender_hp_addr)
            if hp < hp_start:
                print(f" [{f}:HP↓]", end='', flush=True)
                health_dropped = True
            changed = [a for a in baseline_zero if s.get(a, 0) != 0]
            if changed:
                print(f" [{f}:Δ{len(changed)}]", end='', flush=True)
                for addr in changed:
                    addr_timeline[addr].append((atk_idx, 'hold', f, s[addr]))
                    ever_activated[addr].append(s[addr])
        print()

        # Release
        ctrl()

        # POST: defender reeling
        print(f"    POST ({post_frames}f):", end='', flush=True)
        for f in range(post_frames):
            step(1)
            s = snap_range(defender_scan_start, defender_scan_end)
            hp = read_u32(defender_hp_addr)
            if hp < hp_start and not health_dropped:
                print(f" [{f}:HP↓]", end='', flush=True)
                health_dropped = True
            changed = [a for a in baseline_zero if s.get(a, 0) != 0]
            if changed:
                print(f" [{f}:Δ{len(changed)}]", end='', flush=True)
                for addr in changed:
                    addr_timeline[addr].append((atk_idx, 'post', f, s[addr]))
                    ever_activated[addr].append(s[addr])
        print()

        hp_end = read_u32(defender_hp_addr)
        hit = hp_end < hp_start
        print(f"    HP after = 0x{hp_end:08X}  {'✓ HIT!' if hit else '✗ MISS'}")

        # Brief pause between attacks
        ctrl()
        step(20)

    # ── Phase 4: Analysis ─────────────────────────────────────────────────
    print(f"\n{B}{'─'*70}{RST}")
    print(f"{B}  ANALYSIS — {defender_label} hitstun candidates{RST}")
    print(f"{B}{'─'*70}{RST}\n")

    if not ever_activated:
        print(f"  {Y}No addresses activated from zero baseline!{RST}")
        return {'candidates': [], 'activated': {}, 'timeline': {}}

    # Filter: POST-phase activations are the best hitstun signals
    post_activated = {
        addr for addr, entries in addr_timeline.items()
        if any(e[1] == 'post' for e in entries)
    }
    hold_only = set(ever_activated.keys()) - post_activated

    print(f"  Addresses 0→non-zero: {len(ever_activated)}")
    print(f"  Active in POST (defender reeling): {len(post_activated)}")
    print(f"  Active in HOLD only (attacker signal): {len(hold_only)}")

    # Score candidates
    scored: list[tuple[float, int, str]] = []
    for addr in post_activated:
        entries = addr_timeline[addr]
        post_entries = [e for e in entries if e[1] == 'post']
        pre_entries  = [e for e in entries if e[1] == 'pre']
        attacks_seen = len(set(e[0] for e in post_entries))

        score = len(post_entries) * 2.0 - len(pre_entries) * 3.0 + attacks_seen * 5.0
        offset = addr - defender_scan_start
        scored.append((score, addr, f"offset=+0x{offset:03X}"))

    scored.sort(key=lambda x: -x[0])

    print(f"\n  {G}{B}TOP HITSTUN CANDIDATES:{RST}\n")
    s32 = lambda v: v if v < 0x80000000 else v - 0x100000000
    for rank, (score, addr, note) in enumerate(scored[:25]):
        entries = addr_timeline[addr]
        values = sorted(set(ever_activated[addr]))
        phases = {e[1] for e in entries}
        attacks_hit = len(set(e[0] for e in entries))
        val_str = ', '.join(f'0x{v:08X}({s32(v):+d})' for v in values[:5])

        marker = ''
        if score >= 10: marker = f' {G}★★★ STRONG{RST}'
        elif score >= 5: marker = f' {C}★★ GOOD{RST}'
        elif score >= 0: marker = f' {Y}★ WEAK{RST}'

        print(f"  {rank+1:2d}. {B}0x{addr:08X}{RST}  {note}  score={score:.1f}{marker}")
        print(f"      phases={phases}  attacks={attacks_hit}/{num_attacks}")
        print(f"      values: {val_str}")
        if rank < 10:
            for e in entries[:15]:
                atk, phase, frame, val = e
                print(f"        atk{atk} {phase}[{frame:2d}] = 0x{val:08X} ({s32(val):+d})")
        print()

    # ── Bonus: non-zero baseline addresses with PERFECT ARC ──────────────
    print(f"\n{B}  BONUS: Non-zero baseline addresses that changed (PERFECT ARC check):{RST}")
    # Take a quick pre/during/post snapshot with one more attack
    pre = snap_range(defender_scan_start, defender_scan_end)
    if attacker_channel == 'p1':
        ctrl(p1=attacker_btn)
    else:
        ctrl(p2=attacker_btn)
    step(12)
    ctrl()
    step(5)
    mid = snap_range(defender_scan_start, defender_scan_end)
    step(25)
    late = snap_range(defender_scan_start, defender_scan_end)

    arc_candidates = []
    for addr in sorted(pre.keys()):
        v_pre = pre[addr]
        v_mid = mid.get(addr, 0)
        v_late = late.get(addr, 0)
        if v_pre == 0:
            continue
        if v_mid != v_pre and v_late == v_pre:
            offset = addr - defender_scan_start
            print(f"  {M}0x{addr:08X}{RST}  +0x{offset:03X}  "
                  f"base=0x{v_pre:08X} → mid=0x{v_mid:08X} → back=0x{v_late:08X}  "
                  f"{G}PERFECT ARC ★★★{RST}")
            arc_candidates.append((addr, v_pre, v_mid, v_late))

    if not arc_candidates:
        print(f"  (none found)")

    return {
        'candidates': scored,
        'arc_candidates': arc_candidates,
        'activated': dict(ever_activated),
        'timeline': {a: e for a, e in addr_timeline.items()},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    global _dbg

    ap = argparse.ArgumentParser(description='MK4 hitstun address scanner')
    ap.add_argument('--boot', action='store_true', help='Boot emulator with debugger')
    ap.add_argument('--scan-side', default='both', choices=['p1', 'p2', 'both'],
                    help='Which defender to scan')
    ap.add_argument('--attacks', type=int, default=3, help='Attacks per scan')
    args = ap.parse_args()

    if args.boot:
        if not SAVE_PATH.exists():
            print(f"Savestate not found: {SAVE_PATH}")
            sys.exit(1)
        if not os.path.exists(ROM_PATH):
            print(f"ROM not found: {ROM_PATH}")
            sys.exit(1)

        print(f"{B}Booting mupen64plus with --debug --savestate...{RST}")
        _dbg = DirectDebugger()
        _dbg.start(savestate_path=str(SAVE_PATH))
        print(f"{G}Debugger ready{RST}\n")
    else:
        print(f"{R}--boot is required (direct debugger mode){RST}")
        sys.exit(1)

    try:
        # Pause and settle
        dbg("pause")
        time.sleep(0.3)
        print(f"Settling (120 frames)...")
        settle()

        # Detect routing
        print(f"\n{B}Detecting controller routing...{RST}")
        routes = detect_routes()
        print(f"  p1 channel → {routes.get('p1', '?')}")
        print(f"  p2 channel → {routes.get('p2', '?')}")

        p1_fighter_ch = None
        p2_fighter_ch = None
        for ch, fighter in routes.items():
            if fighter == 'p1_fighter':
                p1_fighter_ch = ch
            elif fighter == 'p2_fighter':
                p2_fighter_ch = ch

        if not p1_fighter_ch or not p2_fighter_ch:
            print(f"{R}Could not isolate both fighters!{RST}")
            print(f"  Routes: {routes}")
            return

        print(f"  P1 fighter → {p1_fighter_ch} channel")
        print(f"  P2 fighter → {p2_fighter_ch} channel")

        results = {}

        # ── Scan P2 as victim ─────────────────────────────────────────────
        if args.scan_side in ('p2', 'both'):
            results['p2_victim'] = scan_hitstun(
                attacker_channel=p1_fighter_ch,
                attacker_btn=BTN_A,
                attacker_walk_btn=BTN_D_RIGHT,
                defender_hp_addr=P2_HP_ADDR,
                defender_scan_start=P2_SCAN_START,
                defender_scan_end=P2_SCAN_END,
                defender_label='P2',
                attack_label='P1 LOW PUNCH → P2',
                num_attacks=args.attacks,
            )

        # ── Scan P1 as victim ─────────────────────────────────────────────
        if args.scan_side in ('p1', 'both'):
            results['p1_victim'] = scan_hitstun(
                attacker_channel=p2_fighter_ch,
                attacker_btn=BTN_A,
                attacker_walk_btn=BTN_D_LEFT,
                defender_hp_addr=P1_HP_ADDR,
                defender_scan_start=P1_SCAN_START,
                defender_scan_end=P1_SCAN_END,
                defender_label='P1',
                attack_label='P2 LOW PUNCH → P1',
                num_attacks=args.attacks,
            )

        # ── Summary ──────────────────────────────────────────────────────
        print(f"\n{B}{'═'*70}{RST}")
        print(f"{B}  FINAL SUMMARY{RST}")
        print(f"{B}{'═'*70}{RST}\n")

        for label, res in results.items():
            candidates = res.get('candidates', [])
            arcs = res.get('arc_candidates', [])
            strong = [c for c in candidates if c[0] >= 10]
            base = P2_BASE if 'p2' in label else P1_BASE

            print(f"  {label}:")
            if strong:
                for s, a, n in strong[:5]:
                    offset = a - base
                    print(f"    {G}★★★{RST} 0x{a:08X}  (base+0x{offset:03X})  score={s:.1f}")
            elif candidates:
                for s, a, n in candidates[:5]:
                    offset = a - base
                    print(f"    {Y}★{RST} 0x{a:08X}  (base+0x{offset:03X})  score={s:.1f}")
            else:
                print(f"    {R}No zero-baseline candidates{RST}")

            if arcs:
                print(f"    Non-zero PERFECT ARC candidates:")
                for a, vpre, vmid, vlate in arcs[:5]:
                    offset = a - base
                    print(f"      {M}★★★{RST} 0x{a:08X}  (base+0x{offset:03X})  "
                          f"base=0x{vpre:08X}→0x{vmid:08X}→0x{vlate:08X}")
            print()

        # Save results
        out_path = N64_ROOT / 'training/data/reverse/probe_runs' / f'hitstun_scan_{time.strftime("%Y%m%d_%H%M%S")}.json'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {}
        for label, res in results.items():
            serializable[label] = {
                'candidates': [(s, f'0x{a:08X}', n) for s, a, n in res.get('candidates', [])[:30]],
                'arc_candidates': [(f'0x{a:08X}', f'0x{vp:08X}', f'0x{vm:08X}', f'0x{vl:08X}')
                                   for a, vp, vm, vl in res.get('arc_candidates', [])],
            }
        out_path.write_text(json.dumps(serializable, indent=2) + '\n')
        print(f"  Results saved: {out_path}")
        print(f"\n{G}Done.{RST}")

    finally:
        ctrl()
        if _dbg:
            _dbg.close()
        print("Emulator shut down.")


if __name__ == '__main__':
    main()
