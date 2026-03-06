#!/usr/bin/env python3
"""
smoke_test_pipeline.py — End-to-end training pipeline smoke test.

Launches ONE emulator, connects via bridge, and validates every stage of the
training loop: stateload → frame stepping → health reads → attack → reward.

Usage:
    python3 training/scripts/smoke_test_pipeline.py

Exits 0 if all checks pass, 1 on any failure.
"""
from __future__ import annotations

import mmap
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training' / 'src'))
sys.path.insert(0, str(N64_ROOT / 'training' / 'scripts'))

SOCK_PATH    = str(N64_ROOT / 'training/data/bridge/mk4-smoke-test.sock')
CTRL_PATH    = '/tmp/mk4_ctrl_smoke_test'
CTRL_PATH_P2 = '/tmp/mk4_ctrl_smoke_test_p2'
ROM_PATH     = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN     = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB      = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN       = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR     = '/opt/homebrew/lib/mupen64plus'
CFG_DIR      = str(N64_ROOT / '.m64p/instances/smoke-test/config')
DATA_DIR     = '/opt/homebrew/share/mupen64plus'
DUMP_DIR     = str(N64_ROOT / 'training/data/bridge/debugger_dumps/smoke_test')

PASS = '\033[92mPASS\033[0m'
FAIL = '\033[91mFAIL\033[0m'
WARN = '\033[93mWARN\033[0m'

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    results.append((name, ok, detail))
    status = PASS if ok else FAIL
    msg = f'  [{status}] {name}'
    if detail:
        msg += f'  ({detail})'
    print(msg)
    return ok


def resolve_savestate() -> Path:
    # p1p2state.st — 2-player mode, both controllers intercepted
    candidates = [
        N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/my_state.st',
    ]
    for p in candidates:
        if p.exists():
            return p
    return Path('')


def write_ctrl(pressed_mask: int = 0, x: int = 0, y: int = 0, path: str = CTRL_PATH) -> None:
    if not os.path.exists(path):
        with open(path, 'w+b') as f:
            f.write(b'\x00' * 4)
    with open(path, 'r+b') as f:
        m = mmap.mmap(f.fileno(), 4)
        m.seek(0)
        m.write(struct.pack('<Hbb', pressed_mask & 0xFFFF, x & 0xFF, y & 0xFF))
        m.flush()
        m.close()


def step_frames(bridge, n: int) -> bool:
    timeout = max(10.0, float(n) * 2.0)
    result = bridge.debugger_command(f'frame {n}', timeout_sec=timeout, output_tail_chars=2000)
    output = str(result.get('output', ''))
    return f'M64P_FRAME_OK frames={n}' in output


def main() -> int:
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    from n64train.reverse.mk4_tracing import Mk4FightTraceProvider, HEALTH_MAX, HEALTH_RAW_MAX
    from n64train.runtime.rewards import Mk4ShapedRewardExtractor
    from mk4_train import RAW_OBS_DIM, build_obs, _BTN, STEP_SECS, SETTLE_SECS
    from n64train.runtime.actions import Button

    print('=' * 70)
    print('  MK4 TRAINING PIPELINE SMOKE TEST')
    print('=' * 70)
    print()

    # ── 1. Pre-flight: check files exist ──────────────────────────────────────
    print('[1/8] Pre-flight checks')
    save_path = resolve_savestate()
    check('ROM exists', Path(ROM_PATH).exists(), ROM_PATH)
    check('Savestate exists', save_path.exists(), str(save_path.name) if save_path.exists() else 'NONE FOUND')
    check('Mupen binary exists', Path(M64P_BIN).exists())
    check('Core library exists', Path(CORELIB).exists())
    check('Input plugin exists', Path(PLUGIN).exists())

    if not save_path.exists() or not Path(ROM_PATH).exists():
        print('\nFATAL: missing ROM or savestate. Cannot continue.')
        return 1

    # ── 2. Launch emulator ────────────────────────────────────────────────────
    print(f'\n[2/8] Launching emulator (this takes ~30-60s)...')
    try:
        os.remove(SOCK_PATH)
    except OSError:
        pass

    import shutil
    cfg = Path(CFG_DIR)
    if cfg.exists():
        shutil.rmtree(str(cfg))
    cfg.mkdir(parents=True, exist_ok=True)

    cmd = [
        '/opt/homebrew/bin/python3', str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path', SOCK_PATH,
        '--instance-id', 'smoke-test',
        '--memory-reader', 'debugger-dump',
        '--rom-path', ROM_PATH,
        '--debugger-ui-binary', M64P_BIN,
        '--debugger-corelib', CORELIB,
        '--debugger-plugindir', PLUG_DIR,
        '--debugger-configdir', CFG_DIR,
        '--debugger-datadir', DATA_DIR,
        '--debugger-dump-dir', DUMP_DIR,
        '--debugger-gfx-plugin', 'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin', 'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode', '0',
    ]
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = CTRL_PATH
    env['N64TRAIN_CTRL_P2'] = CTRL_PATH_P2

    log_file = open(N64_ROOT / 'training/data/logs/smoke_test_emulator.log', 'w')
    proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
    print(f'  Emulator pid={proc.pid}')

    # Wait for socket
    sock_ok = False
    for i in range(120):
        if Path(SOCK_PATH).exists():
            sock_ok = True
            break
        if proc.poll() is not None:
            break
        time.sleep(1)
        if i % 10 == 9:
            print(f'  ... waiting for socket ({i+1}s)')

    check('Emulator socket appeared', sock_ok, f'{i+1}s' if sock_ok else 'timeout')
    if not sock_ok:
        proc.kill()
        log_file.close()
        return 1

    # ── 3. Bridge connection ──────────────────────────────────────────────────
    print(f'\n[3/8] Bridge connection')
    b = None
    h = None
    tracer = None
    try:
        b = SocketEmulatorBridge(SOCK_PATH, timeout_sec=120)
        b.connect()
        resp = b.hello()
        check('Bridge HELLO', True, f'status={resp.status.status}')
    except Exception as e:
        check('Bridge HELLO', False, str(e))
        proc.kill()
        log_file.close()
        return 1

    h = Mk4BridgeHelper(b)
    tracer = Mk4FightTraceProvider(helper=h)

    # ── 4. Stateload ─────────────────────────────────────────────────────────
    print(f'\n[4/8] Savestate load ({save_path.name})')
    try:
        h.pause()
    except Exception:
        pass
    time.sleep(0.3)

    stateload_ok = False
    for attempt in range(3):
        try:
            b.load_savestate_path(save_path)
            stateload_ok = True
            break
        except Exception as e:
            if attempt == 2:
                check('Savestate load', False, str(e))
            time.sleep(2)

    check('Savestate load', stateload_ok)
    if not stateload_ok:
        proc.kill()
        log_file.close()
        return 1

    # ── 5. Deterministic frame stepping ──────────────────────────────────────
    print(f'\n[5/8] Deterministic frame stepping')
    write_ctrl(0)  # neutral

    settle_frames = max(1, int(round(SETTLE_SECS * 60.0)))
    try:
        ok = step_frames(b, settle_frames)
        check(f'frame {settle_frames} (settle)', ok)
    except Exception as e:
        check(f'frame {settle_frames} (settle)', False, str(e))
        proc.kill()
        log_file.close()
        return 1

    action_frames = max(1, int(round(STEP_SECS * 60.0)))
    try:
        ok = step_frames(b, action_frames)
        check(f'frame {action_frames} (action step)', ok)
    except Exception as e:
        check(f'frame {action_frames} (action step)', False, str(e))

    # ── 6. Health reads ──────────────────────────────────────────────────────
    print(f'\n[6/8] RAM health reads (u32 at 0x800FE0D8 / 0x80126F54)')
    try:
        state = tracer.read(0)
        p1 = state.p1_health
        p2 = state.p2_health
        timer = state.timer

        check('P1 health readable', p1 is not None, f'p1={p1}')
        check('P2 health readable', p2 is not None, f'p2={p2}')
        check('Timer readable', timer is not None, f'timer={timer}')

        if p1 is not None:
            check('P1 health in range [0, 160]', 0 <= p1 <= HEALTH_MAX, f'{p1}')
        if p2 is not None:
            check('P2 health in range [0, 160]', 0 <= p2 <= HEALTH_MAX, f'{p2}')

        # Also read raw u32 for diagnostics
        p1_raw = h.read_u32(0x800FE0D8)
        p2_raw = h.read_u32(0x80126F54)
        check('P1 raw u32 in range [0, 65536]', 0 <= p1_raw <= HEALTH_RAW_MAX,
              f'raw={p1_raw} -> norm={int(p1_raw * 160 / HEALTH_RAW_MAX)}')
        check('P2 raw u32 in range [0, 65536]', 0 <= p2_raw <= HEALTH_RAW_MAX,
              f'raw={p2_raw} -> norm={int(p2_raw * 160 / HEALTH_RAW_MAX)}')

        # Positions
        check('P1 X readable', state.p1_x is not None, f'p1_x={state.p1_x}')
        check('P2 X readable', state.p2_x is not None, f'p2_x={state.p2_x}')
    except Exception as e:
        check('tracer.read()', False, str(e))

    # ── 7. P1 stays still, P2 attacks → verify P1 health drops ──────────
    print(f'\n[7/8] P1 health test (P1=NEUTRAL, P2=random attacks via P2 ctrl)')

    # Reload savestate for a clean fight
    try:
        b.load_savestate_path(save_path)
        write_ctrl(0)  # P1 neutral
        write_ctrl(0, path=CTRL_PATH_P2)  # P2 neutral
        step_frames(b, settle_frames)
    except Exception as e:
        check('Reload for P1 health test', False, str(e))

    pre_state = tracer.read(0)
    p1_pre = pre_state.p1_health or 0
    p2_pre = pre_state.p2_health or 0
    print(f'  Start: P1={p1_pre}  P2={p2_pre}')

    import random as _rng
    # Button masks for P2 attacks
    _A    = 1 << 7   # LOW PUNCH
    _B    = 1 << 6   # HIGH PUNCH
    _C_UP = 1 << 11  # HIGH KICK
    _C_R  = 1 << 8   # LOW KICK
    _D_L  = 1 << 1   # walk toward P1 (P2 starts on right)
    _ATKS = [_A, _B, _C_UP, _C_R]

    # Phase 1: P2 walks left toward P1 (100 steps), P1 stays neutral
    for _ in range(100):
        write_ctrl(0)  # P1 neutral
        write_ctrl(_D_L, path=CTRL_PATH_P2)
        try:
            step_frames(b, action_frames)
        except Exception:
            break

    # Phase 2: P2 sends random attacks, P1 stays still (200 steps)
    for step in range(200):
        write_ctrl(0)  # P1 always neutral
        if step % 3 == 0:
            atk = _rng.choice(_ATKS) | _D_L
            write_ctrl(atk, path=CTRL_PATH_P2)
        else:
            write_ctrl(0, path=CTRL_PATH_P2)
        try:
            step_frames(b, action_frames)
        except Exception:
            break
        if step % 50 == 49:
            mid = tracer.read(0)
            print(f'  step={step+1}: P1={mid.p1_health} P2={mid.p2_health}')

    write_ctrl(0)
    write_ctrl(0, path=CTRL_PATH_P2)

    post_state = tracer.read(0)
    p1_post = post_state.p1_health or 0
    p2_post = post_state.p2_health or 0

    p1_took_damage = p1_post < p1_pre
    check('P1 health decreased (P2 attacked)', p1_took_damage,
          f'P1: {p1_pre}->{p1_post} (delta={p1_pre - p1_post})')
    check('P2 health unchanged (P1 stayed still)', p2_post >= p2_pre - 5,
          f'P2: {p2_pre}->{p2_post}')

    if not p1_took_damage:
        print(f'  [{WARN}] P1 took no damage! P2 attacks may not be reaching.')
        print(f'         P1_X={post_state.p1_x}  P2_X={post_state.p2_x}')

    # ── 8. Observation + reward pipeline ──────────────────────────────────────
    print(f'\n[8/8] Observation and reward pipeline')
    try:
        obs = build_obs(post_state)
        check(
            f'build_obs() returns {RAW_OBS_DIM} floats',
            len(obs) == RAW_OBS_DIM,
            f'len={len(obs)}',
        )
        check('All obs values are finite', all(-10 < v < 10 for v in obs),
              f'range=[{min(obs):.3f}, {max(obs):.3f}]')
    except Exception as e:
        check('build_obs()', False, str(e))

    try:
        extractor = Mk4ShapedRewardExtractor()
        terms = extractor.compute(pre_state, post_state, action_history=['LOW_PUNCH'] * 5)
        r = terms.scalar()
        check('Reward extractor runs', True, f'reward={r:+.3f}')
        check('dealt makes sense', terms.damage_dealt >= 0, f'dealt={terms.damage_dealt:.1f}')
        check('taken makes sense', terms.damage_taken <= 0, f'taken={terms.damage_taken:.1f}')
    except Exception as e:
        check('Reward extractor', False, str(e))

    # is_round_over / p1_won
    try:
        over = tracer.is_round_over(post_state)
        won = tracer.p1_won(post_state)
        check('is_round_over() runs', True, f'over={over}')
        check('p1_won() runs', True, f'won={won}')
    except Exception as e:
        check('Round detection', False, str(e))

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print('=' * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print(f'  RESULTS: {passed}/{total} passed, {failed} failed')

    if failed > 0:
        print(f'\n  FAILURES:')
        for name, ok, detail in results:
            if not ok:
                print(f'    - {name}: {detail}')

    print('=' * 70)

    # Cleanup
    write_ctrl(0)
    try:
        b.terminate_server()
    except Exception:
        pass
    try:
        b.close()
    except Exception:
        pass
    # Give server a moment to shut down, then force kill if needed
    time.sleep(2)
    if proc.poll() is None:
        proc.kill()
    log_file.close()

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.')
        sys.exit(130)
