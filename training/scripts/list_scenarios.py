#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.scenarios import list_scenarios  # noqa: E402


def main() -> int:
    scenarios = list_scenarios()
    if not scenarios:
        print("No scenarios found in training/data/scenarios.")
        return 0
    for spec in scenarios:
        print(
            f"{spec.scenario_id}: {spec.tactical_class.value} source={spec.source.value} "
            f"matchup={spec.matchup or '-'} stage={spec.stage or '-'} savestate={spec.savestate_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
