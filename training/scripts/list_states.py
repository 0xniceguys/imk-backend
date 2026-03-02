#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.savestates import list_savestates  # noqa: E402


def main() -> int:
    states = list_savestates()
    if not states:
        print("No savestates found.")
        return 0
    for state in states:
        print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
