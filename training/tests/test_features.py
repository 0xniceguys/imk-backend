from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.features import mk4_phase0_registry  # noqa: E402


class FeatureRegistryTests(unittest.TestCase):
    def test_student_feature_set_rejects_privileged(self) -> None:
        registry = mk4_phase0_registry()
        with self.assertRaises(ValueError):
            registry.validate_student_feature_set(["frame_rgb", "p1_x"])

    def test_student_feature_set_accepts_observable_only(self) -> None:
        registry = mk4_phase0_registry()
        registry.validate_student_feature_set(["frame_rgb"])


if __name__ == "__main__":
    unittest.main()
