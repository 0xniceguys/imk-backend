#!/usr/bin/env python3
"""
gameshark_rich_state_scan.py — Rank rich P2 combat-state candidates from GameShark offsets.

Purpose:
  Scan P2 struct offsets while the MK4 CPU attacks in arcade mode, then rank
  offsets whose value changes align with real P1 damage events.

Why:
  We need richer, trustworthy observation features for training. This script
  finds high-signal P2 state addresses using event correlation instead of guesswork.

Default scan:
  - Savestate: arcade_training_scorpion.st
  - P2 base:   0x80126E00
  - Offset range: 0x000..0x1FC (128 words)
  - Scenarios: neutral, stand_block, crouch_block
  - Frames per scenario: 1200

Output:
  - JSON with per-scenario metrics and ranked offsets
  - Markdown summary with top candidates
"""
from __future__ import annotations

import argparse
import csv
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
from dataclasses import dataclass
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
OUT_DIR = N64_ROOT / 'training/data/reverse/probe_runs'
LOG_DIR = N64_ROOT / 'training/data/logs'

ROM_PATH = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR = '/opt/homebrew/lib/mupen64plus'
DATA_DIR = '/opt/homebrew/share/mupen64plus'
SAVE_PATH = N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st'

# GameShark struct bases
P1_BASE = 0x800FE000
P2_BASE = 0x80126E00

# Health words (verified)
P1_HEALTH = 0x800FE0D8
P2_HEALTH = 0x80126F54

BTN_D_RIGHT = 1 << 0
BTN_D_DOWN = 1 << 2
BTN_C_LEFT = 1 << 9


@dataclass
class ScenarioConfig:
    name: str
    p1_mask: int


SCENARIOS: list[ScenarioConfig] = [
    ScenarioConfig('neutral', 0),
    ScenarioConfig('stand_block', BTN_C_LEFT),
    ScenarioConfig('crouch_block', BTN_C_LEFT | BTN_D_DOWN),
]


class BridgeSession:
    def __init__(self, tag: str):
        self.tag = tag
        self.sock_path = Path(f'/tmp/mk4_gs_scan_{tag}.sock')
        self.ctrl_path = f'/tmp/mk4_ctrl_gs_scan_{tag}'
        self.cfg_dir = N64_ROOT / f'.m64p/instances/gs_scan_{tag}/config'
        self.dump_dir = OUT_DIR / f'gs_scan_{tag}_dumps'
        self.log_path = LOG_DIR / f'gs_scan_{tag}.log'
        self.proc: subprocess.Popen | None = None

    def _send(self, command: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(self.sock_path))
        req = {'id': 'gs-scan', 'command': command, 'payload': payload or {}}
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

    def read_range(self, addr: int, words: int) -> list[int]:
        return self._read_words(addr, words)

    def step(self, frames: int = 1) -> None:
        out = self.dbg(f'frame {max(1, int(frames))}', timeout=max(20.0, float(frames) + 5.0))
        expected = f'M64P_FRAME_OK frames={max(1, int(frames))}'
        if expected not in out:
            raise RuntimeError(f'frame step failed: {out[-400:]}')

    def write_ctrl(self, mask: int = 0) -> None:
        if not os.path.exists(self.ctrl_path):
            with open(self.ctrl_path, 'w+b') as f:
                f.write(b'\x00' * 4)
        with open(self.ctrl_path, 'r+b') as f:
            m = mmap.mmap(f.fileno(), 4)
            m.seek(0)
            m.write(struct.pack('<Hbb', mask & 0xFFFF, 0, 0))
            m.flush()
            m.close()

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
            '--instance-id', f'gs-scan-{self.tag}',
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
        env['N64TRAIN_CTRL_P1'] = self.ctrl_path
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

    def stop(self) -> None:
        try:
            self.write_ctrl(0)
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


def _is_nonzero(v: int) -> bool:
    return v != 0


def _calc_offset_metrics(values: list[int], event_frames: list[int]) -> dict[str, float | int]:
    n = len(values)
    if n < 3:
        return {
            'unique': len(set(values)),
            'delta_rate': 0.0,
            'nonzero_rate': 0.0,
            'event_change_rate': 0.0,
            'event_nonzero_rate': 0.0,
            'bg_change_rate': 0.0,
            'bg_nonzero_rate': 0.0,
            'score': 0.0,
        }

    changed = [False] * n
    for i in range(1, n):
        changed[i] = (values[i] != values[i - 1])

    near_event = [False] * n
    for e in event_frames:
        for j in (e - 1, e, e + 1):
            if 0 <= j < n:
                near_event[j] = True

    event_idx = [i for i in range(n) if near_event[i]]
    bg_idx = [i for i in range(n) if not near_event[i]]

    def _rate(idxs: list[int], pred) -> float:
        if not idxs:
            return 0.0
        hit = sum(1 for i in idxs if pred(i))
        return float(hit) / float(len(idxs))

    event_change = _rate(event_idx, lambda i: changed[i])
    event_nonzero = _rate(event_idx, lambda i: _is_nonzero(values[i]))
    bg_change = _rate(bg_idx, lambda i: changed[i])
    bg_nonzero = _rate(bg_idx, lambda i: _is_nonzero(values[i]))

    delta_rate = _rate(list(range(1, n)), lambda i: changed[i])
    nonzero_rate = _rate(list(range(n)), lambda i: _is_nonzero(values[i]))
    unique = len(set(values))

    # Event alignment score; positive means more event-correlated than background.
    score = (
        (event_change - bg_change)
        + 0.5 * (event_nonzero - bg_nonzero)
        + 0.002 * float(min(unique, 100))
    )
    return {
        'unique': unique,
        'delta_rate': round(delta_rate, 6),
        'nonzero_rate': round(nonzero_rate, 6),
        'event_change_rate': round(event_change, 6),
        'event_nonzero_rate': round(event_nonzero, 6),
        'bg_change_rate': round(bg_change, 6),
        'bg_nonzero_rate': round(bg_nonzero, 6),
        'score': round(score, 6),
    }


def _parse_trace_offsets(raw: str) -> list[int]:
    out: list[int] = []
    for tok in (raw or '').split(','):
        tok = tok.strip()
        if not tok:
            continue
        out.append(int(tok, 16))
    return out


def _write_trace_csv(
    out_dir: Path,
    stamp: str,
    scenario: str,
    trace_rows: list[dict[str, int]],
    trace_cols: list[str],
) -> str:
    path = out_dir / f'gameshark_rich_scan_{stamp}_{scenario}_trace.csv'
    cols = ['frame', 'p1_health', 'p2_health', 'p1_damage_event'] + trace_cols
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in trace_rows:
            w.writerow({k: row.get(k, 0) for k in cols})
    return str(path)


def run_scan(
    frames: int,
    start_off: int,
    end_off: int,
    walk_frames: int,
    trace_offsets: list[int] | None = None,
) -> dict:
    words = ((end_off - start_off) // 4) + 1
    if words <= 0:
        raise ValueError('Invalid offset range')
    offsets = [start_off + i * 4 for i in range(words)]
    addrs = [P2_BASE + off for off in offsets]
    offset_to_idx = {off: i for i, off in enumerate(offsets)}
    trace_offsets = trace_offsets or []
    trace_offsets = [off for off in trace_offsets if off in offset_to_idx]

    tag = time.strftime('%Y%m%d_%H%M%S')
    bridge = BridgeSession(tag=tag)
    bridge.start()
    try:
        per_scenario: dict[str, dict] = {}
        aggregate_values: dict[int, list[int]] = {off: [] for off in offsets}
        aggregate_events: dict[int, list[int]] = {off: [] for off in offsets}
        # For aggregate scoring we concatenate scenario series and shift indices.
        concat_values: dict[int, list[int]] = {off: [] for off in offsets}
        concat_event_idx: dict[int, list[int]] = {off: [] for off in offsets}
        trace_csv_by_scenario: dict[str, str] = {}

        for sc in SCENARIOS:
            bridge.dbg('pause')
            time.sleep(0.2)
            _ = bridge._send('LOAD_SAVESTATE', {'savestate_path': str(SAVE_PATH)}, timeout=45.0)
            time.sleep(0.5)

            bridge.step(120)
            bridge.write_ctrl(BTN_D_RIGHT)
            bridge.step(walk_frames)
            bridge.write_ctrl(0)
            bridge.step(30)
            if sc.p1_mask:
                bridge.write_ctrl(sc.p1_mask)

            p1_prev = bridge.read_u32(P1_HEALTH)
            p2_prev = bridge.read_u32(P2_HEALTH)
            values_by_off: dict[int, list[int]] = {off: [] for off in offsets}
            event_frames: list[int] = []
            p1_damage_total = 0
            p2_damage_total = 0
            trace_rows: list[dict[str, int]] = []
            trace_cols = [f'0x{off:03X}' for off in trace_offsets]

            for fi in range(frames):
                bridge.step(1)
                vals = bridge.read_range(addrs[0], words)
                p1_now = bridge.read_u32(P1_HEALTH)
                p2_now = bridge.read_u32(P2_HEALTH)

                for idx, off in enumerate(offsets):
                    values_by_off[off].append(vals[idx])

                p1_damage_event = 1 if p1_now < p1_prev else 0
                if trace_offsets:
                    row = {
                        'frame': fi,
                        'p1_health': int(p1_now),
                        'p2_health': int(p2_now),
                        'p1_damage_event': p1_damage_event,
                    }
                    for off in trace_offsets:
                        row[f'0x{off:03X}'] = int(vals[offset_to_idx[off]])
                    trace_rows.append(row)

                if p1_now < p1_prev:
                    event_frames.append(fi)
                    p1_damage_total += int(p1_prev - p1_now)
                if p2_now < p2_prev:
                    p2_damage_total += int(p2_prev - p2_now)
                p1_prev = p1_now
                p2_prev = p2_now

            if sc.p1_mask:
                bridge.write_ctrl(0)

            metrics = {}
            for off in offsets:
                m = _calc_offset_metrics(values_by_off[off], event_frames)
                metrics[f'0x{off:03X}'] = m

                base = len(concat_values[off])
                concat_values[off].extend(values_by_off[off])
                concat_event_idx[off].extend([base + e for e in event_frames])

            if trace_offsets:
                trace_csv_by_scenario[sc.name] = _write_trace_csv(
                    out_dir=OUT_DIR,
                    stamp=tag,
                    scenario=sc.name,
                    trace_rows=trace_rows,
                    trace_cols=trace_cols,
                )

            per_scenario[sc.name] = {
                'frames': frames,
                'p1_damage_events': len(event_frames),
                'p1_damage_total_raw': p1_damage_total,
                'p2_damage_total_raw': p2_damage_total,
                'event_frames_sample': event_frames[:20],
                'metrics': metrics,
            }

        aggregate_rank = []
        for off in offsets:
            agg = _calc_offset_metrics(concat_values[off], concat_event_idx[off])
            aggregate_rank.append({
                'offset': f'0x{off:03X}',
                'address': f'0x{(P2_BASE + off):08X}',
                **agg,
            })
        aggregate_rank.sort(key=lambda x: float(x['score']), reverse=True)

        anchor_offsets = [0x0C0, 0x094, 0x19C, 0x130]
        anchors = {}
        for off in anchor_offsets:
            if start_off <= off <= end_off:
                anchors[f'0x{off:03X}'] = next(
                    (r for r in aggregate_rank if r['offset'] == f'0x{off:03X}'),
                    None,
                )

        return {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'savestate': str(SAVE_PATH),
            'p2_base': f'0x{P2_BASE:08X}',
            'scan_offsets': {'start': f'0x{start_off:03X}', 'end': f'0x{end_off:03X}', 'words': words},
            'frames_per_scenario': frames,
            'walk_frames': walk_frames,
            'scenarios': [sc.name for sc in SCENARIOS],
            'results_by_scenario': per_scenario,
            'aggregate_top': aggregate_rank[:40],
            'anchors': anchors,
            'trace_offsets': [f'0x{off:03X}' for off in trace_offsets],
            'trace_csv_by_scenario': trace_csv_by_scenario,
        }
    finally:
        bridge.stop()


def write_summary_md(data: dict, path: Path) -> None:
    top = data.get('aggregate_top', [])
    anchors = data.get('anchors', {})
    lines = []
    lines.append('# GameShark Rich-State Scan')
    lines.append('')
    lines.append(f"- Generated: {data.get('generated_at')}")
    lines.append(f"- Savestate: `{data.get('savestate')}`")
    lines.append(f"- P2 base: `{data.get('p2_base')}`")
    lines.append(f"- Scan offsets: `{data.get('scan_offsets')}`")
    lines.append(f"- Frames/scenario: `{data.get('frames_per_scenario')}`")
    lines.append(f"- Scenarios: `{', '.join(data.get('scenarios', []))}`")
    lines.append('')
    lines.append('## Anchor Offsets')
    for off in ('0x0C0', '0x094', '0x19C', '0x130'):
        a = anchors.get(off)
        if not a:
            lines.append(f"- `{off}`: not in scan")
            continue
        lines.append(
            f"- `{off}` `{a['address']}` score={a['score']} "
            f"event_change={a['event_change_rate']} bg_change={a['bg_change_rate']} "
            f"unique={a['unique']}"
        )
    lines.append('')
    lines.append('## Top Candidates')
    for row in top[:20]:
        lines.append(
            f"- `{row['offset']}` `{row['address']}` score={row['score']} "
            f"event_change={row['event_change_rate']} bg_change={row['bg_change_rate']} "
            f"event_nonzero={row['event_nonzero_rate']} unique={row['unique']}"
        )
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser(description='Scan GameShark offsets for rich P2 combat-state signals')
    ap.add_argument('--frames', type=int, default=1200, help='Frames per scenario')
    ap.add_argument('--walk-frames', type=int, default=160, help='Pre-fight walk-in frames')
    ap.add_argument('--start-off', type=lambda x: int(x, 16), default=0x000,
                    help='Start P2 offset (hex, default 0x000)')
    ap.add_argument('--end-off', type=lambda x: int(x, 16), default=0x1FC,
                    help='End P2 offset inclusive (hex, default 0x1FC)')
    ap.add_argument(
        '--trace-offs',
        default='',
        help='Comma-separated hex offsets to export as per-frame CSV traces (example: 0x080,0x074,0x094)',
    )
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out_json = OUT_DIR / f'gameshark_rich_scan_{stamp}.json'
    out_md = OUT_DIR / f'gameshark_rich_scan_{stamp}.md'

    data = run_scan(
        frames=max(200, int(args.frames)),
        start_off=args.start_off,
        end_off=args.end_off,
        walk_frames=max(60, int(args.walk_frames)),
        trace_offsets=_parse_trace_offsets(args.trace_offs),
    )
    out_json.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    write_summary_md(data, out_md)

    print(f'JSON: {out_json}')
    print(f'MD:   {out_md}')
    trace_files = data.get('trace_csv_by_scenario', {})
    if trace_files:
        print('Trace CSVs:')
        for sc, p in trace_files.items():
            print(f'  {sc}: {p}')
    print('Top 10 offsets:')
    for row in data.get('aggregate_top', [])[:10]:
        print(
            f"  {row['offset']} {row['address']} "
            f"score={row['score']} event_change={row['event_change_rate']} "
            f"bg_change={row['bg_change_rate']} unique={row['unique']}"
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
