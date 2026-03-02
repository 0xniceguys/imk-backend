from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.envs.mk4_lowlevel import MK4EnvConfig, MK4LowLevelEnv  # noqa: E402
from n64train.runtime.actions import ControllerState, MacroAction  # noqa: E402
from n64train.runtime.types import ActionPacket, SpeedMode  # noqa: E402


class EnvApiTests(unittest.TestCase):
    def test_stub_step_updates_budget_and_frame(self) -> None:
        env = MK4LowLevelEnv(
            MK4EnvConfig(
                allow_stub_steps=True,
                max_env_frames=10,
                speed_mode=SpeedMode.TRAIN_TURBO,
                instance_id="test-env",
            )
        )
        obs = env.reset()
        self.assertEqual(obs.timing.emulator_frame_id, 0)

        result = env.step(
            ActionPacket(
                macro_action=MacroAction.ADVANCE,
                micro_controller_state=ControllerState(),
                repeat_frames=3,
            )
        )
        self.assertEqual(result.observation.timing.emulator_frame_id, 3)
        self.assertEqual(env.get_budget_counters()["training"], 3)
        self.assertTrue(result.info["stub_step"])


if __name__ == "__main__":
    unittest.main()
