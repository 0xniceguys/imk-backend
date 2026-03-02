#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.launcher import LaunchOptions, Mupen64PlusSession  # noqa: E402
from n64train.runtime.types import SpeedMode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Boot MK4 via the local Mupen64Plus wrapper")
    parser.add_argument(
        "--dummy",
        action="store_true",
        help="Experimental: try dummy gfx/audio plugins (mainly for non-visual tests)",
    )
    parser.add_argument(
        "--load-latest",
        action="store_true",
        help="Boot using the newest savestate file if available",
    )
    parser.add_argument(
        "--speed-mode",
        default=SpeedMode.DEBUG_VISIBLE.value,
        choices=[mode.value for mode in SpeedMode],
        help="Runtime speed mode (TRAIN_TURBO enables no-speed-limit)",
    )
    parser.add_argument(
        "--resolution",
        default="320x240",
        help="Window resolution (use small windows for concurrent headed runs)",
    )
    parser.add_argument(
        "--instance-id",
        default="manual-boot",
        help="Per-run instance identifier (isolates config/data directories)",
    )
    parser.add_argument(
        "--log-path",
        default="",
        help="Optional log file path for emulator stdout/stderr",
    )
    parser.add_argument(
        "--turbo-dummy-audio",
        action="store_true",
        help="In TRAIN_TURBO, switch audio plugin to dummy for extra speed",
    )
    parser.add_argument(
        "--profile",
        default="",
        help="Mupen keybinding profile name (e.g. reverse_human) applied to the isolated instance config",
    )
    args = parser.parse_args()

    options = LaunchOptions(
        headless_dummy=args.dummy,
        load_latest_state=args.load_latest,
        speed_mode=SpeedMode(args.speed_mode),
        headed=True,
        resolution=args.resolution,
        instance_id=args.instance_id,
        log_path=Path(args.log_path) if args.log_path else None,
        dummy_audio_in_turbo=args.turbo_dummy_audio,
        profile_name=args.profile or None,
    )
    session = Mupen64PlusSession(options=options)
    session.start()
    print(f"Started PID {session.process.pid}")
    print("Press Ctrl+C here or Esc in the emulator to stop.")
    try:
        return session.wait()
    except KeyboardInterrupt:
        session.stop()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
