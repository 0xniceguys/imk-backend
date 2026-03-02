from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.types import ScenarioSource, ScenarioSpec, TacticalClass  # noqa: E402


class ScenarioSpecTests(unittest.TestCase):
    def test_round_trip_dict(self) -> None:
        spec = ScenarioSpec(
            scenario_id="neutral-001",
            savestate_path=Path("/tmp/example.st"),
            tactical_class=TacticalClass.NEUTRAL_SPACING,
            source=ScenarioSource.CURATED,
            matchup="SubZero vs Scorpion",
            stage="Arena",
            tags=("neutral", "spacing"),
        )
        payload = spec.to_dict()
        again = ScenarioSpec.from_dict(json.loads(json.dumps(payload)))
        self.assertEqual(again.scenario_id, spec.scenario_id)
        self.assertEqual(again.tactical_class, spec.tactical_class)
        self.assertEqual(again.tags, spec.tags)


if __name__ == "__main__":
    unittest.main()
