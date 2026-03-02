#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.experiments.architectures import FLAGSHIP_ARCH_ID, fixed_architecture_suite  # noqa: E402


def main() -> int:
    for spec in fixed_architecture_suite():
        flags = []
        if spec.flagship or spec.arch_id == FLAGSHIP_ARCH_ID:
            flags.append("FLAGSHIP")
        if spec.encoder_backbone.startswith("transformer"):
            flags.append("TRANSFORMER")
        if "cnn" in spec.encoder_backbone:
            flags.append("CNN")
        print(f"{spec.arch_id}: {spec.display_name}")
        print(f"  encoder={spec.encoder_backbone} world_model={spec.world_model} hierarchical={spec.hierarchical} planner={spec.planner}")
        if flags:
            print(f"  flags={','.join(flags)}")
        print(f"  notes={spec.notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
