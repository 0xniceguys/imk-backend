#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def _add_paths() -> None:
    script_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(script_dir.parent / "src"))
    sys.path.insert(0, str(script_dir))


_add_paths()

from mk4_capture_arcade_p1_char_grid import (  # noqa: E402
    AppleScriptKeyDriver,
    CGEventKeyDriver,
    CHAR_GRID,
    DebuggerSendKeyDriver,
    KEY_DOWN,
    KEY_LEFT,
    KEY_LEFT_SHIFT,
    KEY_RIGHT,
    KEY_RETURN,
    Mk4CharacterGridCapture,
    ScreenProbe,
)
from n64train.paths import PATHS  # noqa: E402
from n64train.reverse.mk4_findings import Mk4MenuScreenState  # noqa: E402
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402
from n64train.runtime.frame_capture import ScreenshotPollFrameCapture  # noqa: E402
from n64train.reverse.scanner import reverse_capture_dir  # noqa: E402


DEFAULT_SOCKET = "/Users/ichiropractic/code/n64/training/data/bridge/mk4-visible.sock"
KNOWN_MENU_PRIMARY_STATES = {
    int(Mk4MenuScreenState.INTRO_ATTRACT),
    int(Mk4MenuScreenState.TOP_LEVEL_MAIN_MENU),
    int(Mk4MenuScreenState.ARCADE_RUMBLE_WARNING),
    int(Mk4MenuScreenState.OPTIONS_MENU),
    int(Mk4MenuScreenState.CHARACTER_SELECT),
}


def _slug(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace(".", "")
    )


def _character_grid_index() -> dict[str, tuple[int, int, str]]:
    out: dict[str, tuple[int, int, str]] = {}
    for row_idx, row in enumerate(CHAR_GRID):
        for col_idx, name in enumerate(row):
            aliases = {_slug(name)}
            if name.lower() == "sub-zero":
                aliases.add("subzero")
            if name.lower() == "johnny cage":
                aliases.add("johnny")
            if name.lower() == "liu kang":
                aliases.add("liukang")
            for key in aliases:
                out[key] = (row_idx, col_idx, name)
    return out


CHAR_INDEX = _character_grid_index()


@dataclass(frozen=True)
class BuilderConfig:
    socket_path: str
    instance_id: str
    bank_dir: Path
    base_savestate_path: Path
    round_start_offset_frames: int
    ladder_right_presses: int
    variants_per_character: int
    pre_select_jitter_max_frames: int
    pre_ladder_jitter_max_frames: int
    pre_save_jitter_max_frames: int
    continue_on_error: bool
    leave_running: bool
    checkpoint_screenshots: bool
    input_driver: str = "cgevent"  # "cgevent" (Quartz), "osascript", or "debugger"


class Mk4ArcadeRoundStartBankBuilder:
    def __init__(self, cfg: BuilderConfig, *, rng: random.Random) -> None:
        self.cfg = cfg
        self.rng = rng
        self.bridge = SocketEmulatorBridge(cfg.socket_path, timeout_sec=30.0)
        if cfg.input_driver == "osascript":
            self.driver = AppleScriptKeyDriver(process_name="mupen64plus")
        elif cfg.input_driver == "cgevent":
            self.driver = CGEventKeyDriver(process_name="mupen64plus")
        else:
            self.driver = DebuggerSendKeyDriver(self.bridge)
        self.screenshot_capture = ScreenshotPollFrameCapture(instance_id=cfg.instance_id)
        self.checkpoint_dir = cfg.bank_dir / "checkpoints"
        # Real OS key drivers need slightly more generous timing (window round-trip).
        key_delay = 0.25 if cfg.input_driver in {"osascript", "cgevent"} else 0.12
        transition_timeout = 20.0 if cfg.input_driver in {"osascript", "cgevent"} else 15.0
        self.flow = Mk4CharacterGridCapture(
            bridge=self.bridge,
            key_driver=self.driver,
            out_dir=reverse_capture_dir(),
            key_delay_sec=key_delay,
            transition_poll_sec=0.10,
            transition_timeout_sec=transition_timeout,
        )

    def close(self) -> None:
        try:
            if self.cfg.leave_running:
                self.flow._resume()
            else:
                self.flow._pause()
        except Exception:
            pass
        self.bridge.close()

    def build_bank(
        self,
        *,
        characters: list[tuple[int, int, str]],
    ) -> dict[str, Any]:
        self.cfg.bank_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.base_savestate_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "type": "mk4_arcade_p1_round_start_savestate_bank",
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "socket_path": self.cfg.socket_path,
            "flow": {
                "mode": "Arcade -> warning skip -> 1P -> character -> ladder max -> fight -> savestate",
                "round_start_detection": "heuristic_non_menu_transition_plus_frame_offset",
                "round_start_offset_frames": self.cfg.round_start_offset_frames,
                "ladder_right_presses": self.cfg.ladder_right_presses,
            },
            "variation": {
                "variants_per_character": self.cfg.variants_per_character,
                "pre_select_jitter_max_frames": self.cfg.pre_select_jitter_max_frames,
                "pre_ladder_jitter_max_frames": self.cfg.pre_ladder_jitter_max_frames,
                "pre_save_jitter_max_frames": self.cfg.pre_save_jitter_max_frames,
            },
            "base_savestate": str(self.cfg.base_savestate_path),
            "checkpoint_screenshots_enabled": self.cfg.checkpoint_screenshots,
            "entries": [],
            "errors": [],
        }

        base_probe = self._ensure_base_charselect_savestate()
        manifest["base_char_select_probe"] = base_probe.to_dict()

        for row_idx, col_idx, character_name in characters:
            for variant_idx in range(1, self.cfg.variants_per_character + 1):
                try:
                    entry = self._build_character_variant(row_idx, col_idx, character_name, variant_idx)
                    manifest["entries"].append(entry)
                except Exception as exc:
                    error_payload = {
                        "character_name": character_name,
                        "row": row_idx + 1,
                        "col": col_idx + 1,
                        "variant": variant_idx,
                        "error": str(exc),
                    }
                    manifest["errors"].append(error_payload)
                    if not self.cfg.continue_on_error:
                        raise
        return manifest

    def _ensure_base_charselect_savestate(self) -> ScreenProbe:
        self.flow._ensure_bridge_alive()
        self.flow._pause()
        probe = self.flow._probe()
        if int(probe.primary_state) != int(Mk4MenuScreenState.CHARACTER_SELECT):
            self._go_to_character_select_from_anywhere()
            self.flow._pause()
            probe = self.flow._probe()
        if int(probe.primary_state) != int(Mk4MenuScreenState.CHARACTER_SELECT):
            raise RuntimeError(f"Expected Character Select (38), got probe={probe.to_dict()}")
        self._checkpoint(
            "character_select_ready",
            expect=lambda p: int(p.primary_state) == int(Mk4MenuScreenState.CHARACTER_SELECT),
            expect_desc="Character Select (state=38)",
        )
        self._save_state(self.cfg.base_savestate_path)
        return probe

    def _go_to_character_select_from_anywhere(self) -> None:
        self.flow._pause()
        start_probe = self.flow._probe()
        if int(start_probe.primary_state) == int(Mk4MenuScreenState.CHARACTER_SELECT):
            return
        if int(start_probe.primary_state) == int(Mk4MenuScreenState.ARCADE_RUMBLE_WARNING):
            self._checkpoint(
                "arcade_rumble_warning_existing",
                expect=lambda p: int(p.primary_state) == int(Mk4MenuScreenState.ARCADE_RUMBLE_WARNING),
                expect_desc="Arcade Rumble Warning (state=10)",
            )
            self.flow._resume()
            self.flow._press_and_settle(KEY_RETURN)
            arcade_next_probe = self.flow._wait_for(self.flow._is_arcade_player_count_screen, "Arcade 1P/2P screen")
            if self.flow._is_arcade_player_count_screen_from_probe(arcade_next_probe):
                self._checkpoint(
                    "arcade_player_count_existing_from_warning",
                    expect=self.flow._is_arcade_player_count_screen_from_probe,
                    expect_desc="Arcade 1P/2P screen",
                )
                self.flow._ensure_player_count_cursor(0)
                self.flow._resume()
                self.flow._press_and_settle(KEY_LEFT_SHIFT)
                try:
                    self.flow._wait_for_character_select()
                except Exception:
                    # Some builds may accept Start instead of A here.
                    self.flow._resume()
                    self.flow._press_and_settle(KEY_RETURN)
                    self.flow._wait_for_character_select()
                return
        if self.flow._is_arcade_player_count_screen_from_probe(start_probe):
            self._checkpoint(
                "arcade_player_count_existing",
                expect=self.flow._is_arcade_player_count_screen_from_probe,
                expect_desc="Arcade 1P/2P screen",
            )
            self.flow._ensure_player_count_cursor(0)
            self.flow._resume()
            self.flow._press_and_settle(KEY_LEFT_SHIFT)
            try:
                self.flow._wait_for_character_select()
            except Exception:
                self.flow._resume()
                self.flow._press_and_settle(KEY_RETURN)
                self.flow._wait_for_character_select()
            return

        self.flow._recover_to_top_main_arcade()
        self._checkpoint(
            "top_main_arcade_ready",
            expect=lambda p: int(p.primary_state) == int(Mk4MenuScreenState.TOP_LEVEL_MAIN_MENU) and int(p.shared_cursor) == 0,
            expect_desc="Top-Level Main Menu with Arcade highlighted",
        )
        self.flow._menu_confirm_with_fallback()
        arcade_next_probe = self._wait_for_arcade_post_select_screen()

        if int(arcade_next_probe.primary_state) == int(Mk4MenuScreenState.ARCADE_RUMBLE_WARNING):
            self._checkpoint(
                "arcade_rumble_warning",
                expect=lambda p: int(p.primary_state) == int(Mk4MenuScreenState.ARCADE_RUMBLE_WARNING),
                expect_desc="Arcade Rumble Warning (state=10)",
            )
            self.flow._resume()
            self.flow._press_and_settle(KEY_RETURN)
            arcade_next_probe = self.flow._wait_for(self.flow._is_arcade_player_count_screen, "Arcade 1P/2P screen")

        if self.flow._is_arcade_player_count_screen_from_probe(arcade_next_probe):
            self._checkpoint(
                "arcade_player_count",
                expect=self.flow._is_arcade_player_count_screen_from_probe,
                expect_desc="Arcade 1P/2P screen",
            )
            self.flow._ensure_player_count_cursor(0)
            self.flow._resume()
            self.flow._press_and_settle(KEY_LEFT_SHIFT)
            try:
                self.flow._wait_for_character_select()
            except Exception:
                # Fallback for builds/configs where Start confirms instead of A.
                self.flow._resume()
                self.flow._press_and_settle(KEY_RETURN)
                self.flow._wait_for_character_select()
            return

        if int(arcade_next_probe.primary_state) == int(Mk4MenuScreenState.CHARACTER_SELECT):
            return

        raise RuntimeError(f"Unexpected Arcade flow screen after selecting Arcade: {arcade_next_probe.to_dict()}")

    def _build_character_variant(
        self,
        row_idx: int,
        col_idx: int,
        character_name: str,
        variant_idx: int,
    ) -> dict[str, Any]:
        self._load_state(self.cfg.base_savestate_path)
        self.flow._pause()
        base_probe = self.flow._probe()
        if int(base_probe.primary_state) != int(Mk4MenuScreenState.CHARACTER_SELECT):
            raise RuntimeError(f"Base savestate did not load to Character Select: {base_probe.to_dict()}")
        base_checkpoint = self._checkpoint(
            f"{_slug(character_name)}_charselect_base_variant{variant_idx:02d}",
            expect=lambda p: int(p.primary_state) == int(Mk4MenuScreenState.CHARACTER_SELECT),
            expect_desc="Character Select (state=38)",
        )

        pre_select_jitter = self._maybe_jitter(self.cfg.pre_select_jitter_max_frames)
        self._move_char_select_cursor_to(row_idx=row_idx, col_idx=col_idx)
        self.flow._menu_confirm_with_fallback()
        ladder_probe = self._wait_for_ladder_screen()
        ladder_checkpoint = self._checkpoint(
            f"{_slug(character_name)}_ladder_variant{variant_idx:02d}",
            expect=lambda p: int(p.primary_state) not in KNOWN_MENU_PRIMARY_STATES,
            expect_desc="Arcade difficulty ladder (non-menu primary state)",
        )

        pre_ladder_jitter = self._maybe_jitter(self.cfg.pre_ladder_jitter_max_frames)
        for _ in range(self.cfg.ladder_right_presses):
            self.flow._press_and_settle(KEY_RIGHT)
        self.flow._menu_confirm_with_fallback()

        first_post_ladder_probe = self._wait_for_not_probe(ladder_probe, desc="post-ladder transition")
        pre_save_jitter = self._maybe_jitter(self.cfg.pre_save_jitter_max_frames)
        self.flow._advance_frames(max(1, self.cfg.round_start_offset_frames))
        self.flow._pause()
        save_probe = self.flow._probe()
        save_checkpoint = self._checkpoint(
            f"{_slug(character_name)}_roundstart_candidate_variant{variant_idx:02d}",
            expect=lambda p: int(p.primary_state) not in KNOWN_MENU_PRIMARY_STATES,
            expect_desc="In-fight / non-menu state (round-start candidate)",
        )

        state_path = self._round_start_state_path(character_name, variant_idx)
        self._save_state(state_path)

        return {
            "character_name": character_name,
            "row": row_idx + 1,
            "col": col_idx + 1,
            "variant": variant_idx,
            "savestate_path": str(state_path),
            "checkpoints": {
                "char_select_probe": base_probe.to_dict(),
                "ladder_probe": ladder_probe.to_dict(),
                "first_post_ladder_probe": first_post_ladder_probe.to_dict(),
                "save_probe": save_probe.to_dict(),
            },
            "checkpoint_screens": {
                "char_select_base": base_checkpoint,
                "ladder_screen": ladder_checkpoint,
                "round_start_candidate": save_checkpoint,
            },
            "heuristic": {
                "round_start_offset_frames": self.cfg.round_start_offset_frames,
                "ladder_right_presses": self.cfg.ladder_right_presses,
                "pre_select_jitter_frames": pre_select_jitter,
                "pre_ladder_jitter_frames": pre_ladder_jitter,
                "pre_save_jitter_frames": pre_save_jitter,
            },
        }

    def _move_char_select_cursor_to(self, *, row_idx: int, col_idx: int) -> None:
        for _ in range(max(0, int(row_idx))):
            self.flow._press_and_settle(KEY_DOWN)
        for _ in range(max(0, int(col_idx))):
            self.flow._press_and_settle(KEY_RIGHT)

    def _wait_for_ladder_screen(self) -> ScreenProbe:
        deadline = time.time() + 20.0
        last_probe: ScreenProbe | None = None
        stable_count = 0
        while time.time() < deadline:
            self.flow._pause()
            probe = self.flow._probe()
            if int(probe.primary_state) == int(Mk4MenuScreenState.CHARACTER_SELECT):
                self.flow._resume()
                time.sleep(0.10)
                continue

            if probe == last_probe:
                stable_count += 1
            else:
                stable_count = 1
                last_probe = probe

            if int(probe.primary_state) not in KNOWN_MENU_PRIMARY_STATES and 0 <= int(probe.shared_cursor) <= 6:
                return probe
            if stable_count >= 3 and int(probe.primary_state) not in KNOWN_MENU_PRIMARY_STATES:
                return probe

            self.flow._resume()
            time.sleep(0.10)
        raise RuntimeError("Timed out waiting for Arcade difficulty ladder screen")

    def _wait_for_arcade_post_select_screen(self) -> ScreenProbe:
        _retried = False
        deadline = time.time() + 25.0
        while time.time() < deadline:
            self.flow._pause()
            probe = self.flow._probe()

            if int(probe.primary_state) == int(Mk4MenuScreenState.ARCADE_RUMBLE_WARNING):
                return probe
            if self.flow._is_arcade_player_count_screen_from_probe(probe):
                return probe
            if int(probe.primary_state) == int(Mk4MenuScreenState.CHARACTER_SELECT):
                return probe

            if int(probe.primary_state) == int(Mk4MenuScreenState.TOP_LEVEL_MAIN_MENU):
                if not _retried and time.time() > deadline - 12.0:
                    # One-time retry: re-confirm Arcade from top menu.
                    _retried = True
                    self.flow._resume()
                    self.flow._menu_confirm_with_fallback()
                    continue
                self.flow._resume()
                time.sleep(0.15)
                continue

            self.flow._resume()
            time.sleep(0.15)
        raise RuntimeError("Timed out waiting for Arcade post-select screen (warning/1P-2P/char-select)")

    def _wait_for_not_probe(self, baseline: ScreenProbe, *, desc: str) -> ScreenProbe:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            self.flow._pause()
            probe = self.flow._probe()
            if probe != baseline:
                return probe
            self.flow._resume()
            time.sleep(0.10)
        raise RuntimeError(f"Timed out waiting for {desc}")

    def _checkpoint(
        self,
        label: str,
        *,
        expect: Callable[[ScreenProbe], bool],
        expect_desc: str,
    ) -> dict[str, Any]:
        self.flow._pause()
        probe = self.flow._probe()
        valid = bool(expect(probe))
        if not valid:
            raise RuntimeError(
                f"Checkpoint validation failed for {label}: expected {expect_desc}; got probe={probe.to_dict()}"
            )
        screenshot = self._capture_checkpoint_screenshot(label)
        payload = {
            "label": label,
            "validated": True,
            "expectation": expect_desc,
            "screen_probe": probe.to_dict(),
            "screenshot": screenshot,
        }
        print(json.dumps({"checkpoint": label, "probe": probe.to_dict(), "screenshot": screenshot}, indent=2), flush=True)
        return payload

    def _capture_checkpoint_screenshot(self, label: str) -> dict[str, Any]:
        if not self.cfg.checkpoint_screenshots:
            return {"enabled": False}

        before = self._latest_screenshot_file_info()
        before_id = None if before is None else (before["path"], before["mtime_ns"])
        resp = self.bridge.debugger_command("screenshot", timeout_sec=5.0, output_tail_chars=2000)
        if "M64P_SCREENSHOT_OK" not in str(resp.get("output", "")):
            raise RuntimeError(f"Failed to queue screenshot at checkpoint {label}: {resp}")

        # Let the queued screenshot flush on rendered frames.
        self.flow._advance_frames(4)
        self.flow._pause()

        deadline = time.time() + 5.0
        latest = before
        while time.time() < deadline:
            latest = self._latest_screenshot_file_info()
            after_id = None if latest is None else (latest["path"], latest["mtime_ns"])
            if latest is not None and after_id != before_id:
                src = Path(str(latest["path"]))
                dst = self.checkpoint_dir / f"{int(time.time()*1000)}_{_slug(label)}{src.suffix.lower() or '.png'}"
                shutil.copy2(src, dst)
                return {
                    "enabled": True,
                    "source_path": str(src),
                    "copied_path": str(dst),
                    "frame_shape": list(latest.get("frame_shape") or []) or None,
                    "stale": False,
                }
            time.sleep(0.05)

        raise RuntimeError(f"Timed out waiting for screenshot for checkpoint {label}")

    def _latest_screenshot_file_info(self) -> dict[str, Any] | None:
        candidates: list[Path] = []
        inst_dir = PATHS.local_m64p_instances_root / self.cfg.instance_id / "data" / "screenshots"
        global_dir = PATHS.screenshot_dir
        for d in (inst_dir, global_dir):
            if not d.exists():
                continue
            try:
                candidates.extend([p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".png"])
            except OSError:
                continue
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime_ns if hasattr(p.stat(), "st_mtime_ns") else p.stat().st_mtime)
        st = latest.stat()
        payload = latest.read_bytes()
        shape = self.screenshot_capture._png_shape(payload)
        return {
            "path": str(latest),
            "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
            "frame_shape": shape,
        }

    def _maybe_jitter(self, max_frames: int) -> int:
        max_frames = max(0, int(max_frames))
        if max_frames <= 0:
            return 0
        frames = self.rng.randint(0, max_frames)
        if frames > 0:
            self.flow._advance_frames(frames)
        return frames

    def _save_state(self, path: Path) -> None:
        self.flow._pause()
        resp = self.bridge.save_savestate_path(path)
        if not bool(resp.get("saved", False)):
            raise RuntimeError(f"Savestate save failed for {path}: {resp}")
        if not path.exists():
            raise RuntimeError(f"Savestate save reported success but file not found: {path}")

    def _load_state(self, path: Path) -> None:
        resp = self.bridge.load_savestate_path(path)
        if not bool(resp.get("loaded", False)):
            raise RuntimeError(f"Savestate load failed for {path}: {resp}")
        # Give the emulator a couple of frames to settle after state load.
        self.flow._pause()
        self.flow._advance_frames(2)
        self.flow._pause()

    def _round_start_state_path(self, character_name: str, variant_idx: int) -> Path:
        stem = f"mk4_arcade_p1_{_slug(character_name)}_ladder_max_round_start_v{variant_idx:02d}.st"
        return self.cfg.bank_dir / stem


def _parse_characters(args: argparse.Namespace) -> list[tuple[int, int, str]]:
    if args.characters:
        values = [v.strip() for v in args.characters.split(",") if v.strip()]
        out: list[tuple[int, int, str]] = []
        for raw in values:
            key = _slug(raw)
            if key not in CHAR_INDEX:
                raise SystemExit(f"Unknown character name: {raw!r}")
            out.append(CHAR_INDEX[key])
        return out
    out = []
    for row_idx, row in enumerate(CHAR_GRID):
        for col_idx, name in enumerate(row):
            out.append((row_idx, col_idx, name))
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an Arcade P1 round-start emulator savestate bank. "
            "Flow: boot -> Arcade -> warning skip -> 1P -> character select -> ladder max -> match intro -> save state."
        )
    )
    parser.add_argument("--socket-path", default=DEFAULT_SOCKET)
    parser.add_argument("--instance-id", default="reverse-visible", help="Mupen instance ID for screenshot polling")
    parser.add_argument(
        "--bank-dir",
        default=str(PATHS.training_data_root / "savestates" / "mk4_arcade_p1_round_start_bank"),
    )
    parser.add_argument(
        "--base-savestate-path",
        default="",
        help="Optional explicit path for the reusable Character Select base savestate",
    )
    parser.add_argument(
        "--characters",
        default="",
        help="Comma-separated subset (default: all 15). Example: 'Kai,Scorpion,Sub-Zero'",
    )
    parser.add_argument("--variants-per-character", type=int, default=1)
    parser.add_argument("--rng-seed", type=int, default=0)
    parser.add_argument("--ladder-right-presses", type=int, default=5)
    parser.add_argument(
        "--round-start-offset-frames",
        type=int,
        default=900,
        help="Heuristic frame delay after leaving the ladder screen before saving the round-start state candidate",
    )
    parser.add_argument("--pre-select-jitter-max-frames", type=int, default=0)
    parser.add_argument("--pre-ladder-jitter-max-frames", type=int, default=0)
    parser.add_argument("--pre-save-jitter-max-frames", type=int, default=0)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--no-checkpoint-screenshots",
        dest="checkpoint_screenshots",
        action="store_false",
        help="Disable checkpoint screenshot capture/validation (default: enabled)",
    )
    parser.add_argument(
        "--input-driver",
        choices=["cgevent", "osascript", "debugger"],
        default="cgevent",
        help="Key injection driver: 'cgevent' (Quartz CGEventPost, default), 'osascript' (System Events), or 'debugger' (bridge path)",
    )
    parser.add_argument(
        "--leave-running",
        action="store_true",
        help="Leave emulator running at the end (default: leave paused)",
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="Optional explicit manifest path (JSON). Defaults to timestamped file in bank-dir.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    bank_dir = Path(args.bank_dir)
    base_savestate_path = (
        Path(args.base_savestate_path)
        if args.base_savestate_path
        else bank_dir / "mk4_arcade_p1_character_select_base.st"
    )
    seed = args.rng_seed if args.rng_seed != 0 else int(time.time())
    rng = random.Random(seed)
    characters = _parse_characters(args)

    cfg = BuilderConfig(
        socket_path=args.socket_path,
        bank_dir=bank_dir,
        base_savestate_path=base_savestate_path,
        round_start_offset_frames=max(1, int(args.round_start_offset_frames)),
        ladder_right_presses=max(0, int(args.ladder_right_presses)),
        variants_per_character=max(1, int(args.variants_per_character)),
        pre_select_jitter_max_frames=max(0, int(args.pre_select_jitter_max_frames)),
        pre_ladder_jitter_max_frames=max(0, int(args.pre_ladder_jitter_max_frames)),
        pre_save_jitter_max_frames=max(0, int(args.pre_save_jitter_max_frames)),
        continue_on_error=bool(args.continue_on_error),
        leave_running=bool(args.leave_running),
        checkpoint_screenshots=bool(getattr(args, "checkpoint_screenshots", True)),
        input_driver=str(getattr(args, "input_driver", "osascript")),
        instance_id=str(args.instance_id),
    )

    builder = Mk4ArcadeRoundStartBankBuilder(cfg, rng=rng)
    try:
        manifest = builder.build_bank(characters=characters)
        manifest["rng_seed"] = seed
        manifest["character_rows"] = CHAR_GRID
        manifest["selected_characters"] = [name for _, _, name in characters]
        if args.summary_path:
            summary_path = Path(args.summary_path)
        else:
            summary_path = bank_dir / f"mk4_arcade_p1_round_start_bank_{int(time.time())}.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "summary_path": str(summary_path),
                    "bank_dir": str(bank_dir),
                    "saved_states": len(manifest.get("entries", [])),
                    "errors": len(manifest.get("errors", [])),
                    "note": (
                        "Round-start is currently a heuristic (ladder->match transition plus frame offset). "
                        "Tune --round-start-offset-frames if needed."
                    ),
                },
                indent=2,
            )
        )
    finally:
        builder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
