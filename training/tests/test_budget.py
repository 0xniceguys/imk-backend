from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.budget import BudgetExceededError, ExperimentBudget, FrameCategory  # noqa: E402


class BudgetTests(unittest.TestCase):
    def test_counts_all_categories(self) -> None:
        budget = ExperimentBudget(max_env_frames=20)
        budget.record(FrameCategory.TRAINING, 5)
        budget.record(FrameCategory.EVALUATION, 3)
        budget.record(FrameCategory.SCENARIO_BRANCH, 2)
        self.assertEqual(budget.total_env_frames(), 10)
        snap = budget.snapshot()
        self.assertEqual(snap["training"], 5)
        self.assertEqual(snap["evaluation"], 3)
        self.assertEqual(snap["scenario_branch"], 2)

    def test_budget_exceeded_raises(self) -> None:
        budget = ExperimentBudget(max_env_frames=3)
        budget.record(FrameCategory.TRAINING, 3)
        with self.assertRaises(BudgetExceededError):
            budget.record(FrameCategory.EVALUATION, 1)


if __name__ == "__main__":
    unittest.main()
