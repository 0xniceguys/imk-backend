#!/usr/bin/env python3
"""
find_move_state_addrs.py — MK4 Move/Facing/Airborne RAM Scanner
────────────────────────────────────────────────────────────────
Controls BOTH P1 and P2 directly via mmap controller files.
Loads savestate, injects specific inputs, dumps RAM before/after,
and diffs to find:

  - P1/P2 facing direction (flips when players cross over)
  - P1/P2 Y-position / airborne flag (changes during jump)
  - P1/P2 animation/move state ID (changes per move type)
  - P1/P2 hitstun flag (changes when hit)

Strategy per signal:
  FACING  — load state, run 1s neutral, dump; then force CPU to corner
             so P1 and P2 cross over → dump again. Any byte that flipped
             between {0,1} or {0,255} is a facing flag.
  JUMP    — dump at idle; press D_UP for P1 → dump mid-air. Y-tracking
             addresses will increase from 0 during jump arc.
  ATTACK  — dump at idle; press A (LOW_PUNCH) for P1 → dump during
             startup frames. Animation-ID addresses change per attack.
  HITSTUN — dump P2 at full health; let P1 hit P2; re-dump. Byte near
             health address that goes non-zero = hitstun counter.

Usage:
    # Requires emulator + bridge already running with a fight savestate
    python3 training/scripts/find_move_state_addrs.py

    # With --no-launch if emulator is already running:
    python3 training/scripts/find_move_state_addrs.py --no-launch

Output:
    training/data/move_state_scan/results.json
    Console printout of top candidates per signal
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))

# ── Paths ─────────────────────────────────────────────────────────────────────
SOCK       = str(N64_ROOT / 'training/data/bridge/mk4-visible.sock')
SAVE_ST    = str(N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st')
SAVE_ST_FB = str(N64_ROOT / 'training/data/savestates/mk4_arcade/kai_arcade_p1p2.st')
DUMP_DIR   = N64_ROOT / 'training/data/move_state_scan'
RESULT_JSON = DUMP_DIR / 'results.json'

# Controller mmap files — one per player
P1_CTRL = '/tmp/mk4_ctrl'
P2_CTRL = '/tmp/mk4_ctrl_p2'

# N64 RDRAM
RDRAM_BASE = 0x80000000
RDRAM_SIZE = 0x400000   # 4 MB

# ── N64 button bitmask (matches ControllerState write format) ─────────────────
# Format: struct.pack('<Hbb', buttons_u16, analog_x_s8, analog_y_s8)
# Button bits (from mupen64plus input source):
BTN_A       = 1 << 7    # Low Punch in MK4
BTN_B       = 1 << 6    # High Punch
BTN_Z       = 1 << 5    # Block
BTN_START   = 1 << 4
BTN_D_UP    = 1 << 3    # Jump
BTN_D_DOWN  = 1 << 2    # Crouch
BTN_D_LEFT  = 1 << 1    # Walk left / retreat for P1
BTN_D_RIGHT = 1 << 0    # Walk right / advance for P1
BTN_C_UP    = 1 << 11   # High Kick
BTN_C_DOWN  = 1 << 10   # Run
BTN_C_LEFT  = 1 << 9    # Block (alt)
BTN_C_RIGHT = 1 << 8    # Low Kick
BTN_R       = 1 << 12   # Side step out
BTN_L       = 1 << 13   # Side step in


# ── Controller mmap helper ────────────────────────────────────────────────────

class Ctrl:
    """mmap-based N64 controller for one player."""

    def __init__(self, path: str) -> None:
        self.path = path
        # Create file if it doesn't exist
        if not os.path.exists(path):
            with open(path, 'w+b') as f:
                f.write(b'\x00' * 4)
        self._f = open(path, 'r+b')
        self._m = mmap.mmap(self._f.fileno(), 4)
        self.release()

    def _write(self, buttons: int, ax: int = 0, ay: int = 0) -> None:
        self._m.seek(0)
        self._m.write(struct.pack('<Hbb', buttons & 0xFFFF, ax & 0xFF, ay & 0xFF))
        self._m.flush()

    def press(self, buttons: int, ax: int = 0, ay: int = 0) -> None:
        self._write(buttons, ax, ay)

    def release(self) -> None:
        self._write(0, 0, 0)

    def tap(self, buttons: int, hold_secs: float = 0.12) -> None:
        self.press(buttons)
        time.sleep(hold_secs)
        self.release()

    def close(self) -> None:
        self.release()
        self._m.close()
        self._f.close()


# ── Bridge helpers ────────────────────────────────────────────────────────────

def connect():
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    b = SocketEmulatorBridge(SOCK, timeout_sec=15)
    return b, Mk4BridgeHelper(b)


def cmd(fn, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            b, h = connect()
            try:
                return fn(b, h)
            finally:
                try: b.close()
                except: pass
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            if attempt < retries:
                print(f'  [retry] bridge error ({e}), retrying...')
                time.sleep(1.5)
            else:
                raise


def dump(b, label: str) -> bytes:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    out = DUMP_DIR / f'{label}.bin'
    resp = b.debugger_command(
        f'dumpmem 80000000 0x400000 {out}',
        timeout_sec=60, output_tail_chars=512)
    raw = str(resp.get('output', ''))
    if 'M64P_DUMPMEM_OK' not in raw:
        raise RuntimeError(f'dumpmem failed: {raw[-200:]}')
    data = out.read_bytes()
    print(f'  [{label}] {len(data)//1024}KB dumped')
    return data


def load_state(b, h) -> None:
    st = Path(SAVE_ST) if Path(SAVE_ST).exists() else Path(SAVE_ST_FB)
    b.load_savestate_path(st)
    time.sleep(0.5)
    h.run()


# ── Diff engine ───────────────────────────────────────────────────────────────

def diff_bytes(before: bytes, after: bytes,
               max_results: int = 60) -> list[dict[str, Any]]:
    """Return all changed bytes sorted by |delta| desc, with N64 address."""
    size = min(len(before), len(after))
    changes = []
    for off in range(size):
        b0, b1 = before[off], after[off]
        if b0 == b1:
            continue
        addr = RDRAM_BASE + (off ^ 3)   # N64 byte-lane swap
        changes.append({
            'addr': f'0x{addr:08X}',
            'vaddr': addr,
            'offset': off,
            'before': b0,
            'after': b1,
            'delta': b1 - b0,
            'abs_delta': abs(b1 - b0),
        })
    changes.sort(key=lambda x: -x['abs_delta'])
    return changes[:max_results]


def diff_words(before: bytes, after: bytes,
               max_results: int = 30) -> list[dict[str, Any]]:
    """Diff at 32-bit word granularity (catches int16 position in upper halfword)."""
    n = min(len(before), len(after)) // 4
    changes = []
    for i in range(n):
        w0 = struct.unpack_from('>I', before, i * 4)[0]
        w1 = struct.unpack_from('>I', after,  i * 4)[0]
        if w0 == w1:
            continue
        addr = RDRAM_BASE + i * 4
        s0 = w0 if w0 < 0x80000000 else w0 - 0x100000000
        s1 = w1 if w1 < 0x80000000 else w1 - 0x100000000
        h0 = (w0 >> 16) & 0xFFFF; sh0 = h0 if h0 < 0x8000 else h0 - 0x10000
        h1 = (w1 >> 16) & 0xFFFF; sh1 = h1 if h1 < 0x8000 else h1 - 0x10000
        changes.append({
            'addr': f'0x{addr:08X}',
            'vaddr': addr,
            'u32_before': w0, 'u32_after': w1,
            's32_before': s0, 's32_after': s1,
            's16hi_before': sh0, 's16hi_after': sh1,
            's16hi_delta': sh1 - sh0,
            'abs_delta': abs(s1 - s0),
        })
    changes.sort(key=lambda x: -x['abs_delta'])
    return changes[:max_results]


def filter_facing_candidates(before: bytes, after: bytes) -> list[dict]:
    """
    Facing flips are 1-bit or 1-byte toggles — small |delta|, value ∈ {0,1} or {0,0xFF}.
    """
    size = min(len(before), len(after))
    out = []
    for off in range(size):
        b0, b1 = before[off], after[off]
        if b0 == b1:
            continue
        pair = tuple(sorted([b0, b1]))
        if pair in {(0, 1), (0, 255), (0, 128), (1, 2), (0, 2)}:
            addr = RDRAM_BASE + (off ^ 3)
            out.append({'addr': f'0x{addr:08X}', 'before': b0, 'after': b1, 'offset': off})
    return out[:40]


# ── Print helpers ─────────────────────────────────────────────────────────────

def print_byte_table(changes: list[dict], title: str, n: int = 20) -> None:
    print(f'\n  ── {title} (top {min(n, len(changes))}) ──')
    if not changes:
        print('    (no changes)')
        return
    print(f'  {"Address":<14} {"Before":>6} {"After":>6} {"Delta":>8}')
    print('  ' + '-' * 42)
    for c in changes[:n]:
        print(f'  {c["addr"]:<14} {c["before"]:>6} {c["after"]:>6} {c["delta"]:>+8}')


def print_word_table(changes: list[dict], title: str, n: int = 15) -> None:
    print(f'\n  ── {title} ──')
    if not changes:
        print('    (no changes)')
        return
    print(f'  {"Address":<14} {"s16hi_before":>13} {"s16hi_after":>12} {"Δs16hi":>8}')
    print('  ' + '-' * 52)
    for c in changes[:n]:
        print(f'  {c["addr"]:<14} {c["s16hi_before"]:>13} {c["s16hi_after"]:>12} {c["s16hi_delta"]:>+8}')


# ── Scan phases ───────────────────────────────────────────────────────────────

def phase_neutral_baseline(p1: Ctrl, p2: Ctrl) -> bytes:
    """Load state, wait for fight start, dump idle baseline."""
    print('\n[1] Loading savestate + waiting for fight to start...')
    def _load(b, h): load_state(b, h)
    cmd(_load)

    p1.release(); p2.release()
    time.sleep(3.5)   # wait past round-start animation

    print('[1] Pausing + dumping neutral baseline...')
    def _dump(b, h):
        h.pause(); time.sleep(0.2)
        data = dump(b, 'neutral_idle')
        return data
    return cmd(_dump)


def phase_p1_jump(p1: Ctrl, p2: Ctrl, idle: bytes) -> dict:
    """P1 jumps straight up — find Y-position and airborne addresses."""
    print('\n[2] JUMP test — P1 jumps, P2 stays idle...')

    def _run(b, h): h.run()
    cmd(_run)
    time.sleep(0.3)

    # Jump: D_UP tap
    p1.press(BTN_D_UP)
    time.sleep(0.05)   # just past startup

    def _snap(b, h):
        h.pause(); time.sleep(0.15)
        return dump(b, 'p1_airborne')
    mid_air = cmd(_snap)

    p1.release()
    cmd(lambda b, h: h.run())
    time.sleep(0.8)   # land + settle

    byte_changes = diff_bytes(idle, mid_air)
    word_changes = diff_word_increasing(idle, mid_air)   # Y should be > 0
    facing_cands = []   # jump shouldn't flip facing

    print_byte_table(byte_changes, 'P1 JUMP — byte changes (airborne flag candidates)')
    print_word_table(word_changes, 'P1 JUMP — word changes (Y-position candidates)')

    return {'byte_changes': byte_changes, 'word_changes': word_changes}


def diff_word_increasing(before: bytes, after: bytes, n: int = 20) -> list[dict]:
    """Words where s16hi INCREASED — Y goes up during jump."""
    changes = diff_words(before, after, max_results=200)
    return [c for c in changes if c['s16hi_delta'] > 0][:n]


def phase_p1_attack(p1: Ctrl, p2: Ctrl, idle: bytes) -> dict:
    """P1 throws LOW PUNCH — find animation/move ID addresses."""
    print('\n[3] ATTACK test — P1 presses A (Low Punch)...')

    def _run(b, h): h.run()
    cmd(_run)
    time.sleep(0.3)

    # Press A
    p1.press(BTN_A)
    time.sleep(0.04)  # capture during startup frames (before hit)

    def _snap(b, h):
        h.pause(); time.sleep(0.15)
        return dump(b, 'p1_lowpunch')
    attacking = cmd(_snap)

    p1.release()
    cmd(lambda b, h: h.run())
    time.sleep(0.6)

    byte_changes = diff_bytes(idle, attacking)
    print_byte_table(byte_changes, 'P1 LOW PUNCH — byte changes (animation ID candidates)')

    return {'byte_changes': byte_changes}


def phase_p2_attack(p1: Ctrl, p2: Ctrl, idle: bytes) -> dict:
    """P2 throws HIGH PUNCH — find P2 animation ID separately from P1."""
    print('\n[4] ATTACK test — P2 presses B (High Punch)...')

    def _run(b, h): h.run()
    cmd(_run)
    time.sleep(0.3)

    p2.press(BTN_B)
    time.sleep(0.04)

    def _snap(b, h):
        h.pause(); time.sleep(0.15)
        return dump(b, 'p2_highpunch')
    p2_attacking = cmd(_snap)

    p2.release()
    cmd(lambda b, h: h.run())
    time.sleep(0.6)

    byte_changes = diff_bytes(idle, p2_attacking)
    print_byte_table(byte_changes, 'P2 HIGH PUNCH — byte changes (P2 animation ID candidates)')

    return {'byte_changes': byte_changes}


def phase_facing(p1: Ctrl, p2: Ctrl, idle: bytes) -> dict:
    """
    Force a crossover: push P1 hard right past P2 so they swap sides.
    Facing bytes should flip.
    """
    print('\n[5] FACING test — P1 walks through P2 to flip facing...')

    def _run(b, h): h.run()
    cmd(_run)
    time.sleep(0.3)

    # Hold D_RIGHT for 2.5s — should force P1 past P2 if P2 doesn't move
    p1.press(BTN_D_RIGHT)
    p2.release()  # P2 stands still
    time.sleep(2.5)

    def _snap(b, h):
        h.pause(); time.sleep(0.2)
        return dump(b, 'after_crossover')
    after = cmd(_snap)

    p1.release()
    cmd(lambda b, h: h.run())
    time.sleep(0.5)

    facing_cands = filter_facing_candidates(idle, after)
    byte_changes  = diff_bytes(idle, after)

    print(f'\n  ── FACING candidates (value flipped ∈ [0,1]/[0,255]) ──')
    if not facing_cands:
        print('    (none — crossover may not have occurred)')
    for c in facing_cands[:15]:
        print(f'  {c["addr"]}  {c["before"]} → {c["after"]}')

    return {'facing_candidates': facing_cands, 'all_byte_changes': byte_changes}


def phase_hitstun(p1: Ctrl, p2: Ctrl, idle: bytes) -> dict:
    """P1 hits P2 — look for hitstun bytes near health address."""
    print('\n[6] HITSTUN test — P1 walks close and attacks P2...')

    def _run(b, h): h.run()
    cmd(_run)
    time.sleep(0.3)

    # Advance P1 close, then punch
    p1.press(BTN_D_RIGHT)
    time.sleep(1.2)     # close the gap
    p1.press(BTN_A)     # keep right pressed while attacking
    time.sleep(0.06)

    def _snap(b, h):
        h.pause(); time.sleep(0.2)
        return dump(b, 'p2_in_hitstun')
    after_hit = cmd(_snap)

    p1.release()
    cmd(lambda b, h: h.run())
    time.sleep(0.5)

    byte_changes = diff_bytes(idle, after_hit)
    # Hitstun: look for bytes that went from 0 → non-zero near P2_HEALTH_ADDR region
    P2_HEALTH_RDRAM_OFFSET = 0x36E72E
    hitstun_region = [
        c for c in byte_changes
        if abs(c['vaddr'] - (RDRAM_BASE + P2_HEALTH_RDRAM_OFFSET)) < 0x80
        and c['before'] == 0 and c['after'] > 0
    ]

    print_byte_table(hitstun_region or byte_changes[:20],
                     'HITSTUN — bytes near P2 health that went 0→nonzero')

    return {'byte_changes': byte_changes, 'hitstun_candidates': hitstun_region}


def phase_crouch(p1: Ctrl, p2: Ctrl, idle: bytes) -> dict:
    """P1 crouches — crouch state byte should change."""
    print('\n[7] CROUCH test — P1 holds D_DOWN...')

    def _run(b, h): h.run()
    cmd(_run)
    time.sleep(0.3)

    p1.press(BTN_D_DOWN)
    time.sleep(0.15)

    def _snap(b, h):
        h.pause(); time.sleep(0.2)
        return dump(b, 'p1_crouching')
    crouching = cmd(_snap)

    p1.release()
    cmd(lambda b, h: h.run())
    time.sleep(0.4)

    byte_changes = diff_bytes(idle, crouching)
    print_byte_table(byte_changes, 'P1 CROUCH — byte changes (crouch/stance flag candidates)')

    return {'byte_changes': byte_changes}


def direct_read_known_neighbors() -> dict:
    """
    Read known addresses ± wide offsets to find struct neighbors.
    Prints raw values of all candidates near confirmed addresses.
    """
    print('\n[0] Direct read — struct neighbors around known addresses...')

    # Known confirmed addresses
    CANDIDATES = {
        # P1 struct (X confirmed at 0x800F87F8 hi-halfword)
        'P1_X_hi    (known)':  0x800F87F8,
        'P1_Y_+4':             0x800F87FC,
        'P1_Y_+8':             0x800F8800,
        'P1_Y_-4':             0x800F87F4,
        'P1_anim_+0x0C':       0x800F8804,
        'P1_anim_+0x10':       0x800F8808,
        'P1_state_+0x14':      0x800F880C,
        'P1_state_+0x18':      0x800F8810,
        'P1_facing_+0x18':     0x800F8810,
        'P1_facing_+0x1C':     0x800F8814,

        # P2 struct (X confirmed at 0x8006A060 hi-halfword)
        'P2_X_hi    (known)':  0x8006A060,
        'P2_Y_+4':             0x8006A064,
        'P2_Y_+8':             0x8006A068,
        'P2_Y_-4':             0x8006A05C,
        'P2_anim_+0x0C':       0x8006A06C,
        'P2_anim_+0x10':       0x8006A070,
        'P2_state_+0x14':      0x8006A074,
        'P2_state_+0x18':      0x8006A078,
        'P2_facing_+0x18':     0x8006A078,
        'P2_facing_+0x1C':     0x8006A07C,

        # Health region (0x8036E729 P1, 0x8036E72E P2)
        'P1_health  (known)':  0x8036E729,
        'P2_health  (known)':  0x8036E72E,
        'P1_hitstun_-3':       0x8036E726,
        'P1_hitstun_+3':       0x8036E72C,
        'P2_hitstun_+3':       0x8036E731,
        'P2_hitstun_+6':       0x8036E734,
        'state_block_-16':     0x8036E719,
        'anim_id_-32':         0x8036E709,
    }

    def _reads(b, h):
        from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
        h2 = Mk4BridgeHelper(b)
        print(f'\n  {"Name":<26}  {"Addr":<12}  {"u8":>5}  {"u32_hi":>8}  {"s16hi":>7}')
        print('  ' + '-' * 66)
        result = {}
        for name, addr in CANDIDATES.items():
            try:
                u8_v  = h2.read_u8(addr)
                u32_v = h2.read_u32(addr)
                hi    = (u32_v >> 16) & 0xFFFF
                s16   = hi if hi < 0x8000 else hi - 0x10000
                print(f'  {name:<26}  0x{addr:08X}  {u8_v:>5d}  {u32_v:>8d}  {s16:>+7d}')
                result[name] = {'addr': f'0x{addr:08X}', 'u8': u8_v, 'u32': u32_v, 's16hi': s16}
            except Exception as e:
                print(f'  {name:<26}  0x{addr:08X}  ERR: {e}')
        return result
    return cmd(_reads)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description='MK4 move-state RAM scanner')
    ap.add_argument('--phases', default='all',
                    help='Comma-separated phases to run: direct,neutral,jump,attack,p2attack,facing,hitstun,crouch  (default: all)')
    args = ap.parse_args()

    phases = args.phases.lower().split(',') if args.phases != 'all' else \
        ['direct', 'neutral', 'jump', 'attack', 'p2attack', 'facing', 'hitstun', 'crouch']

    DUMP_DIR.mkdir(parents=True, exist_ok=True)

    p1 = Ctrl(P1_CTRL)
    p2 = Ctrl(P2_CTRL)

    results: dict[str, Any] = {}

    try:
        if 'direct' in phases:
            results['direct'] = direct_read_known_neighbors()

        idle: bytes | None = None

        if set(phases) & {'neutral', 'jump', 'attack', 'p2attack', 'facing', 'hitstun', 'crouch'}:
            idle = phase_neutral_baseline(p1, p2)
            results['idle_dump'] = 'neutral_idle.bin'

        if idle and 'jump' in phases:
            results['jump'] = phase_p1_jump(p1, p2, idle)
            idle = phase_neutral_baseline(p1, p2)   # re-baseline after each phase

        if idle and 'attack' in phases:
            results['p1_attack'] = phase_p1_attack(p1, p2, idle)
            idle = phase_neutral_baseline(p1, p2)

        if idle and 'p2attack' in phases:
            results['p2_attack'] = phase_p2_attack(p1, p2, idle)
            idle = phase_neutral_baseline(p1, p2)

        if idle and 'facing' in phases:
            results['facing'] = phase_facing(p1, p2, idle)
            idle = phase_neutral_baseline(p1, p2)

        if idle and 'hitstun' in phases:
            results['hitstun'] = phase_hitstun(p1, p2, idle)
            idle = phase_neutral_baseline(p1, p2)

        if idle and 'crouch' in phases:
            results['crouch'] = phase_crouch(p1, p2, idle)

        # Strip non-serialisable bytes from results before saving
        def _clean(obj: Any) -> Any:
            if isinstance(obj, bytes):
                return f'<{len(obj)} bytes>'
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_clean(v) for v in obj]
            return obj

        RESULT_JSON.write_text(json.dumps(_clean(results), indent=2))
        print(f'\n✅ Done. Full results → {RESULT_JSON}')

        # ── Summary of best guesses ──────────────────────────────────────────
        print('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        print('NEXT STEPS:')
        print('  1. Run --phases direct  to read live values at known struct offsets')
        print('  2. Jump/attack during the run to see which values change')
        print('  3. Cross-reference with results.json candidates')
        print('  4. Add confirmed addresses to mk4_tracing.py and TracedState.extras')
        print('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

    finally:
        p1.close()
        p2.close()
        # Ensure emulator doesn't keep stale inputs
        time.sleep(0.1)


if __name__ == '__main__':
    main()
