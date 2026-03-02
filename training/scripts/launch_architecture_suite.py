#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.experiments.runner import ConcurrentArchitectureRunner  # noqa: E402
from n64train.runtime.types import SpeedMode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the fixed 6-architecture MK4 emulator suite")
    parser.add_argument("--dry-run", action="store_true", help="Print launch specs without starting emulators")
    parser.add_argument(
        "--speed-mode",
        default=SpeedMode.TRAIN_TURBO.value,
        choices=[mode.value for mode in SpeedMode],
        help="Launcher speed mode (turbo recommended for training runs)",
    )
    parser.add_argument("--resolution", default="320x240", help="Window resolution for each headed run")
    parser.add_argument(
        "--frame-budget",
        type=int,
        default=50_000,
        help="Per-architecture env-frame budget used by the runner bookkeeping",
    )
    parser.add_argument(
        "--run-seconds",
        type=float,
        default=0.0,
        help="If > 0, keep launched emulators alive for this many seconds, then stop them",
    )
    args = parser.parse_args()

    runner = ConcurrentArchitectureRunner.default_fixed_suite(
        resolution=args.resolution,
        speed_mode=SpeedMode(args.speed_mode),
        frame_budget=args.frame_budget,
    )

    if args.dry_run:
        print(json.dumps(runner.planned_launch_specs(), indent=2))
        return 0

    runner.launch_all()
    print(json.dumps(runner.status(), indent=2))
    try:
        if args.run_seconds > 0:
            runner.wait_for(args.run_seconds)
        else:
            print("Suite running. Press Ctrl+C to stop.")
            while True:
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping all runs...")
    finally:
        runner.stop_all()
        print(json.dumps(runner.status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
