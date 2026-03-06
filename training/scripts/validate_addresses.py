#!/usr/bin/env python3
"""
validate_addresses.py — Live verification of MK4 RAM addresses.

Boots one emulator, loads the arcade savestate, reads all addresses at rest,
then advances frames and checks for expected changes. Prints PASS/FAIL per address.

Run:  python3 training/scripts/validate_addresses.py
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / 'training/src'))
sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

PYTHON    = sys.executable
BRIDGE_DIR = N64_ROOT / 'training/data/bridge'
SOCK_PATH  = BRIDGE_DIR / 'mk4-validate.sock'
LOG_DIR    = N64_ROOT / 'training/data/logs'
ROM_PATH   = str(N64_ROOT / 'Mortal Kombat 4 (USA).z64')
M64P_BIN   = str(N64_ROOT / 'vendor/mupen64plus-ui-console/projects/unix/mupen64plus')
CORELIB    = str(N64_ROOT / 'vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib')
PLUGIN     = str(N64_ROOT / 'vendor/n64train-input/n64train-input.dylib')
PLUG_DIR   = '/opt/homebrew/lib/mupen64plus'
DATA_DIR   = '/opt/homebrew/share/mupen64plus'

# Addresses to validate
ADDRS = {
    'P1_HEALTH':  {'addr': 0x800FE0D8, 'type': 'u32', 'expect_start': 0x10000, 'desc': 'P1 internal health (full=65536)'},
    'P2_HEALTH':  {'addr': 0x80126F54, 'type': 'u32', 'expect_start': 0x10000, 'desc': 'P2 internal health (full=65536)'},
    'TIMER':      {'addr': 0x80105118, 'type': 'u8',  'expect_start': (90, 99), 'desc': 'Fight timer (counts down from ~97)'},
    'P1_X':       {'addr': 0x800F87F8, 'type': 's16hi', 'desc': 'P1 X position (signed hi-halfword)'},
    'P2_X':       {'addr': 0x8006A060, 'type': 's16hi', 'desc': 'P2 X position (signed hi-halfword)'},
    'P1_ACTION':  {'addr': 0x800FE08C, 'type': 'u32', 'desc': 'P1 action state (0=idle)'},
    'P1_GROUND':  {'addr': 0x800FE0F8, 'type': 'u32', 'desc': 'P1 ground flag (4=ground, 1=air)'},
    'P1_Y_VEL':   {'addr': 0x800FE90C, 'type': 'u32', 'desc': 'P1 Y velocity (PERFECT ARC on jump)'},
    'P1_ATK_TYPE':{'addr': 0x800FE090, 'type': 'u32', 'desc': 'P1 attack type register'},
    'P1_LK':      {'addr': 0x800FE144, 'type': 'u32', 'desc': 'P1 LK register'},
    'P1_HITSTUN': {'addr': 0x800FE310, 'type': 'u32', 'desc': 'P1 hitstun/hitbox active'},
    'P2_ACTION':  {'addr': 0x80126EC0, 'type': 'u32', 'desc': 'P2 anim pointer'},
    'P2_GROUND':  {'addr': 0x80126ECC, 'type': 'u32', 'desc': 'P2 ground flag (2=ground, 1=air)'},
    'P2_ATK_TYPE':{'addr': 0x80126E94, 'type': 'u32', 'desc': 'P2 attack type register'},
    'P2_LK':      {'addr': 0x80126F30, 'type': 'u32', 'desc': 'P2 LK register'},
    'P2_HITSTUN': {'addr': 0x80126F9C, 'type': 'u32', 'desc': 'P2 hitstun (0=idle, 2=punch)'},
}

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
BOLD   = '\033[1m'
RESET  = '\033[0m'


def send_cmd(sock_path, command, payload=None, timeout=10.0):
    """Send a bridge command and return response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(sock_path))
    req = {"id": "val", "command": command, "payload": payload or {}}
    s.sendall((json.dumps(req) + "\n").encode())
    resp = json.loads(s.makefile('r').readline())
    s.close()
    if not resp.get('ok'):
        raise RuntimeError(f"Bridge error: {resp.get('error', {}).get('message', 'unknown')}")
    return resp.get('payload', {})


def dbg_cmd(sock_path, cmd, timeout=10.0):
    """Run a debugger command and return output string."""
    resp = send_cmd(sock_path, "DEBUGGER_COMMAND", {"command": cmd, "timeout_sec": timeout}, timeout=timeout+5)
    return resp.get('output', '')


def read_u32(sock_path, addr):
    """Read a 32-bit word from emulator RAM."""
    out = dbg_cmd(sock_path, f"mem /1w 0x{addr:08x}")
    # Parse hex from output
    import re
    for line in out.strip().split('\n'):
        line = line.strip()
        if line.startswith('(dbg)') or line.startswith('PC at') or line.startswith('mem '):
            continue
        tokens = re.findall(r'[0-9A-Fa-f]{2,16}', line)
        if tokens:
            return int(tokens[-1], 16) & 0xFFFFFFFF
    raise ValueError(f"Could not parse u32 from: {out}")


def read_u8(sock_path, addr):
    """Read a single byte (with XOR3 correction)."""
    xor_addr = addr ^ 0x3
    out = dbg_cmd(sock_path, f"mem /1b 0x{xor_addr:08x}")
    import re
    for line in out.strip().split('\n'):
        line = line.strip()
        if line.startswith('(dbg)') or line.startswith('PC at') or line.startswith('mem '):
            continue
        tokens = re.findall(r'[0-9A-Fa-f]{2,16}', line)
        if tokens:
            return int(tokens[-1], 16) & 0xFF
    raise ValueError(f"Could not parse u8 from: {out}")


def read_s16hi(sock_path, addr):
    """Read signed int16 from upper halfword of u32."""
    w = read_u32(sock_path, addr)
    hi = (w >> 16) & 0xFFFF
    return hi if hi < 0x8000 else hi - 0x10000


def read_addr(sock_path, name, info):
    """Read an address using the appropriate type."""
    t = info['type']
    if t == 'u32':
        return read_u32(sock_path, info['addr'])
    elif t == 'u8':
        return read_u8(sock_path, info['addr'])
    elif t == 's16hi':
        return read_s16hi(sock_path, info['addr'])
    else:
        raise ValueError(f"Unknown type: {t}")


def step_frames(sock_path, n):
    """Advance emulator N frames."""
    out = dbg_cmd(sock_path, f"frame {n}", timeout=max(10, n * 2))
    if f'M64P_FRAME_OK frames={n}' not in out:
        print(f"  {YELLOW}WARN: frame step may have failed: {out[-200:]}{RESET}")


def main():
    # Find savestate
    candidates = [
        N64_ROOT / 'training/data/savestates/mk4_arcade/arcade_training_scorpion.st',
        N64_ROOT / 'training/data/savestates/mk4_arcade/p1p2state.st',
    ]
    save_path = None
    for c in candidates:
        if c.exists():
            save_path = c
            break
    if not save_path:
        print(f"{RED}No savestate found{RESET}")
        sys.exit(1)

    print(f"{BOLD}MK4 RAM Address Validation{RESET}")
    print(f"  Savestate: {save_path.name}")
    print(f"  ROM: {Path(ROM_PATH).name}")
    print()

    # Clean up
    if SOCK_PATH.exists():
        SOCK_PATH.unlink()
    cfg_dir = N64_ROOT / '.m64p/instances/validate/config'
    import shutil
    if cfg_dir.exists():
        shutil.rmtree(str(cfg_dir))
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Launch bridge server + emulator
    print(f"Launching emulator...")
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / 'validate_addrs.log'
    cmd = [
        PYTHON, str(N64_ROOT / 'training/scripts/run_bridge_server.py'),
        '--socket-path',         str(SOCK_PATH),
        '--instance-id',         'validate',
        '--memory-reader',       'debugger-dump',
        '--rom-path',            ROM_PATH,
        '--debugger-ui-binary',  M64P_BIN,
        '--debugger-corelib',    CORELIB,
        '--debugger-plugindir',  PLUG_DIR,
        '--debugger-configdir',  str(cfg_dir),
        '--debugger-datadir',    DATA_DIR,
        '--debugger-dump-dir',   str(N64_ROOT / 'training/data/bridge/debugger_dumps/validate'),
        '--debugger-gfx-plugin', 'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
        '--debugger-input-plugin', PLUGIN,
        '--debugger-rsp-plugin', 'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode',    '0',
        '--speed-mode',          'DEBUG_VISIBLE',
        '--log-path',            str(log_path),
    ]
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = '/tmp/mk4_ctrl_validate'
    log_file = open(log_path, 'w')
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)

    # Wait for socket
    print(f"Waiting for bridge socket...")
    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        if SOCK_PATH.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(str(SOCK_PATH))
                s.close()
                ready = True
                break
            except Exception:
                pass
        time.sleep(1)

    if not ready:
        print(f"{RED}Bridge server failed to start{RESET}")
        proc.terminate()
        sys.exit(1)

    print(f"{GREEN}Bridge ready{RESET}")
    print()

    try:
        # Pause emulator
        dbg_cmd(SOCK_PATH, "pause")
        time.sleep(0.3)

        # Load savestate
        print(f"Loading savestate: {save_path.name}...")
        send_cmd(SOCK_PATH, "LOAD_SAVESTATE", {"savestate_path": str(save_path)}, timeout=45)
        time.sleep(0.5)

        # Settle frames
        step_frames(SOCK_PATH, 120)  # 2 seconds of game time
        time.sleep(0.5)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 1: Read all addresses at round start
        # ═══════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{'═'*65}{RESET}")
        print(f"{BOLD}  TEST 1: Initial values after savestate load + settle{RESET}")
        print(f"{BOLD}{'═'*65}{RESET}\n")

        initial = {}
        results = {}
        for name, info in ADDRS.items():
            try:
                val = read_addr(SOCK_PATH, name, info)
                initial[name] = val
                # Check expected start values
                status = ''
                if 'expect_start' in info:
                    exp = info['expect_start']
                    if isinstance(exp, tuple):
                        if exp[0] <= val <= exp[1]:
                            status = f'{GREEN}PASS{RESET}'
                            results[name] = 'PASS'
                        else:
                            status = f'{RED}FAIL (expected {exp[0]}-{exp[1]}){RESET}'
                            results[name] = 'FAIL'
                    elif val == exp:
                        status = f'{GREEN}PASS{RESET}'
                        results[name] = 'PASS'
                    else:
                        status = f'{RED}FAIL (expected 0x{exp:X}={exp}){RESET}'
                        results[name] = 'FAIL'
                else:
                    status = f'{YELLOW}INFO{RESET}'
                    results[name] = 'INFO'

                hex_str = f'0x{val:08X}' if info['type'] in ('u32', 's16hi') else f'0x{val:02X}'
                print(f'  {name:15s} = {hex_str:>12s} ({val:>10d})  {status}  — {info["desc"]}')
            except Exception as e:
                print(f'  {name:15s}   {RED}ERROR: {e}{RESET}')
                results[name] = 'ERROR'

        # ═══════════════════════════════════════════════════════════════════
        # TEST 2: Walk P1 toward P2 (D_RIGHT only, 200 frames)
        # ═══════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{'═'*65}{RESET}")
        print(f"{BOLD}  TEST 2: Walk P1 toward P2 (200 frames, D_RIGHT only){RESET}")
        print(f"{BOLD}{'═'*65}{RESET}\n")

        import struct, mmap
        ctrl_path = '/tmp/mk4_ctrl_validate'
        # N64 button bitmasks (from plugin.c)
        BTN_D_RIGHT = 1 << 0
        BTN_A       = 1 << 7  # LOW PUNCH
        BTN_B       = 1 << 6  # HIGH PUNCH
        BTN_C_RIGHT = 1 << 8  # LOW KICK
        BTN_C_UP    = 1 << 11 # HIGH KICK

        def write_ctrl(buttons, x=0, y=0):
            if not os.path.exists(ctrl_path):
                with open(ctrl_path, 'w+b') as f:
                    f.write(b'\x00' * 4)
            with open(ctrl_path, 'r+b') as f:
                m = mmap.mmap(f.fileno(), 4)
                m.seek(0)
                m.write(struct.pack('<Hbb', buttons & 0xFFFF, x, y))
                m.flush()
                m.close()

        # Walk toward P2
        write_ctrl(BTN_D_RIGHT)
        step_frames(SOCK_PATH, 200)
        time.sleep(0.3)

        after_walk = {}
        for name, info in ADDRS.items():
            try:
                val = read_addr(SOCK_PATH, name, info)
                after_walk[name] = val
                prev = initial.get(name)
                changed = prev is not None and val != prev
                delta_str = ''
                if changed and info['type'] in ('u32', 'u8'):
                    delta = val - prev
                    delta_str = f' (delta={delta:+d})'
                chg = f'{GREEN}CHANGED{RESET}' if changed else f'{YELLOW}same{RESET}'
                hex_str = f'0x{val:08X}' if info['type'] in ('u32', 's16hi') else f'0x{val:02X}'
                print(f'  {name:15s} = {hex_str:>12s} ({val:>10d})  {chg}{delta_str}')
            except Exception as e:
                print(f'  {name:15s}   {RED}ERROR: {e}{RESET}')

        # ═══════════════════════════════════════════════════════════════════
        # TEST 3: P1 punches repeatedly (no direction held!)
        # Alternate: punch 8 frames → neutral 12 frames → repeat
        # This lets the attack animation play and land hits.
        # ═══════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{'═'*65}{RESET}")
        print(f"{BOLD}  TEST 3: P1 punching P2 (alternating A/B/kicks, 40 reps){RESET}")
        print(f"{BOLD}{'═'*65}{RESET}\n")

        # Cycle through different attacks to maximize chance of landing
        attacks = [BTN_A, BTN_B, BTN_C_RIGHT, BTN_C_UP, BTN_A, BTN_B]
        attack_names = ['LOW_PUNCH(A)', 'HIGH_PUNCH(B)', 'LOW_KICK(C_R)', 'HIGH_KICK(C_U)', 'LOW_PUNCH(A)', 'HIGH_PUNCH(B)']

        total_reps = 40
        for i in range(total_reps):
            atk_idx = i % len(attacks)
            btn = attacks[atk_idx]
            # Press attack (no direction) for 8 frames
            write_ctrl(btn)
            step_frames(SOCK_PATH, 8)
            # Release for 12 frames so animation completes
            write_ctrl(0)
            step_frames(SOCK_PATH, 12)

            # Print progress every 10 reps
            if (i + 1) % 10 == 0:
                p2h = read_addr(SOCK_PATH, 'P2_HEALTH', ADDRS['P2_HEALTH'])
                p1h = read_addr(SOCK_PATH, 'P1_HEALTH', ADDRS['P1_HEALTH'])
                print(f'  rep {i+1:2d}/{total_reps}: P1_HP={p1h}  P2_HP={p2h}  (last attack: {attack_names[atk_idx]})')

        time.sleep(0.3)

        after_fight = {}
        for name, info in ADDRS.items():
            try:
                val = read_addr(SOCK_PATH, name, info)
                after_fight[name] = val
                prev = initial.get(name)
                changed = prev is not None and val != prev
                delta_str = ''
                if changed and info['type'] in ('u32', 'u8'):
                    delta = val - prev
                    delta_str = f' (delta={delta:+d})'
                chg = f'{GREEN}CHANGED{RESET}' if changed else f'{YELLOW}same{RESET}'
                hex_str = f'0x{val:08X}' if info['type'] in ('u32', 's16hi') else f'0x{val:02X}'
                print(f'  {name:15s} = {hex_str:>12s} ({val:>10d})  {chg}{delta_str}')
            except Exception as e:
                print(f'  {name:15s}   {RED}ERROR: {e}{RESET}')

        # Release controller
        write_ctrl(0)

        # ═══════════════════════════════════════════════════════════════════
        # VERDICT
        # ═══════════════════════════════════════════════════════════════════
        print(f"\n{BOLD}{'═'*65}{RESET}")
        print(f"{BOLD}  VERDICT{RESET}")
        print(f"{BOLD}{'═'*65}{RESET}\n")

        # Health checks
        p1_start = initial.get('P1_HEALTH')
        p2_start = initial.get('P2_HEALTH')
        p1_end   = after_fight.get('P1_HEALTH')
        p2_end   = after_fight.get('P2_HEALTH')
        timer_start = initial.get('TIMER')
        timer_end   = after_fight.get('TIMER')

        checks = []

        # P1 health at start
        if p1_start is not None:
            if p1_start == 0x10000:
                checks.append(('P1_HEALTH start=0x10000 (full)', True))
            else:
                checks.append((f'P1_HEALTH start=0x{p1_start:X} (expected 0x10000)', False))

        # P2 health at start
        if p2_start is not None:
            if p2_start == 0x10000:
                checks.append(('P2_HEALTH start=0x10000 (full)', True))
            else:
                checks.append((f'P2_HEALTH start=0x{p2_start:X} (expected 0x10000)', False))

        # P2 health should decrease (P1 attacked)
        if p2_start is not None and p2_end is not None:
            if p2_end < p2_start:
                checks.append((f'P2_HEALTH decreased: {p2_start} -> {p2_end} (P1 punches landed)', True))
            else:
                checks.append((f'P2_HEALTH did NOT decrease: {p2_start} -> {p2_end}', False))

        # P1 health should decrease (CPU attacks back)
        if p1_start is not None and p1_end is not None:
            if p1_end < p1_start:
                checks.append((f'P1_HEALTH decreased: {p1_start} -> {p1_end} (CPU attacked P1)', True))
            else:
                checks.append((f'P1_HEALTH did NOT decrease: {p1_start} -> {p1_end} — CPU may not have attacked', False))

        # Timer
        if timer_start is not None:
            if 90 <= timer_start <= 99:
                checks.append((f'TIMER start={timer_start} (valid range)', True))
            else:
                checks.append((f'TIMER start={timer_start} (expected 90-99)', False))

        if timer_end is not None and timer_start is not None:
            if timer_end < timer_start:
                checks.append((f'TIMER counting down: {timer_start} -> {timer_end}', True))
            else:
                checks.append((f'TIMER not counting: {timer_start} -> {timer_end}', False))

        passed = 0
        failed = 0
        for msg, ok in checks:
            if ok:
                print(f'  {GREEN}PASS{RESET}  {msg}')
                passed += 1
            else:
                print(f'  {RED}FAIL{RESET}  {msg}')
                failed += 1

        print(f'\n  {BOLD}Result: {passed} passed, {failed} failed{RESET}')
        if failed == 0:
            print(f'  {GREEN}{BOLD}All addresses verified!{RESET}')
        else:
            print(f'  {RED}{BOLD}Some addresses need investigation{RESET}')

    finally:
        # Cleanup
        print(f"\nCleaning up...")
        try:
            send_cmd(SOCK_PATH, "TERMINATE", timeout=3)
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        if SOCK_PATH.exists():
            SOCK_PATH.unlink()
        log_file.close()
        print("Done.")


if __name__ == '__main__':
    main()
