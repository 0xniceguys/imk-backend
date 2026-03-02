from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.experiments.architectures import FLAGSHIP_ARCH_ID, fixed_architecture_suite  # noqa: E402


class ArchitectureSuiteTests(unittest.TestCase):
    def test_fixed_suite_has_six(self) -> None:
        suite = fixed_architecture_suite()
        self.assertEqual(len(suite), 6)

    def test_transformer_and_cnn_are_explicit(self) -> None:
        suite = fixed_architecture_suite()
        encoders = [spec.encoder_backbone for spec in suite]
        self.assertTrue(any("transformer" in encoder for encoder in encoders))
        self.assertTrue(any("cnn" in encoder for encoder in encoders))

    def test_flagship_present(self) -> None:
        suite = fixed_architecture_suite()
        self.assertTrue(any(spec.arch_id == FLAGSHIP_ARCH_ID and spec.flagship for spec in suite))


if __name__ == "__main__":
    unittest.main()
