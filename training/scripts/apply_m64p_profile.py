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

from n64train.runtime.m64p_profiles import apply_profile_to_file, profile_names  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a tracked Mupen64Plus keybinding profile")
    parser.add_argument(
        "--profile",
        required=True,
        choices=profile_names(),
        help="Profile name to apply",
    )
    parser.add_argument(
        "--base-cfg",
        required=True,
        help="Base mupen64plus.cfg to read",
    )
    parser.add_argument(
        "--out-cfg",
        required=True,
        help="Output mupen64plus.cfg path (can be same as --base-cfg)",
    )
    args = parser.parse_args()

    base_cfg = Path(args.base_cfg)
    out_cfg = Path(args.out_cfg)
    if not base_cfg.is_file():
        parser.error(f"--base-cfg not found: {base_cfg}")

    report = apply_profile_to_file(base_cfg=base_cfg, out_cfg=out_cfg, profile_name=args.profile)
    print(json.dumps(report, indent=2))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
