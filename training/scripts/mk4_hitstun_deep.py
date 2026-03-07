#!/usr/bin/env python3
"""
mk4_hitstun_deep.py — Deep verification of hitstun candidates.

Reads ONLY the top candidate addresses from the broad scan, but with:
 • 5 attacks per type (LP, HP, LK, HK)
 • Full frame-by-frame value timeline for each candidate
 • Longer post-attack window (60 frames) to see full recovery arc
 • Both P1→P2 and P2→P1 directions

This gives us the PERFECT ARC pattern for each candidate:
  idle → hit → recovery countdown → idle

Usage:
    python3 training/scripts/mk4_hitstun_deep.py --boot
    python3 training/scripts/mk4_hitstun_deep.py --boot --attacks 3  # fewer for speed
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import re
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))

ROM_PATH  = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
SAVE_PATH = N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st'
CFG_DIR   = N64_ROOT / '.m64p/instances/hitstun_deep/config'
DUMP_DIR  = N64_ROOT / 'training/data/bridge/debugger_dumps/hitstun_deep'
P1_CTRL   = '/tmp/mk4_ctrl_deep_p1'
P2_CTRL   = '/tmp/mk4_ctrl_deep_p2'

_IS_LINUX = sys.platform.startswith('linux')
if _IS_LINUX:
    M64P_BIN = '/usr/games/mupen64plus'
    CORELIB  = '/usr/lib/x86_64-linux-gnu/libmupen64plus.so.2'
    PLUG_DIR = '/usr/lib/x86_64-linux-gnu/mupen64plus'
    DATA_DIR = '/usr/share/mupen64plus'
    PLUGIN   = str(N64_ROOT / 'vendor/n64train-input/n64train-input.so')
    GFX      = 'mupen64plus-video-rice.so'
    RSP      = 'mupen64plus-rsp-hle.so'
else:
    M64P_BIN = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
    CORELIB  = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
    PLUG_DIR = '/opt/homebrew/lib/mupen64plus'
    DATA_DIR = '/opt/homebrew/share/mupen64plus'
    PLUGIN   = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
    GFX      = 'mupen64plus-video-rice.dylib'
    RSP      = 'mupen64plus-rsp-hle.dylib'

# ── Addresses ─────────────────────────────────────────────────────────────────
P1_HP   = 0x800FE0D8
P2_HP   = 0x80126F54
P1_X    = 0x800F87F8
P2_X    = 0x8006A060
P1_BASE = 0x800FE000
P2_BASE = 0x80126E00

# TOP CANDIDATES from broad scan — we'll deeply verify these
P2_CANDIDATES = {
    'P2+074': P2_BASE + 0x074,  # recovery timer (score=110)
    'P2+080': P2_BASE + 0x080,  # anim frame counter (score=85)
    'P2+094': P2_BASE + 0x094,  # attack sig (score=85)
    'P2+04C': P2_BASE + 0x04C,  # PERFECT ARC (non-zero base)
    'P2+0C0': P2_BASE + 0x0C0,  # action state / anim ptr
    'P2+0CC': P2_BASE + 0x0CC,  # state flag 2→1→2
    'P2+19C': P2_BASE + 0x19C,  # OLD wrong addr (verify it's wrong)
    'P2+0B0': P2_BASE + 0x0B0,  # check symmetric with P1
    'P2+08C': P2_BASE + 0x08C,  # check symmetric with P1
    'P2+178': P2_BASE + 0x178,  # ground flag
    'P2_HP':  P2_HP,
}

P1_CANDIDATES = {
    'P1+04C': P1_BASE + 0x04C,  # victim state (score=140)
    'P1+08C': P1_BASE + 0x08C,  # action state (score=140)
    'P1+0B0': P1_BASE + 0x0B0,  # score=140
    'P1+310': P1_BASE + 0x310,  # attackbox
    'P1+4FC': P1_BASE + 0x4FC,  # binary flag (score=110)
    'P1+894': P1_BASE + 0x894,  # score=140
    'P1+2A8': P1_BASE + 0x2A8,  # score=140
    'P1+7E0': P1_BASE + 0x7E0,  # score=140
    'P1+7F0': P1_BASE + 0x7F0,  # score=140
    'P1+0F8': P1_BASE + 0x0F8,  # ground flag
    'P1+90C': P1_BASE + 0x90C,  # Y velocity
    'P1_HP':  P1_HP,
}

# Attacks to test
BTN_A       = 1 << 7   # LP
BTN_B       = 1 << 6   # HP
BTN_C_RIGHT = 1 << 8   # LK
BTN_C_UP    = 1 << 11  # HK
BTN_D_RIGHT = 1 << 0
BTN_D_LEFT  = 1 << 1

ATTACKS = {
    'LP': BTN_A,
    'HP': BTN_B,
    'LK': BTN_C_RIGHT,
    'HK': BTN_C_UP,
}

G = '\033[92m'; R = '\033[91m'; Y = '\033[93m'
C = '\033[96m'; B = '\033[1m'; M = '\033[95m'; RST = '\033[0m'

# ── Debugger ──────────────────────────────────────────────────────────────────
_session = None

def boot(savestate_path: str):
    global _session
    from n64train.runtime.debugger_cli import DebuggerCliConfig, DebuggerCliSession
    import shutil, pty, subprocess, selectors

    if CFG_DIR.exists(): shutil.rmtree(str(CFG_DIR))
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    DUMP_DIR.mkdir(parents=True, exist_ok=True)

    cfg = DebuggerCliConfig(
        ui_binary=Path(M64P_BIN), corelib=Path(CORELIB), rom_path=Path(ROM_PATH),
        plugindir=Path(PLUG_DIR), configdir=CFG_DIR, datadir=Path(DATA_DIR),
        workdir=N64_ROOT, gfx_plugin=GFX, audio_plugin='dummy',
        input_plugin=PLUGIN, rsp_plugin=RSP, nospeedlimit=False, emumode=0,
        dump_dir=DUMP_DIR, startup_timeout_s=60.0,
    )
    _session = DebuggerCliSession(cfg)

    # Patch start to inject --savestate
    def _patched_start():
        if _session.is_alive(): return
        cmd = [str(cfg.ui_binary), "--debug", "--corelib", str(cfg.corelib)]
        if cfg.plugindir: cmd += ["--plugindir", str(cfg.plugindir)]
        if cfg.configdir: cmd += ["--configdir", str(cfg.configdir)]
        if cfg.datadir:   cmd += ["--datadir", str(cfg.datadir)]
        cmd += ["--gfx", cfg.gfx_plugin, "--audio", cfg.audio_plugin,
                "--input", cfg.input_plugin, "--rsp", cfg.rsp_plugin]
        if cfg.nospeedlimit: cmd += ["--nospeedlimit"]
        cmd += ["--emumode", str(cfg.emumode), "--savestate", savestate_path, str(cfg.rom_path)]
        master_fd, slave_fd = pty.openpty()
        try:
            _session._proc = subprocess.Popen(
                cmd, cwd=str(cfg.workdir), stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                bufsize=0, close_fds=True,
                env={**os.environ, 'N64TRAIN_CTRL_P1': P1_CTRL, 'N64TRAIN_CTRL_P2': P2_CTRL},
            )
        finally:
            os.close(slave_fd)
        _session._master_fd = master_fd
        _session._selector = selectors.DefaultSelector()
        _session._selector.register(master_fd, selectors.EVENT_READ)
        _session._read_until_prompt(timeout_s=cfg.startup_timeout_s)

    _session.start = _patched_start
    _session.start()

def dbg(cmd: str, timeout: float = 15.0) -> str:
    return _session.command(cmd, timeout_s=timeout)

def read_u32(addr: int) -> int:
    out = dbg(f"mem /1w 0x{addr:08x}")
    for line in out.strip().split('\n'):
        line = line.strip()
        if line.startswith('(dbg)') or line.startswith('PC at') or line.startswith('mem ') or not line:
            continue
        tokens = re.findall(r'[0-9A-Fa-f]{2,16}', line)
        if tokens:
            return int(tokens[-1], 16) & 0xFFFFFFFF
    raise ValueError(f"read_u32 0x{addr:08x}: {out!r}")

def read_s16hi(addr: int) -> int:
    w = read_u32(addr)
    hi = (w >> 16) & 0xFFFF
    return hi if hi < 0x8000 else hi - 0x10000

def step(n=1): dbg(f"frame {n}", timeout=max(15, n + 5))

def ctrl(p1=0, p2=0):
    for path, btns in [(P1_CTRL, p1), (P2_CTRL, p2)]:
        if not os.path.exists(path):
            with open(path, 'w+b') as f: f.write(b'\x00' * 4)
        with open(path, 'r+b') as f:
            m = mmap.mmap(f.fileno(), 4); m.seek(0)
            m.write(struct.pack('<Hbb', btns & 0xFFFF, 0, 0))
            m.flush(); m.close()

def snap(addrs: dict[str, int]) -> dict[str, int]:
    return {name: read_u32(addr) for name, addr in addrs.items()}

def s32(v):
    return v if v < 0x80000000 else v - 0x100000000


# ── Core: deep frame-by-frame scan ───────────────────────────────────────────

def deep_scan_one_attack(
    *,
    attacker_channel: str,
    attack_btn: int,
    attack_name: str,
    walk_btn: int,
    candidates: dict[str, int],
    hp_key: str,
    label: str,
    pre_frames: int = 5,
    hold_frames: int = 15,
    post_frames: int = 60,
    max_retries: int = 3,
) -> dict:
    """One attack, full frame-by-frame readout of all candidates.
    Retries with extra walking if the attack misses."""

    for attempt in range(max_retries + 1):
        # Walk into range — walk longer on retries
        walk_frames = 300 + attempt * 120
        if attacker_channel == 'p1':
            ctrl(p1=walk_btn)
        else:
            ctrl(p2=walk_btn)
        step(walk_frames)
        ctrl()
        step(15)

        timeline: dict[str, list[tuple[str, int, int]]] = {n: [] for n in candidates}
        hp_start = read_u32(candidates[hp_key])

        # PRE
        ctrl()
        for f in range(pre_frames):
            step(1)
            s = snap(candidates)
            for n, v in s.items():
                timeline[n].append(('pre', f, v))

        # HOLD — walk + attack simultaneously to close last gap
        combined_btn = attack_btn | walk_btn
        if attacker_channel == 'p1':
            ctrl(p1=combined_btn)
        else:
            ctrl(p2=combined_btn)
        for f in range(hold_frames):
            step(1)
            s = snap(candidates)
            for n, v in s.items():
                timeline[n].append(('hold', f, v))

        # POST
        ctrl()
        for f in range(post_frames):
            step(1)
            s = snap(candidates)
            for n, v in s.items():
                timeline[n].append(('post', f, v))

        hp_end = read_u32(candidates[hp_key])
        hit = hp_end < hp_start
        dmg = hp_start - hp_end if hit else 0

        if hit or attempt == max_retries:
            break
        # Miss — try again with more walking
        ctrl()
        step(20)

    return {
        'attack': attack_name,
        'hit': hit,
        'hp_start': hp_start,
        'hp_end': hp_end,
        'damage': dmg,
        'timeline': timeline,
    }


def analyze_and_print(
    results: list[dict],
    candidates: dict[str, int],
    hp_key: str,
    label: str,
):
    """Analyze all attack results for one side and print clean summary."""

    print(f"\n{B}{'═'*80}{RST}")
    print(f"{B}  DEEP ANALYSIS: {label}{RST}")
    print(f"{B}{'═'*80}{RST}\n")

    # Only analyze attacks that actually hit
    hits = [r for r in results if r['hit']]
    misses = [r for r in results if not r['hit']]
    print(f"  Hits: {len(hits)}/{len(results)}  Misses: {len(misses)}")
    for r in results:
        tag = f"{G}HIT{RST}" if r['hit'] else f"{Y}MISS{RST}"
        print(f"    {r['attack']}: {tag}  dmg={r['damage']}")
    print()

    if not hits:
        print(f"  {R}No hits landed — cannot analyze hitstun!{RST}")
        return {}

    # For each candidate, compute signal quality metrics
    scores = {}
    for name in candidates:
        if name == hp_key:
            continue

        # Gather per-hit statistics
        idle_values = set()      # values during PRE of all attacks
        active_values = set()    # values during POST frames 0-30 of hits
        late_values = set()      # values during POST frames 40-60 of hits
        first_change_frame = []  # frame where value first differs from idle
        last_change_frame = []   # last frame where value differs from idle
        arc_count = 0            # number of hits showing PERFECT ARC

        for r in hits:
            tl = r['timeline'][name]
            pre_vals = [v for phase, f, v in tl if phase == 'pre']
            post_vals = [(f, v) for phase, f, v in tl if phase == 'post']
            hold_vals = [(f, v) for phase, f, v in tl if phase == 'hold']

            if not pre_vals:
                continue
            idle_val = pre_vals[-1]  # last PRE frame as reference
            idle_values.add(idle_val)

            # Find first frame where value changes from idle
            all_frames = hold_vals + post_vals
            first_diff = None
            last_diff = None
            for f, v in all_frames:
                if v != idle_val:
                    active_values.add(v)
                    if first_diff is None:
                        first_diff = f
                    last_diff = f

            if first_diff is not None:
                first_change_frame.append(first_diff)
            if last_diff is not None:
                last_change_frame.append(last_diff)

            # Check for return to idle (PERFECT ARC)
            late = [v for f, v in post_vals if f >= 40]
            if late:
                late_values.update(late)
                if late[-1] == idle_val and first_diff is not None:
                    arc_count += 1

        # Score the candidate
        n_hits = len(hits)
        consistency = len(first_change_frame) / n_hits if n_hits else 0
        arc_rate = arc_count / n_hits if n_hits else 0
        is_binary = len(active_values) <= 3
        idle_stable = len(idle_values) <= 2

        # True hitstun: activates AFTER hit, returns to idle, consistent
        score = 0.0
        score += consistency * 30        # activates on every hit
        score += arc_rate * 30           # returns to idle (PERFECT ARC)
        score += (10 if idle_stable else 0)  # steady idle value
        score += (10 if is_binary else 0)    # simple on/off signal
        # Bonus: if it only activates in post (victim), not hold (attacker)
        hold_only_changes = sum(1 for r in hits
                                for phase, f, v in r['timeline'][name]
                                if phase == 'hold' and v != list(idle_values)[0] if idle_values
                               ) if idle_values else 0
        post_changes = sum(1 for r in hits
                           for phase, f, v in r['timeline'][name]
                           if phase == 'post' and v != list(idle_values)[0] if idle_values
                          ) if idle_values else 0
        if post_changes > 0 and hold_only_changes == 0:
            score += 20  # pure victim signal

        scores[name] = {
            'score': score,
            'consistency': consistency,
            'arc_rate': arc_rate,
            'idle_values': idle_values,
            'active_sample': sorted(active_values)[:5],
            'is_binary': is_binary,
            'idle_stable': idle_stable,
            'first_frames': first_change_frame,
            'last_frames': last_change_frame,
            'arc_count': arc_count,
        }

    # Sort and print
    ranked = sorted(scores.items(), key=lambda x: -x[1]['score'])
    for rank, (name, info) in enumerate(ranked):
        sc = info['score']
        if sc >= 80: marker = f'{G}★★★ EXCELLENT{RST}'
        elif sc >= 50: marker = f'{C}★★ GOOD{RST}'
        elif sc >= 20: marker = f'{Y}★ WEAK{RST}'
        else: marker = f'{R}✗ NOISE{RST}'

        addr = candidates[name]
        idle_str = ', '.join(f'0x{v:08X}' for v in sorted(info['idle_values']))
        active_str = ', '.join(f'0x{v:08X}({s32(v):+d})' for v in info['active_sample'])

        print(f"  {rank+1:2d}. {B}{name}{RST}  0x{addr:08X}  score={sc:.0f}  {marker}")
        print(f"      consistency={info['consistency']:.0%}  arc_rate={info['arc_rate']:.0%}  "
              f"binary={'Y' if info['is_binary'] else 'N'}  idle_stable={'Y' if info['idle_stable'] else 'N'}")
        print(f"      idle: {idle_str}")
        print(f"      active: {active_str}")
        if info['first_frames']:
            avg_first = sum(info['first_frames']) / len(info['first_frames'])
            avg_last = sum(info['last_frames']) / len(info['last_frames']) if info['last_frames'] else 0
            print(f"      first_change: avg frame {avg_first:.1f}  last_change: avg frame {avg_last:.1f}")

        # Print compact timeline for top candidates
        if rank < 6 and hits:
            r = hits[0]  # Show first hit only
            tl = r['timeline'][name]
            vals = [v for _, _, v in tl]
            # Compress: show value changes only
            changes = []
            prev_v = None
            for i, (phase, f, v) in enumerate(tl):
                if v != prev_v:
                    changes.append(f"{phase}[{f}]=0x{v:08X}")
                    prev_v = v
            if len(changes) > 20:
                changes = changes[:10] + ['...'] + changes[-5:]
            print(f"      timeline({r['attack']}): {' → '.join(changes)}")
        print()

    return scores


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Deep hitstun verification')
    ap.add_argument('--boot', action='store_true')
    ap.add_argument('--attacks', type=int, default=5, help='Attacks per type')
    ap.add_argument('--side', default='both', choices=['p1', 'p2', 'both'])
    args = ap.parse_args()

    if not args.boot:
        print(f"{R}--boot required{RST}"); sys.exit(1)

    print(f"{B}Booting for deep hitstun verification...{RST}")
    boot(str(SAVE_PATH))
    print(f"{G}Ready{RST}\n")

    try:
        dbg("pause"); time.sleep(0.3)
        step(120)

        # Detect routing
        print(f"{B}Detecting controller routing...{RST}")
        x1a = read_s16hi(P1_X); x2a = read_s16hi(P2_X)
        ctrl(p1=BTN_D_RIGHT); step(40); ctrl(); step(10)
        x1b = read_s16hi(P1_X); x2b = read_s16hi(P2_X)
        d1 = abs(x1b-x1a); d2 = abs(x2b-x2a)
        p1_ch = 'p1' if d1 >= d2 else 'p2'
        ctrl(p1=BTN_D_LEFT); step(40); ctrl(); step(10)

        x1a2 = read_s16hi(P1_X); x2a2 = read_s16hi(P2_X)
        ctrl(p2=BTN_D_RIGHT); step(40); ctrl(); step(10)
        x1b2 = read_s16hi(P1_X); x2b2 = read_s16hi(P2_X)
        d1b = abs(x1b2-x1a2); d2b = abs(x2b2-x2a2)
        p2_ch = 'p2' if d2b >= d1b else 'p1'
        ctrl(p2=BTN_D_LEFT); step(40); ctrl(); step(10)

        print(f"  P1 fighter → {p1_ch} channel")
        print(f"  P2 fighter → {p2_ch} channel")

        all_results = {}

        # ── P2 as victim (P1 attacks) ─────────────────────────────────────
        if args.side in ('p2', 'both'):
            p2_results = []
            for atk_name, atk_btn in ATTACKS.items():
                print(f"\n  {C}=== P1 {atk_name} → P2 (x{args.attacks}) ==={RST}")
                for i in range(args.attacks):
                    print(f"    Attack {i+1}/{args.attacks}...", end='', flush=True)
                    r = deep_scan_one_attack(
                        attacker_channel=p1_ch, attack_btn=atk_btn,
                        attack_name=f'P1_{atk_name}', walk_btn=BTN_D_RIGHT,
                        candidates=P2_CANDIDATES, hp_key='P2_HP', label='P2 victim',
                    )
                    tag = f" {G}HIT dmg={r['damage']}{RST}" if r['hit'] else f" {Y}MISS{RST}"
                    print(tag)
                    p2_results.append(r)

            p2_scores = analyze_and_print(p2_results, P2_CANDIDATES, 'P2_HP', 'P2 AS VICTIM (P1 attacks)')
            all_results['p2_victim'] = {'results': p2_results, 'scores': p2_scores}

        # ── P1 as victim (P2 attacks) ─────────────────────────────────────
        if args.side in ('p1', 'both'):
            p1_results = []
            for atk_name, atk_btn in ATTACKS.items():
                print(f"\n  {C}=== P2 {atk_name} → P1 (x{args.attacks}) ==={RST}")
                for i in range(args.attacks):
                    print(f"    Attack {i+1}/{args.attacks}...", end='', flush=True)
                    r = deep_scan_one_attack(
                        attacker_channel=p2_ch, attack_btn=atk_btn,
                        attack_name=f'P2_{atk_name}', walk_btn=BTN_D_LEFT,
                        candidates=P1_CANDIDATES, hp_key='P1_HP', label='P1 victim',
                    )
                    tag = f" {G}HIT dmg={r['damage']}{RST}" if r['hit'] else f" {Y}MISS{RST}"
                    print(tag)
                    p1_results.append(r)

            p1_scores = analyze_and_print(p1_results, P1_CANDIDATES, 'P1_HP', 'P1 AS VICTIM (P2 attacks)')
            all_results['p1_victim'] = {'results': p1_results, 'scores': p1_scores}

        # ── Save results ──────────────────────────────────────────────────
        out_path = N64_ROOT / 'training/data/reverse/probe_runs' / f'hitstun_deep_{time.strftime("%Y%m%d_%H%M%S")}.json'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize scores (sets aren't JSON-serializable)
        serializable = {}
        for side, data in all_results.items():
            sc = {}
            for name, info in data.get('scores', {}).items():
                sc[name] = {
                    'score': info['score'],
                    'consistency': info['consistency'],
                    'arc_rate': info['arc_rate'],
                    'is_binary': info['is_binary'],
                    'arc_count': info['arc_count'],
                    'idle_values': [f'0x{v:08X}' for v in sorted(info['idle_values'])],
                    'active_sample': [f'0x{v:08X}' for v in info['active_sample']],
                }
            hit_summary = []
            for r in data.get('results', []):
                hit_summary.append({
                    'attack': r['attack'],
                    'hit': r['hit'],
                    'damage': r['damage'],
                })
            serializable[side] = {'scores': sc, 'hits': hit_summary}
        out_path.write_text(json.dumps(serializable, indent=2) + '\n')
        print(f"\n  Results saved: {out_path}")
        print(f"\n{G}Done.{RST}")

    finally:
        ctrl()
        _session.close()
        print("Shut down.")


if __name__ == '__main__':
    main()
