#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.paths import PATHS  # noqa: E402
from n64train.runtime.m64p_profiles import profile_names, verify_profile_file  # noqa: E402


def _resolve_config_path(args: argparse.Namespace) -> Path:
    if args.config_path:
        return Path(args.config_path)
    if args.instance_id:
        return PATHS.local_m64p_instances_root / args.instance_id / "config" / "mupen64plus.cfg"
    return PATHS.local_m64p_root / "config" / "mupen64plus.cfg"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a Mupen64Plus config against a tracked keybinding profile")
    parser.add_argument(
        "--profile",
        required=True,
        choices=profile_names(),
        help="Profile name to verify against",
    )
    parser.add_argument(
        "--config-path",
        default="",
        help="Explicit path to mupen64plus.cfg (defaults to local base config)",
    )
    parser.add_argument(
        "--instance-id",
        default="",
        help="Check /Users/ichiropractic/code/n64/.m64p/instances/<id>/config/mupen64plus.cfg",
    )
    args = parser.parse_args()

    cfg_path = _resolve_config_path(args)
    if not cfg_path.is_file():
        parser.error(f"Config not found: {cfg_path}")

    report = verify_profile_file(cfg_path, profile_name=args.profile)
    print(json.dumps(report, indent=2))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
