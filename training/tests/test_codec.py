from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.codec import decode_reward_terms, encode_reward_terms  # noqa: E402
from n64train.runtime.types import RewardTerms  # noqa: E402


class CodecTests(unittest.TestCase):
    def test_reward_terms_roundtrip_preserves_all_fields(self) -> None:
        reward = RewardTerms(
            round_win=1.0,
            damage_dealt=2.0,
            damage_taken=-3.0,
            hit_confirm_bonus=4.0,
            block_success_bonus=5.0,
            whiff_punished_penalty=-6.0,
            idle_timeout_penalty=-7.0,
            illegal_state_penalty=-8.0,
            win_bonus=9.0,
            loss_penalty=-10.0,
            approach_reward=11.0,
            distance_penalty=-12.0,
            survival=13.0,
            spam_penalty=-14.0,
            extras={"x": 15.0},
        )
        payload = encode_reward_terms(reward)
        decoded = decode_reward_terms(payload)
        self.assertEqual(decoded, reward)


if __name__ == "__main__":
    unittest.main()
