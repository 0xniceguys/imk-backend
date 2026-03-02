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

from n64train.runtime.scenarios import save_scenario  # noqa: E402
from n64train.runtime.types import ScenarioSource, ScenarioSpec, TacticalClass  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a scenario manifest for savestate-centric training")
    parser.add_argument("scenario_id")
    parser.add_argument("savestate_path")
    parser.add_argument(
        "--tactical-class",
        default=TacticalClass.NEUTRAL_SPACING.value,
        choices=[x.value for x in TacticalClass],
    )
    parser.add_argument(
        "--source",
        default=ScenarioSource.CURATED.value,
        choices=[x.value for x in ScenarioSource],
    )
    parser.add_argument("--matchup", default="")
    parser.add_argument("--stage", default="")
    parser.add_argument("--side", default="P1")
    parser.add_argument("--difficulty-score", type=float, default=1.0)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    spec = ScenarioSpec(
        scenario_id=args.scenario_id,
        savestate_path=Path(args.savestate_path),
        tactical_class=TacticalClass(args.tactical_class),
        source=ScenarioSource(args.source),
        matchup=args.matchup,
        stage=args.stage,
        side=args.side,
        difficulty_score=args.difficulty_score,
        tags=tuple(args.tag),
        notes=args.notes,
    )
    path = save_scenario(spec)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
