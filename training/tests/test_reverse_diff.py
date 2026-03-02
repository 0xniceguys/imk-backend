from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.reverse.diff import diff_bytes  # noqa: E402


class ReverseDiffTests(unittest.TestCase):
    def test_diff_detects_runs(self) -> None:
        before = bytes([0, 1, 2, 3, 4, 5, 6, 7])
        after = bytes([0, 9, 2, 3, 8, 8, 6, 7])
        summary = diff_bytes(before, after)
        self.assertEqual(summary.total_bytes, 8)
        self.assertEqual(summary.changed_bytes, 3)
        self.assertEqual(len(summary.changed_runs), 2)
        self.assertEqual(summary.changed_runs[0].offset, 1)
        self.assertEqual(summary.changed_runs[0].length, 1)
        self.assertEqual(summary.changed_runs[1].offset, 4)
        self.assertEqual(summary.changed_runs[1].length, 2)

    def test_diff_produces_word_candidates(self) -> None:
        before = b"\x00\x10\x00\x20"
        after = b"\x01\x10\x00\x20"
        summary = diff_bytes(before, after)
        self.assertTrue(len(summary.word_candidates) > 0)


if __name__ == "__main__":
    unittest.main()
