#!/usr/bin/env python3
"""
verify_move_type_ground_truth.py

Deterministic move-type verifier for MK4 using isolated P1/P2 scripted inputs.

Goal:
  Find RAM offsets whose value is a stable and unique code for each attack type
  (LP/HP/LK/HK), for both P1 and P2, using controlled trials.

Method:
  - Load p1p2state.st fresh for every trial.
  - Move only one actor (P1 or P2) into range.
  - Run PRE/HOLD/POST frame windows for one action.
  - Collect actor struct words for an offset range.
  - Score each offset on move separability:
      * per-action mode purity during HOLD
      * distinct modes across 4 actions
      * hold-sample classification accuracy
      * baseline collision penalty (PRE matching action modes)

Outputs:
  - JSON full report
  - Markdown summary
  - CSV traces for top offsets (P1 and P2)
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import mmap
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = N64_ROOT / 'training/data/reverse/probe_runs'
LOG_DIR = N64_ROOT / 'training/data/logs'
SAVE_PATH = N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st'

ROM_PATH = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR = '/opt/homebrew/lib/mupen64plus'
DATA_DIR = '/opt/homebrew/share/mupen64plus'

# Struct bases
P1_BASE = 0x800FE000
P2_BASE = 0x80126E00
P1_ACTION_ST = 0x800FE08C
P2_ACTION_ST = 0x80126EC0

# Position addresses (upper s16 is x)
P1_X_ADDR = 0x800F87F8
P2_X_ADDR = 0x8006A060

BTN_A = 1 << 7
BTN_B = 1 << 6
BTN_C_RIGHT = 1 << 8
BTN_C_UP = 1 << 11
BTN_D_RIGHT = 1 << 0
BTN_D_LEFT = 1 << 1

ACTIONS = (
    ('lp', BTN_A),
    ('hp', BTN_B),
    ('lk', BTN_C_RIGHT),
    ('hk', BTN_C_UP),
)

ACTORS = ('p1', 'p2')
PHASES = ('pre', 'hold', 'post')


class BridgeSession:
    def __init__(self, tag: str, post_load_wait_sec: float = 5.0):
        self.tag = tag
        self.post_load_wait_sec = float(max(0.0, post_load_wait_sec))
        self.sock_path = Path(f'/tmp/mk4_movegt_{tag}.sock')
        self.ctrl_p1 = f'/tmp/mk4_ctrl_movegt_{tag}_p1'
        self.ctrl_p2 = f'/tmp/mk4_ctrl_movegt_{tag}_p2'
        self.cfg_dir = N64_ROOT / f'.m64p/instances/movegt_{tag}/config'
        self.dump_dir = OUT_DIR / f'movegt_{tag}_dumps'
        self.log_path = LOG_DIR / f'movegt_{tag}.log'
        self.proc: subprocess.Popen | None = None

    def _send(self, command: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(self.sock_path))
        req = {'id': 'movegt', 'command': command, 'payload': payload or {}}
        s.sendall((json.dumps(req) + '\n').encode())
        resp = json.loads(s.makefile('r').readline())
        s.close()
        if not resp.get('ok'):
            raise RuntimeError(f'{command} failed: {resp.get("error", {})}')
        return resp.get('payload', {})

    def dbg(self, cmd: str, timeout: float = 20.0) -> str:
        out = self._send(
            'DEBUGGER_COMMAND',
            {'command': cmd, 'timeout_sec': timeout},
            timeout=timeout + 5.0,
        )
        return str(out.get('output', ''))

    def _read_words(self, addr: int, count: int) -> list[int]:
        out = self.dbg(f'mem /{count}w 0x{addr:08x}', timeout=20.0)
        values: list[int] = []
        for line in out.splitlines():
            line = line.strip()
            if (not line) or line.startswith('(dbg)') or line.startswith('PC at') or line.startswith('mem '):
                continue
            toks = re.findall(r'[0-9A-Fa-f]{8}', line)
            for t in toks:
                values.append(int(t, 16) & 0xFFFFFFFF)
        if len(values) < count:
            raise ValueError(f'mem parse failed: expected {count}, got {len(values)} from {out!r}')
        return values[:count]

    def read_u32(self, addr: int) -> int:
        return self._read_words(addr, 1)[0]

    def read_s16hi(self, addr: int) -> int:
        w = self.read_u32(addr)
        hi = (w >> 16) & 0xFFFF
        return hi if hi < 0x8000 else hi - 0x10000

    def read_range(self, addr: int, words: int) -> list[int]:
        return self._read_words(addr, words)

    def step(self, frames: int = 1) -> None:
        n = max(1, int(frames))
        out = self.dbg(f'frame {n}', timeout=max(20.0, float(n) + 5.0))
        expected = f'M64P_FRAME_OK frames={n}'
        if expected not in out:
            raise RuntimeError(f'frame step failed: {out[-400:]}')

    def _write_ctrl_file(self, path: str, mask: int = 0) -> None:
        if not os.path.exists(path):
            with open(path, 'w+b') as f:
                f.write(b'\x00' * 4)
        with open(path, 'r+b') as f:
            m = mmap.mmap(f.fileno(), 4)
            m.seek(0)
            m.write(struct.pack('<Hbb', mask & 0xFFFF, 0, 0))
            m.flush()
            m.close()

    def write_ctrl(self, p1_mask: int = 0, p2_mask: int = 0) -> None:
        self._write_ctrl_file(self.ctrl_p1, p1_mask)
        self._write_ctrl_file(self.ctrl_p2, p2_mask)

    def start(self) -> None:
        if self.sock_path.exists():
            self.sock_path.unlink()
        import shutil
        if self.cfg_dir.exists():
            shutil.rmtree(str(self.cfg_dir))
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable, str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
            '--socket-path', str(self.sock_path),
            '--instance-id', f'movegt-{self.tag}',
            '--memory-reader', 'debugger-dump',
            '--rom-path', ROM_PATH,
            '--debugger-ui-binary', M64P_BIN,
            '--debugger-corelib', CORELIB,
            '--debugger-plugindir', PLUG_DIR,
            '--debugger-configdir', str(self.cfg_dir),
            '--debugger-datadir', DATA_DIR,
            '--debugger-dump-dir', str(self.dump_dir),
            '--debugger-gfx-plugin', 'mupen64plus-video-rice.dylib',
            '--debugger-audio-plugin', 'dummy',
            '--debugger-input-plugin', PLUGIN,
            '--debugger-rsp-plugin', 'mupen64plus-rsp-hle.dylib',
            '--debugger-emumode', '0',
            '--speed-mode', 'DEBUG_VISIBLE',
            '--log-path', str(self.log_path),
        ]
        env = os.environ.copy()
        env['N64TRAIN_CTRL_P1'] = self.ctrl_p1
        env['N64TRAIN_CTRL_P2'] = self.ctrl_p2
        lf = open(self.log_path, 'w')
        self.proc = subprocess.Popen(
            cmd,
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        lf.close()

        deadline = time.time() + 90.0
        while time.time() < deadline:
            if self.sock_path.exists():
                try:
                    _ = self._send('HELLO', timeout=2.0)
                    return
                except Exception:
                    pass
            time.sleep(0.5)
        raise RuntimeError('bridge did not become ready')

    def load_state(self, savestate: Path) -> None:
        self.dbg('pause', timeout=10.0)
        time.sleep(0.2)
        _ = self._send('LOAD_SAVESTATE', {'savestate_path': str(savestate)}, timeout=45.0)
        time.sleep(self.post_load_wait_sec)

    def stop(self) -> None:
        try:
            self.write_ctrl(0, 0)
        except Exception:
            pass
        try:
            self._send('TERMINATE', timeout=3.0)
        except Exception:
            pass
        if self.proc is not None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.sock_path.exists():
            try:
                self.sock_path.unlink()
            except Exception:
                pass


def detect_control_routes(bridge: BridgeSession, savestate: Path) -> dict[str, str]:
    routes: dict[str, str] = {}
    for channel in ('p1', 'p2'):
        # Probe both directions to avoid false "none" when one side is edge-blocked.
        d1 = 0
        d2 = 0
        for btn in (BTN_D_RIGHT, BTN_D_LEFT):
            bridge.load_state(savestate)
            bridge.step(120)
            x1_a = bridge.read_s16hi(P1_X_ADDR)
            x2_a = bridge.read_s16hi(P2_X_ADDR)
            if channel == 'p1':
                bridge.write_ctrl(btn, 0)
            else:
                bridge.write_ctrl(0, btn)
            bridge.step(40)
            bridge.write_ctrl(0, 0)
            bridge.step(10)
            x1_b = bridge.read_s16hi(P1_X_ADDR)
            x2_b = bridge.read_s16hi(P2_X_ADDR)
            d1 = max(d1, abs(x1_b - x1_a))
            d2 = max(d2, abs(x2_b - x2_a))
        if d1 == 0 and d2 == 0:
            # Fallback: detect routing by action-state perturbation.
            bridge.load_state(savestate)
            bridge.step(120)
            p1_prev = bridge.read_u32(P1_ACTION_ST)
            p2_prev = bridge.read_u32(P2_ACTION_ST)
            p1_changes = 0
            p2_changes = 0
            for _ in range(3):
                if channel == 'p1':
                    bridge.write_ctrl(BTN_A, 0)
                else:
                    bridge.write_ctrl(0, BTN_A)
                bridge.step(8)
                bridge.write_ctrl(0, 0)
                bridge.step(16)
                p1_now = bridge.read_u32(P1_ACTION_ST)
                p2_now = bridge.read_u32(P2_ACTION_ST)
                if p1_now != p1_prev:
                    p1_changes += 1
                if p2_now != p2_prev:
                    p2_changes += 1
                p1_prev = p1_now
                p2_prev = p2_now
            if p1_changes == 0 and p2_changes == 0:
                routes[channel] = 'none'
            elif p1_changes >= p2_changes:
                routes[channel] = 'p1'
            else:
                routes[channel] = 'p2'
        elif d1 >= d2:
            routes[channel] = 'p1'
        else:
            routes[channel] = 'p2'
    return routes


def channel_for_actor(routes: dict[str, str], actor: str) -> str | None:
    for channel in ('p1', 'p2'):
        if routes.get(channel) == actor:
            return channel
    return None


def move_actor_into_range(bridge: BridgeSession, actor: str, channel: str, walk_frames: int) -> None:
    # In p1p2state.st actors face each other, so P1 advances right and P2 advances left.
    if actor == 'p1':
        p1 = BTN_D_RIGHT if channel == 'p1' else 0
        p2 = BTN_D_RIGHT if channel == 'p2' else 0
    else:
        p1 = BTN_D_LEFT if channel == 'p1' else 0
        p2 = BTN_D_LEFT if channel == 'p2' else 0
    bridge.write_ctrl(p1, p2)
    bridge.step(max(1, int(walk_frames)))
    bridge.write_ctrl(0, 0)
    bridge.step(30)


def action_masks(channel: str, attack_btn: int) -> tuple[int, int]:
    if channel == 'p1':
        return attack_btn, 0
    return 0, attack_btn


def _mode_and_purity(values: list[int]) -> tuple[int, float]:
    if not values:
        return 0, 0.0
    c = Counter(values)
    mode, n = c.most_common(1)[0]
    return mode, float(n) / float(len(values))


def _classify_hold_value(v: int, action_modes: dict[str, int]) -> str | None:
    hits = [a for a, m in action_modes.items() if m == v]
    if len(hits) == 1:
        return hits[0]
    return None


def build_signature_samples(offsets: list[int], rows: list[dict]) -> tuple[dict[int, dict[str, list[int]]], dict[int, list[int]]]:
    # action -> rep -> phase -> list[frame_values]
    trial_frames: dict[str, dict[int, dict[str, list[list[int]]]]] = {
        a: defaultdict(lambda: {p: [] for p in PHASES}) for a, _ in ACTIONS
    }
    for row in rows:
        trial_frames[row['action']][int(row['rep'])][row['phase']].append(row['values'])

    sig_samples: dict[int, dict[str, list[int]]] = {off: {a: [] for a, _ in ACTIONS} for off in offsets}
    baseline_samples: dict[int, list[int]] = {off: [] for off in offsets}

    for off in offsets:
        idx = off // 4
        for action, _btn in ACTIONS:
            reps = sorted(trial_frames[action].keys())
            for rep in reps:
                phases = trial_frames[action][rep]
                pre_vals = [int(v[idx]) for v in phases['pre'] if len(v) > idx]
                hold_vals = [int(v[idx]) for v in phases['hold'] if len(v) > idx]
                post_vals = [int(v[idx]) for v in phases['post'] if len(v) > idx]
                base, _ = _mode_and_purity(pre_vals)
                baseline_samples[off].append(base)
                sig = base
                for v in hold_vals + post_vals:
                    if v != base:
                        sig = v
                        break
                sig_samples[off][action].append(sig)
    return sig_samples, baseline_samples


def score_offsets(
    actor: str,
    offsets: list[int],
    rows: list[dict],
) -> list[dict]:
    sig_samples, baseline_samples = build_signature_samples(offsets, rows)

    ranked: list[dict] = []
    for off in offsets:
        samples_by_action = sig_samples[off]
        baselines = baseline_samples[off]

        action_modes: dict[str, int] = {}
        action_purity: dict[str, float] = {}
        for action, _btn in ACTIONS:
            mode, purity = _mode_and_purity(samples_by_action[action])
            action_modes[action] = mode
            action_purity[action] = purity

        mode_set = set(action_modes.values())
        distinct_modes = len(mode_set)

        # Trial-level classification accuracy by exact mode match.
        total = 0
        correct = 0
        unknown = 0
        for action, _btn in ACTIONS:
            vals = samples_by_action[action]
            total += len(vals)
            for v in vals:
                pred = _classify_hold_value(v, action_modes)
                if pred is None:
                    unknown += 1
                elif pred == action:
                    correct += 1
        accuracy = (float(correct) / float(total)) if total else 0.0
        unknown_rate = (float(unknown) / float(total)) if total else 1.0

        # Baseline collision: trial baselines matching action modes.
        pre_match = 0
        if baselines:
            mode_values = set(action_modes.values())
            pre_match = sum(1 for v in baselines if v in mode_values)
        pre_match_rate = (float(pre_match) / float(len(baselines))) if baselines else 0.0

        avg_purity = sum(action_purity.values()) / 4.0
        min_purity = min(action_purity.values())

        # Score balance: separability + stability - baseline collisions.
        score = (
            1.2 * accuracy
            + 0.8 * min_purity
            + 0.5 * avg_purity
            + 0.4 * (float(distinct_modes) / 4.0)
            - 0.6 * pre_match_rate
            - 0.4 * unknown_rate
        )

        strong = (
            distinct_modes == 4
            and min_purity >= 0.75
            and accuracy >= 0.90
            and pre_match_rate <= 0.35
        )

        ranked.append({
            'actor': actor,
            'offset': f'0x{off:03X}',
            'address': f'0x{(P1_BASE if actor == "p1" else P2_BASE) + off:08X}',
            'score': round(score, 6),
            'strong': strong,
            'distinct_modes': distinct_modes,
            'accuracy': round(accuracy, 6),
            'unknown_rate': round(unknown_rate, 6),
            'avg_purity': round(avg_purity, 6),
            'min_purity': round(min_purity, 6),
            'pre_match_rate': round(pre_match_rate, 6),
            'action_modes': {k: f'0x{v:08X}' for k, v in action_modes.items()},
            'action_purity': {k: round(v, 6) for k, v in action_purity.items()},
        })
    ranked.sort(key=lambda x: float(x['score']), reverse=True)
    return ranked


def find_combo_ground_truth(
    actor: str,
    offsets: list[int],
    rows: list[dict],
    candidate_offsets: list[int],
    max_combo: int = 3,
) -> list[dict]:
    sig_samples, baseline_samples = build_signature_samples(offsets, rows)
    out: list[dict] = []

    for k in range(1, max_combo + 1):
        for combo in itertools.combinations(candidate_offsets, k):
            action_modes: dict[str, tuple[int, ...]] = {}
            action_purity: dict[str, float] = {}

            for action, _btn in ACTIONS:
                vals = list(zip(*[sig_samples[off][action] for off in combo]))
                if not vals:
                    action_modes[action] = tuple(0 for _ in combo)
                    action_purity[action] = 0.0
                    continue
                c = Counter(vals)
                mode, n = c.most_common(1)[0]
                action_modes[action] = tuple(int(x) for x in mode)
                action_purity[action] = float(n) / float(len(vals))

            distinct_modes = len(set(action_modes.values()))

            total = 0
            correct = 0
            unknown = 0
            for action, _btn in ACTIONS:
                vals = list(zip(*[sig_samples[off][action] for off in combo]))
                total += len(vals)
                for v in vals:
                    hits = [a for a, m in action_modes.items() if tuple(v) == m]
                    if len(hits) != 1:
                        unknown += 1
                    elif hits[0] == action:
                        correct += 1
            acc = (float(correct) / float(total)) if total else 0.0
            unknown_rate = (float(unknown) / float(total)) if total else 1.0

            baseline_tuples = list(zip(*[baseline_samples[off] for off in combo]))
            pre_match = 0
            if baseline_tuples:
                mode_set = set(action_modes.values())
                pre_match = sum(1 for t in baseline_tuples if tuple(t) in mode_set)
            pre_match_rate = (float(pre_match) / float(len(baseline_tuples))) if baseline_tuples else 0.0

            avg_purity = sum(action_purity.values()) / 4.0
            min_purity = min(action_purity.values())
            score = (
                1.3 * acc
                + 0.9 * min_purity
                + 0.5 * avg_purity
                + 0.5 * (float(distinct_modes) / 4.0)
                - 0.7 * pre_match_rate
                - 0.4 * unknown_rate
            )
            strong = (
                distinct_modes == 4
                and min_purity >= 0.75
                and acc >= 0.90
                and pre_match_rate <= 0.35
            )
            out.append({
                'actor': actor,
                'combo_offsets': [f'0x{off:03X}' for off in combo],
                'combo_addresses': [
                    f"0x{(P1_BASE if actor == 'p1' else P2_BASE) + off:08X}"
                    for off in combo
                ],
                'size': k,
                'score': round(score, 6),
                'strong': strong,
                'distinct_modes': distinct_modes,
                'accuracy': round(acc, 6),
                'unknown_rate': round(unknown_rate, 6),
                'avg_purity': round(avg_purity, 6),
                'min_purity': round(min_purity, 6),
                'pre_match_rate': round(pre_match_rate, 6),
                'action_modes': {
                    a: [f'0x{int(v):08X}' for v in mode]
                    for a, mode in action_modes.items()
                },
                'action_purity': {a: round(v, 6) for a, v in action_purity.items()},
            })

    out.sort(key=lambda x: float(x['score']), reverse=True)
    return out


def write_trace_csv(path: Path, offsets: list[int], rows: list[dict], actor: str) -> None:
    cols = ['actor', 'action', 'rep', 'phase', 'frame_in_phase']
    cols += [f'0x{off:03X}' for off in offsets]
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            d = {
                'actor': actor,
                'action': row['action'],
                'rep': row['rep'],
                'phase': row['phase'],
                'frame_in_phase': row['frame_in_phase'],
            }
            for off in offsets:
                idx = off // 4
                d[f'0x{off:03X}'] = int(row['values'][idx]) if len(row['values']) > idx else 0
            w.writerow(d)


def run_verification(
    savestate: Path,
    start_off: int,
    end_off: int,
    repeats: int,
    pre_frames: int,
    hold_frames: int,
    post_frames: int,
    walk_frames: int,
    post_load_wait_sec: float,
) -> dict:
    words = ((end_off - start_off) // 4) + 1
    if words <= 0:
        raise ValueError('Invalid offset range')
    offsets = [start_off + i * 4 for i in range(words)]

    tag = time.strftime('%Y%m%d_%H%M%S')
    bridge = BridgeSession(tag=tag, post_load_wait_sec=post_load_wait_sec)
    bridge.start()
    try:
        routes = detect_control_routes(bridge, savestate)
        channels = {actor: channel_for_actor(routes, actor) for actor in ACTORS}
        if channels['p1'] is None or channels['p2'] is None:
            raise RuntimeError(f'Could not map both actors to control channels: {routes}')

        actor_rows: dict[str, list[dict]] = {a: [] for a in ACTORS}
        for actor in ACTORS:
            channel = channels[actor]
            assert channel is not None
            base = P1_BASE if actor == 'p1' else P2_BASE
            for action, btn in ACTIONS:
                for rep in range(repeats):
                    bridge.load_state(savestate)
                    bridge.step(120)
                    move_actor_into_range(bridge, actor, channel, walk_frames)

                    # PRE
                    bridge.write_ctrl(0, 0)
                    for fi in range(pre_frames):
                        bridge.step(1)
                        vals = bridge.read_range(base + start_off, words)
                        actor_rows[actor].append({
                            'action': action,
                            'rep': rep,
                            'phase': 'pre',
                            'frame_in_phase': fi,
                            'values': vals,
                        })

                    # HOLD
                    p1m, p2m = action_masks(channel, btn)
                    bridge.write_ctrl(p1m, p2m)
                    for fi in range(hold_frames):
                        bridge.step(1)
                        vals = bridge.read_range(base + start_off, words)
                        actor_rows[actor].append({
                            'action': action,
                            'rep': rep,
                            'phase': 'hold',
                            'frame_in_phase': fi,
                            'values': vals,
                        })

                    # POST
                    bridge.write_ctrl(0, 0)
                    for fi in range(post_frames):
                        bridge.step(1)
                        vals = bridge.read_range(base + start_off, words)
                        actor_rows[actor].append({
                            'action': action,
                            'rep': rep,
                            'phase': 'post',
                            'frame_in_phase': fi,
                            'values': vals,
                        })

        p1_ranked = score_offsets('p1', offsets, actor_rows['p1'])
        p2_ranked = score_offsets('p2', offsets, actor_rows['p2'])
        p1_combo = find_combo_ground_truth(
            'p1',
            offsets,
            actor_rows['p1'],
            candidate_offsets=[int(x['offset'], 16) for x in p1_ranked[:14]],
            max_combo=3,
        )
        p2_combo = find_combo_ground_truth(
            'p2',
            offsets,
            actor_rows['p2'],
            candidate_offsets=[int(x['offset'], 16) for x in p2_ranked[:14]],
            max_combo=3,
        )

        # Export trace CSV for top offsets so manual frame-level verification is easy.
        p1_top = [int(x['offset'], 16) for x in p1_ranked[:8]]
        p2_top = [int(x['offset'], 16) for x in p2_ranked[:8]]
        p1_csv = OUT_DIR / f'move_type_gt_{tag}_p1_trace.csv'
        p2_csv = OUT_DIR / f'move_type_gt_{tag}_p2_trace.csv'
        write_trace_csv(p1_csv, p1_top, actor_rows['p1'], 'p1')
        write_trace_csv(p2_csv, p2_top, actor_rows['p2'], 'p2')

        return {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'tag': tag,
            'savestate': str(savestate),
            'scan_offsets': {
                'start': f'0x{start_off:03X}',
                'end': f'0x{end_off:03X}',
                'words': words,
            },
            'config': {
                'repeats': repeats,
                'pre_frames': pre_frames,
                'hold_frames': hold_frames,
                'post_frames': post_frames,
                'walk_frames': walk_frames,
                'post_load_wait_sec': post_load_wait_sec,
            },
            'routes': routes,
            'channels': channels,
            'actions': [a for a, _ in ACTIONS],
            'actors': {
                'p1': {
                    'top': p1_ranked[:40],
                    'strong': [x for x in p1_ranked if x['strong']][:20],
                    'combo_top': p1_combo[:30],
                    'combo_strong': [x for x in p1_combo if x['strong']][:20],
                    'trace_csv': str(p1_csv),
                },
                'p2': {
                    'top': p2_ranked[:40],
                    'strong': [x for x in p2_ranked if x['strong']][:20],
                    'combo_top': p2_combo[:30],
                    'combo_strong': [x for x in p2_combo if x['strong']][:20],
                    'trace_csv': str(p2_csv),
                },
            },
        }
    finally:
        bridge.stop()


def write_summary_md(data: dict, path: Path) -> None:
    lines: list[str] = []
    lines.append('# Move-Type Ground Truth Verification')
    lines.append('')
    lines.append(f"- Generated: {data.get('generated_at')}")
    lines.append(f"- Savestate: `{data.get('savestate')}`")
    lines.append(f"- Offsets: `{data.get('scan_offsets')}`")
    lines.append(f"- Config: `{data.get('config')}`")
    lines.append(f"- Routes: `{data.get('routes')}`")
    lines.append(f"- Channels: `{data.get('channels')}`")
    lines.append('')
    for actor in ACTORS:
        block = data.get('actors', {}).get(actor, {})
        lines.append(f'## {actor.upper()} Top Candidates')
        lines.append(f"- Trace CSV: `{block.get('trace_csv')}`")
        strong = block.get('strong', [])
        combo_strong = block.get('combo_strong', [])
        lines.append(f"- Strong candidates: {len(strong)}")
        lines.append(f"- Strong combos: {len(combo_strong)}")
        rows = block.get('top', [])[:12]
        for r in rows:
            lines.append(
                f"- `{r['offset']}` `{r['address']}` score={r['score']} strong={r['strong']} "
                f"distinct={r['distinct_modes']} acc={r['accuracy']} min_purity={r['min_purity']} "
                f"pre_match={r['pre_match_rate']} modes={r['action_modes']}"
            )
        lines.append('')
        lines.append(f"### {actor.upper()} Best Combos")
        for r in block.get('combo_top', [])[:8]:
            lines.append(
                f"- offs={r['combo_offsets']} addrs={r['combo_addresses']} score={r['score']} "
                f"strong={r['strong']} distinct={r['distinct_modes']} acc={r['accuracy']} "
                f"min_purity={r['min_purity']} pre_match={r['pre_match_rate']} modes={r['action_modes']}"
            )
        lines.append('')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser(description='Verify exact LP/HP/LK/HK move-type RAM codes for P1 and P2')
    ap.add_argument('--savestate', default=str(SAVE_PATH), help='Savestate path (default: p1p2state.st)')
    ap.add_argument('--start-off', type=lambda x: int(x, 16), default=0x000,
                    help='Start struct offset (hex, default 0x000)')
    ap.add_argument('--end-off', type=lambda x: int(x, 16), default=0x1FC,
                    help='End struct offset inclusive (hex, default 0x1FC)')
    ap.add_argument('--repeats', type=int, default=4, help='Repeats per action per actor')
    ap.add_argument('--pre-frames', type=int, default=8, help='Frames before button press')
    ap.add_argument('--hold-frames', type=int, default=10, help='Frames holding button')
    ap.add_argument('--post-frames', type=int, default=16, help='Frames after release')
    ap.add_argument('--walk-frames', type=int, default=220, help='Frames to walk attacker into range')
    ap.add_argument('--post-load-wait-sec', type=float, default=5.0,
                    help='Seconds to wait after loading savestate before running flow (default: 5.0)')
    args = ap.parse_args()

    savestate = Path(args.savestate)
    if not savestate.exists():
        raise SystemExit(f'Savestate not found: {savestate}')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out_json = OUT_DIR / f'move_type_gt_{stamp}.json'
    out_md = OUT_DIR / f'move_type_gt_{stamp}.md'

    data = run_verification(
        savestate=savestate,
        start_off=args.start_off,
        end_off=args.end_off,
        repeats=max(1, int(args.repeats)),
        pre_frames=max(2, int(args.pre_frames)),
        hold_frames=max(2, int(args.hold_frames)),
        post_frames=max(2, int(args.post_frames)),
        walk_frames=max(40, int(args.walk_frames)),
        post_load_wait_sec=max(0.0, float(args.post_load_wait_sec)),
    )
    out_json.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    write_summary_md(data, out_md)

    print(f'JSON: {out_json}')
    print(f'MD:   {out_md}')
    print('Top P1:')
    for row in data.get('actors', {}).get('p1', {}).get('top', [])[:8]:
        print(
            f"  {row['offset']} {row['address']} score={row['score']} "
            f"strong={row['strong']} acc={row['accuracy']} modes={row['action_modes']}"
        )
    print('Top P2:')
    for row in data.get('actors', {}).get('p2', {}).get('top', [])[:8]:
        print(
            f"  {row['offset']} {row['address']} score={row['score']} "
            f"strong={row['strong']} acc={row['accuracy']} modes={row['action_modes']}"
        )
    p1_best_combo = (data.get('actors', {}).get('p1', {}).get('combo_top') or [None])[0]
    p2_best_combo = (data.get('actors', {}).get('p2', {}).get('combo_top') or [None])[0]
    if p1_best_combo:
        print(
            f"Best P1 combo: offs={p1_best_combo['combo_offsets']} "
            f"score={p1_best_combo['score']} strong={p1_best_combo['strong']}"
        )
    if p2_best_combo:
        print(
            f"Best P2 combo: offs={p2_best_combo['combo_offsets']} "
            f"score={p2_best_combo['score']} strong={p2_best_combo['strong']}"
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
