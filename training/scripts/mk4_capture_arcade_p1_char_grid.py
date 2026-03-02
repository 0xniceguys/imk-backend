#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(script_dir.parent / "src"))


_add_src_to_path()

from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper  # noqa: E402
from n64train.reverse.scanner import (  # noqa: E402
    AddressRange,
    BridgeMemoryScanner,
    reverse_capture_dir,
)
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402


DEFAULT_SOCKET = "/Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock"
FULL_RDRAM = AddressRange(start=0x0, end=0x800000)

# Snapshot offsets (dump-layout offsets, not debugger virtual addresses).
OFF_MENU_SCREEN_STATE = 0x48D34
OFF_SHARED_MENU_CURSOR = 0x11D810
OFF_MENU_SIG_A = 0x546D0
OFF_MENU_SIG_B = 0x5472E
OFF_MENU_SIG_C = 0x5472F

# Keyboard events (macOS virtual key codes via AppleScript/System Events)
KEY_UP = 126
KEY_DOWN = 125
KEY_LEFT = 123
KEY_RIGHT = 124
KEY_RETURN = 36
KEY_LEFT_SHIFT = 56
KEY_LEFT_CTRL = 59


CHAR_GRID: list[list[str]] = [
    ["Kai", "Raiden", "Shinnok", "Liu Kang", "Reptile"],
    ["Scorpion", "Jax", "Reiko", "Johnny Cage", "Jarek"],
    ["Tanya", "Fujin", "Sub-Zero", "Quan Chi", "Sonya"],
]


@dataclass(frozen=True)
class ScreenProbe:
    primary_state: int
    shared_cursor: int
    sig_a: int
    sig_b: int
    sig_c: int

    def to_dict(self) -> dict[str, int]:
        return {
            "primary_state": self.primary_state,
            "shared_cursor": self.shared_cursor,
            "sig_a_0x546D0": self.sig_a,
            "sig_b_0x5472E": self.sig_b,
            "sig_c_0x5472F": self.sig_c,
        }


class AppleScriptKeyDriver:
    def __init__(self, process_name: str = "mupen64plus") -> None:
        self.process_name = process_name

    def focus(self) -> None:
        script = f"""
        tell application "System Events"
          set frontmost of first process whose name is "{self.process_name}" to true
        end tell
        """
        self._run_osascript(script)

    def key_code(self, key_code: int) -> None:
        script = f"""
        tell application "System Events"
          key code {int(key_code)}
        end tell
        """
        self._run_osascript(script)

    def press(self, key_code: int, *, delay_sec: float = 0.20) -> None:
        self.focus()
        self.key_code(key_code)
        time.sleep(max(0.0, delay_sec))

    def _run_osascript(self, script: str) -> None:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "osascript failed: "
                + (proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}")
            )


class CGEventKeyDriver:
    """Inject keypresses directly into the mupen64plus process via Quartz CGEventPost.

    This uses macOS virtual key codes (same as AppleScriptKeyDriver) and posts events
    to the application PSN (process serial number) of mupen64plus — no Accessibility
    permission required since we're targeting a specific process by name/PID.
    """

    _VK_TO_USB_HID: dict[int, int] = {
        # macOS vkCode -> USB HID usage (for CGEvent keyboard emulation)
        # Arrow keys
        126: 0x52,  # KEY_UP
        125: 0x51,  # KEY_DOWN
        123: 0x50,  # KEY_LEFT
        124: 0x4F,  # KEY_RIGHT
        # Confirm / cancel
        36: 0x28,   # KEY_RETURN / Enter
        56: 0xE1,   # KEY_LEFT_SHIFT (HID Left Shift)
        59: 0xE0,   # KEY_LEFT_CTRL  (HID Left Ctrl)
    }

    def __init__(self, process_name: str = "mupen64plus") -> None:
        self.process_name = process_name
        self._quartz: object = None
        self._psn: object = None
        self._load_quartz()

    def _load_quartz(self) -> None:
        try:
            import Quartz  # type: ignore[import]
            self._quartz = Quartz
        except ImportError:
            self._quartz = None

    def _find_psn(self) -> object:
        """Find the PSN of the mupen64plus process (needed to post to specific app)."""
        if self._quartz is None:
            return None
        Q = self._quartz
        pid = int(subprocess.check_output(["pgrep", "-x", self.process_name]).split()[0])
        psn = Q.ProcessSerialNumber()
        Q.GetProcessForPID(pid, psn)
        return psn

    def focus(self) -> None:
        # Bring mupen64plus to front so its SDL event loop processes our events.
        try:
            subprocess.run(
                ["osascript", "-e", f'tell application "{self.process_name}" to activate'],
                capture_output=True, timeout=2.0
            )
        except Exception:
            pass

    def _get_pid(self) -> int:
        out = subprocess.check_output(["pgrep", "-x", self.process_name])
        return int(out.split()[0])

    def press(self, key_code: int, *, delay_sec: float = 0.20) -> None:
        if self._quartz is None:
            raise RuntimeError("pyobjc Quartz not available — install pyobjc-framework-Quartz")
        Q = self._quartz
        vk = int(key_code)

        # Post directly to mupen64plus by PID — no need to bring it to front.
        try:
            pid = self._get_pid()
            down = Q.CGEventCreateKeyboardEvent(None, vk, True)
            up   = Q.CGEventCreateKeyboardEvent(None, vk, False)
            Q.CGEventPostToPid(pid, down)
            time.sleep(0.25)  # hold long enough for SDL event pump to drain
            Q.CGEventPostToPid(pid, up)
            del down, up
        except Exception:
            # Fallback: post to system event tap with focus
            self.focus()
            time.sleep(0.05)
            down = Q.CGEventCreateKeyboardEvent(None, vk, True)
            up   = Q.CGEventCreateKeyboardEvent(None, vk, False)
            Q.CGEventPost(Q.kCGHIDEventTap, down)
            time.sleep(0.25)
            Q.CGEventPost(Q.kCGHIDEventTap, up)
        time.sleep(max(0.0, delay_sec))





class DebuggerSendKeyDriver:
    _KEY_NAME_BY_CODE = {
        KEY_UP: "up",
        KEY_DOWN: "down",
        KEY_LEFT: "left",
        KEY_RIGHT: "right",
        KEY_RETURN: "return",
        KEY_LEFT_SHIFT: "lshift",
        KEY_LEFT_CTRL: "lctrl",
    }

    def __init__(self, bridge: SocketEmulatorBridge) -> None:
        self.bridge = bridge

    def focus(self) -> None:
        return

    def press(self, key_code: int, *, delay_sec: float = 0.20) -> None:
        """Inject a key press into the emulator.

        Strategy: keydown → run emulator freely for hold_sec → keyup → pause.

        Running freely during the hold guarantees the game's input-polling loop
        executes and sees the key event.  Frame-stepping (the old approach) kept
        the emulator in paused mode between frames and the menu handler could miss
        the event entirely.
        """
        key_name = self._KEY_NAME_BY_CODE.get(int(key_code))
        if key_name is None:
            raise ValueError(f"Unsupported debugger sendkey key code: {key_code}")

        # How long (real seconds) to hold the key while the emulator is running.
        # Confirm/start/cancel keys need more frames at N64 ~60fps so we hold longer.
        if key_name in {"return", "lshift", "lctrl"}:
            hold_sec = 0.20   # ~12 frames @ 60fps
            tail_sec = 0.10   # a few frames of tail after keyup
        else:
            hold_sec = 0.08   # ~5 frames for navigation keys
            tail_sec = 0.05

        def _cmd(cmd: str, ok_token: str, timeout: float) -> None:
            resp = self.bridge.debugger_command(cmd, timeout_sec=timeout, output_tail_chars=2000)
            out = str(resp.get("output", ""))
            if ok_token not in out:
                raise RuntimeError(f"Debugger command failed ({cmd!r}): {out.strip()}")

        # 1. Send keydown (emulator may be paused or running — command works either way)
        _cmd(f"keydown {key_name}", "M64P_KEYDOWN_OK", 2.0)

        # 2. Make sure emulator is running so the game loop polls the key
        self.bridge.debugger_command("run", timeout_sec=2.0, output_tail_chars=500)

        # 3. Hold for real wall-clock time so multiple game frames execute
        time.sleep(hold_sec)

        # 4. Release the key (emulator still running)
        _cmd(f"keyup {key_name}", "M64P_KEYUP_OK", 2.0)

        # 5. Brief tail so the game registers the release, then pause
        time.sleep(tail_sec)
        self.bridge.debugger_command("pause", timeout_sec=2.0, output_tail_chars=500)

        # 6. Caller-requested settling delay
        time.sleep(max(0.0, delay_sec))


class Mk4CharacterGridCapture:
    def __init__(
        self,
        *,
        bridge: SocketEmulatorBridge,
        key_driver: AppleScriptKeyDriver,
        out_dir: Path,
        key_delay_sec: float = 0.20,
        transition_poll_sec: float = 0.12,
        transition_timeout_sec: float = 8.0,
    ) -> None:
        self.bridge = bridge
        self.helper = Mk4BridgeHelper(bridge)
        self.scanner = BridgeMemoryScanner(bridge)
        self.key_driver = key_driver
        self.out_dir = out_dir
        self.key_delay_sec = key_delay_sec
        self.transition_poll_sec = transition_poll_sec
        self.transition_timeout_sec = transition_timeout_sec

    def run(self) -> dict[str, object]:
        manifest_summary: dict[str, object] = {
            "flow": [],
            "character_grid": [],
            "notes": [
                "Automated via macOS AppleScript keystrokes + bridge debugger pause/capture.",
                "Character labels use user-provided row order (3x5 grid).",
            ],
        }

        self._ensure_bridge_alive()
        self._pause()
        start_probe = self._probe()
        char_select_probe_value = self._is_character_select_screen({0, 6, 10, 13})
        if isinstance(char_select_probe_value, ScreenProbe):
            char_select_probe = char_select_probe_value
            manifest_summary["flow"].append(
                self._capture_labeled_snapshot(
                    "flow_character_select_entry_existing",
                    extra_meta={"phase": "flow", "char_select_probe": char_select_probe.to_dict()},
                )
            )
        else:
            self._resume()
            if start_probe.primary_state == 6:
                self._recover_to_top_main_arcade()
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_top_main_arcade_ready", extra_meta={"phase": "flow"})
                )

                # Enter Arcade from top-level menu.
                self._menu_confirm_with_fallback()
                self._wait_for(lambda: self._probe().primary_state == 10, "Arcade Rumble Warning (state=10)")
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_rumble_warning", extra_meta={"phase": "flow"})
                )

                # Press Start to continue past rumble warning.
                self._press_and_settle(KEY_RETURN)
                self._wait_for(self._is_arcade_player_count_screen, "Arcade 1P/2P screen")
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_player_count_p1p2", extra_meta={"phase": "flow"})
                )
            elif start_probe.primary_state == 10:
                self._pause()
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_rumble_warning_existing", extra_meta={"phase": "flow"})
                )
                self._resume()
                self._press_and_settle(KEY_RETURN)
                self._wait_for(self._is_arcade_player_count_screen, "Arcade 1P/2P screen")
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_player_count_p1p2", extra_meta={"phase": "flow"})
                )
            elif self._is_arcade_player_count_screen_from_probe(start_probe):
                self._pause()
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_player_count_p1p2_existing", extra_meta={"phase": "flow"})
                )
            else:
                self._recover_to_top_main_arcade()
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_top_main_arcade_ready", extra_meta={"phase": "flow"})
                )
                self._menu_confirm_with_fallback()
                self._wait_for(lambda: self._probe().primary_state == 10, "Arcade Rumble Warning (state=10)")
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_rumble_warning", extra_meta={"phase": "flow"})
                )
                self._press_and_settle(KEY_RETURN)
                self._wait_for(self._is_arcade_player_count_screen, "Arcade 1P/2P screen")
                manifest_summary["flow"].append(
                    self._capture_labeled_snapshot("flow_arcade_player_count_p1p2", extra_meta={"phase": "flow"})
                )

            # Ensure P1 selected.
            self._ensure_player_count_cursor(0)
            manifest_summary["flow"].append(
                self._capture_labeled_snapshot("flow_arcade_player_count_p1_selected", extra_meta={"phase": "flow"})
            )

            # Enter character select.
            self._menu_confirm_with_fallback()
            char_select_probe = self._wait_for_character_select()
            manifest_summary["flow"].append(
                self._capture_labeled_snapshot(
                    "flow_character_select_entry",
                    extra_meta={"phase": "flow", "char_select_probe": char_select_probe.to_dict()},
                )
            )

        # Capture all 15 character slots in user-provided row order.
        captured = self._capture_character_grid()
        manifest_summary["character_grid"] = captured

        # Leave emulator running for the user.
        self._resume()
        return manifest_summary

    def _capture_character_grid(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []

        # Starting assumption: default highlight is top-left (Kai).
        for row_idx, row in enumerate(CHAR_GRID):
            for col_idx, character_name in enumerate(row):
                label = f"charselect_p1_r{row_idx+1:02d}_c{col_idx+1:02d}_{character_name.lower().replace(' ', '_')}"
                snap = self._capture_labeled_snapshot(
                    label,
                    extra_meta={
                        "phase": "char_grid",
                        "row": row_idx + 1,
                        "col": col_idx + 1,
                        "character_name": character_name,
                    },
                )
                results.append(
                    {
                        "row": row_idx + 1,
                        "col": col_idx + 1,
                        "character_name": character_name,
                        "snapshot": snap,
                    }
                )
                # Advance cursor to next cell in row-major order.
                if row_idx == len(CHAR_GRID) - 1 and col_idx == len(row) - 1:
                    continue
                if col_idx < len(row) - 1:
                    self._press_and_settle(KEY_RIGHT)
                else:
                    # End of row: go to start of next row.
                    self._press_and_settle(KEY_DOWN)
                    for _ in range(len(row) - 1):
                        self._press_and_settle(KEY_LEFT)
        return results

    def _ensure_bridge_alive(self) -> None:
        self.bridge.hello()

    def _recover_to_top_main_arcade(self) -> None:
        """Drive the emulator from any known screen state to the top-level menu with Arcade highlighted."""
        deadline = time.time() + 60.0
        unknown_zero_attempt = 0
        while time.time() < deadline:
            self._pause()
            probe = self._probe()

            if probe.primary_state == 6:
                # Top-level main menu. Normalize cursor to Arcade (cursor=0).
                if probe.shared_cursor > 0:
                    for _ in range(int(probe.shared_cursor)):
                        self._resume()
                        self._press_and_settle(KEY_UP)
                        self._pause()
                    probe = self._probe()
                if probe.primary_state == 6 and probe.shared_cursor == 0:
                    return

            elif probe.primary_state == 10:
                # Rumble warning -> back out with B.
                self._resume()
                self._press_and_settle(KEY_LEFT_CTRL, delay_sec=0.30)
                time.sleep(0.20)  # settle before next probe

            elif self._is_arcade_player_count_screen_from_probe(probe):
                # 1P/2P screen -> back out with B.
                self._resume()
                self._press_and_settle(KEY_LEFT_CTRL, delay_sec=0.30)
                time.sleep(0.20)  # settle before next probe

            elif probe.primary_state == 0 and not self._looks_like_player_count_probe(probe):
                if self._looks_like_zero_transition_probe(probe):
                    # Transition/title family: let frames advance naturally.
                    self._advance_frames(120)
                    time.sleep(0.10)
                else:
                    # Intro/attract screens: alternate Start and A to skip.
                    self._resume()
                    key = KEY_RETURN if (unknown_zero_attempt % 2) == 0 else KEY_LEFT_SHIFT
                    self._press_and_settle(key, delay_sec=0.40)
                    unknown_zero_attempt += 1
                    time.sleep(0.20)

            else:
                # Unknown screen -> try B to back out.
                self._resume()
                self._press_and_settle(KEY_LEFT_CTRL, delay_sec=0.30)
                time.sleep(0.20)

        raise RuntimeError("Failed to recover to top-level main menu (Arcade) within timeout")


    def _ensure_player_count_cursor(self, target: int) -> None:
        self._pause()
        current = self.helper.get_arcade_player_count_cursor()["value"]
        if int(current) == int(target):
            return
        self._resume()
        # 1P/2P selector is horizontal in MK4 (P2 is to the right).
        # Try horizontal navigation first, then fall back to vertical if needed.
        primary_key = KEY_LEFT if int(target) < int(current) else KEY_RIGHT
        self._press_and_settle(primary_key)
        self._pause()
        now = self.helper.get_arcade_player_count_cursor()["value"]
        if int(now) != int(target):
            self._resume()
            fallback_key = KEY_UP if int(target) < int(current) else KEY_DOWN
            self._press_and_settle(fallback_key)
            self._pause()
            now = self.helper.get_arcade_player_count_cursor()["value"]
        if int(now) != int(target):
            raise RuntimeError(f"Failed to set Arcade player-count cursor to {target}; got {now}")

    def _advance_frames(self, frames: int) -> None:
        resp = self.bridge.debugger_command(
            f"frame {max(1, int(frames))}",
            timeout_sec=max(5.0, float(frames) * 0.1),
            output_tail_chars=4000,
        )
        out = str(resp.get("output", ""))
        if "M64P_FRAME_OK" not in out:
            raise RuntimeError(f"Frame advance failed: {out.strip()}")

    def _wait_for_character_select(self) -> ScreenProbe:
        known_primary_states = {0, 6, 10, 13}
        return self._wait_for(
            lambda: self._is_character_select_screen(known_primary_states),
            "Character Select screen",
        )

    def _is_character_select_screen(self, known_primary_states: set[int]) -> ScreenProbe | bool:
        probe = self._probe()
        if probe.primary_state not in known_primary_states:
            return probe
        # If primary_state collides again, distinguish from known states by signatures.
        if probe.primary_state == 0 and not self._looks_like_intro_probe(probe) and not self._looks_like_player_count_probe(probe):
            return probe
        return False

    def _wait_for(self, predicate: Callable[[], ScreenProbe | bool], desc: str) -> ScreenProbe:
        deadline = time.time() + self.transition_timeout_sec
        while time.time() < deadline:
            self._pause()
            result = predicate()
            if result:
                if isinstance(result, ScreenProbe):
                    return result
                return self._probe()
            self._resume()
            time.sleep(self.transition_poll_sec)
        raise RuntimeError(f"Timed out waiting for {desc}")

    def _menu_confirm_with_fallback(self) -> None:
        # Try A (Left Shift) first, then Enter if no state transition occurs.
        before = self._snapshot_probe_while_paused()
        self._resume()
        self._press_and_settle(KEY_LEFT_SHIFT, delay_sec=0.25)
        time.sleep(0.35)
        self._pause()
        after = self._probe()
        if after != before:
            return
        self._resume()
        self._press_and_settle(KEY_RETURN, delay_sec=0.25)
        time.sleep(0.35)

    def _press_and_settle(self, key_code: int, *, delay_sec: float | None = None) -> None:
        self.key_driver.press(key_code, delay_sec=delay_sec if delay_sec is not None else self.key_delay_sec)
        time.sleep(0.12)

    def _pause(self) -> None:
        self.helper.pause()

    def _resume(self) -> None:
        self.helper.run()

    def _probe(self) -> ScreenProbe:
        return ScreenProbe(
            primary_state=self.helper.get_menu_screen_state()["value"],
            shared_cursor=self.helper.read_u8(0x8011D810),
            sig_a=self.helper.read_u8(0x800546D0),
            sig_b=self.helper.read_u8(0x8005472E),
            sig_c=self.helper.read_u8(0x8005472F),
        )

    def _snapshot_probe_while_paused(self) -> ScreenProbe:
        return self._probe()

    def _looks_like_intro_probe(self, probe: ScreenProbe) -> bool:
        return probe.primary_state == 0 and probe.sig_a == 0 and probe.sig_b == 255 and probe.sig_c == 255

    def _looks_like_player_count_probe(self, probe: ScreenProbe) -> bool:
        return probe.primary_state == 0 and probe.sig_a == 3 and probe.sig_b == 3 and probe.sig_c == 0

    def _looks_like_zero_transition_probe(self, probe: ScreenProbe) -> bool:
        return probe.primary_state == 0 and probe.sig_a == 0 and probe.sig_b == 0 and probe.sig_c == 0

    def _is_arcade_player_count_screen(self) -> ScreenProbe | bool:
        probe = self._probe()
        return probe if self._is_arcade_player_count_screen_from_probe(probe) else False

    def _is_arcade_player_count_screen_from_probe(self, probe: ScreenProbe) -> bool:
        return self._looks_like_player_count_probe(probe)

    def _capture_labeled_snapshot(self, label: str, *, extra_meta: dict[str, object] | None = None) -> dict[str, object]:
        # Assumes emulator is paused.
        probe = self._probe()
        snapshot = self.scanner.capture_range(
            label=label,
            addr_range=FULL_RDRAM,
            chunk_size=0x100000,
            metadata={
                "automation": "mk4_capture_arcade_p1_char_grid",
                "screen_probe": probe.to_dict(),
                **(extra_meta or {}),
            },
        )
        manifest_path, payload_path = snapshot.save(self.out_dir)
        return {
            "label": label,
            "manifest": str(manifest_path),
            "payload": str(payload_path),
            "sha256": snapshot.sha256,
            "byte_len": len(snapshot.payload),
            "screen_probe": probe.to_dict(),
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate Arcade->P1->Character Select flow and capture all 15 P1 character-slot snapshots"
    )
    parser.add_argument("--socket-path", default=DEFAULT_SOCKET)
    parser.add_argument("--out-dir", default="", help="Output directory (defaults to training/data/reverse)")
    parser.add_argument("--key-delay-sec", type=float, default=0.20)
    parser.add_argument("--transition-poll-sec", type=float, default=0.12)
    parser.add_argument("--transition-timeout-sec", type=float, default=8.0)
    parser.add_argument(
        "--summary-path",
        default="",
        help="Optional explicit JSON summary path (defaults to timestamped file in out-dir)",
    )
    parser.add_argument(
        "--input-driver",
        choices=["debugger", "osascript"],
        default="debugger",
        help="How to send menu navigation keys (debugger requires patched ui-console sendkey command)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else reverse_capture_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    bridge = SocketEmulatorBridge(args.socket_path, timeout_sec=20.0)
    if args.input_driver == "debugger":
        driver = DebuggerSendKeyDriver(bridge)
    else:
        driver = AppleScriptKeyDriver(process_name="mupen64plus")
    runner = Mk4CharacterGridCapture(
        bridge=bridge,
        key_driver=driver,
        out_dir=out_dir,
        key_delay_sec=args.key_delay_sec,
        transition_poll_sec=args.transition_poll_sec,
        transition_timeout_sec=args.transition_timeout_sec,
    )

    try:
        summary = runner.run()
        summary["socket_path"] = args.socket_path
        summary["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        summary["character_rows"] = CHAR_GRID
        if args.summary_path:
            summary_path = Path(args.summary_path)
        else:
            summary_path = out_dir / f"mk4_arcade_p1_char_grid_summary_{int(time.time())}.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "summary_path": str(summary_path), "captured": len(summary["character_grid"])}, indent=2))
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
