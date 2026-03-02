from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.experiments.runner import ConcurrentArchitectureRunner  # noqa: E402
from n64train.runtime.types import SpeedMode  # noqa: E402


class RunnerTests(unittest.TestCase):
    def test_default_fixed_suite_plans_six_runs(self) -> None:
        runner = ConcurrentArchitectureRunner.default_fixed_suite(
            resolution="320x240",
            speed_mode=SpeedMode.TRAIN_TURBO,
            frame_budget=1000,
        )
        plans = runner.planned_launch_specs()
        self.assertEqual(len(plans), 6)
        for plan in plans:
            self.assertEqual(plan["window_mode"], "windowed")
            self.assertEqual(plan["resolution"], "320x240")
            self.assertEqual(plan["nospeedlimit"], "1")


if __name__ == "__main__":
    unittest.main()
