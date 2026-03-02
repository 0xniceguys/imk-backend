#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.features import mk4_phase0_registry  # noqa: E402


def main() -> int:
    registry = mk4_phase0_registry()
    print(f"schema_version={registry.schema_version}")
    for spec in registry.all():
        print(
            f"{spec.name}: source={spec.source.value} privilege={spec.privilege_level.value} "
            f"dtype={spec.dtype} shape={spec.shape} norm={spec.normalization}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
