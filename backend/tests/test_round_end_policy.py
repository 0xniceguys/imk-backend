import unittest

from app.services.game_state import FightState, read_fight_state
from app.services.match_runner import apply_round_end_policy


class _BadContractMissingP1Bridge:
    def __init__(self) -> None:
        self.outputs = {
            "mem /1w 0x80126f54": "run\r\n(dbg)\n",
            "mem /1w 0x800fe0d8": "800FE0D8:  00008000\n(dbg)\n",
            "mem /1b 0x8010511b": "8010511B:  56\n(dbg)\n",
            "mem /1w 0x80105118": "80105118:  00000056\n(dbg)\n",
            "mem /1w 0x800f87f8": "800F87F8:  FFFE0000\n(dbg)\n",
            "mem /1w 0x8006a060": "8006A060:  00030000\n(dbg)\n",
            "mem /1w 0x800fe0f8": "800FE0F8:  00000004\n(dbg)\n",
            "mem /1w 0x80126f78": "80126F78:  00000000\n(dbg)\n",
            "mem /1w 0x800fe90c": "800FE90C:  00000738\n(dbg)\n",
        }

    def debugger_command(self, command, **kwargs):
        _ = kwargs
        return {"output": self.outputs[command]}

    def get_ram_features(self):
        return {
            "mk4_state_payload": {
                "version": "mk4_core_v1",
                "available": True,
                "frame_id": 0,
                "p1_health_word": 2916,
                "p2_health_word": 65536,
                "p1_health": 5,
                "p2_health": 160,
                "timer": 0,
                "timer_raw": 0,
                "p1_x": 0.0,
                "p2_x": -2.0,
                "p1_airborne": 0.0,
                "p2_airborne": 0.0,
                "p1_y_vel": 0.0,
                "p1_facing": -1,
                "p2_facing": 1,
            }
        }


class RoundEndPolicyTests(unittest.TestCase):
    def test_p2_ko_requires_five_consecutive_samples(self) -> None:
        ko_streaks = {"p1_ko": 0, "p2_ko": 0, "double_ko": 0}
        state = FightState(p1_health=120, p2_health=0, timer=40)

        for expected_streak in range(1, 5):
            round_done, winner_p1, reason, ko_streaks = apply_round_end_policy(
                state,
                sample_flags=[],
                round_done=True,
                round_over_reason="p2_ko",
                ko_streaks=ko_streaks,
            )
            self.assertFalse(round_done)
            self.assertFalse(winner_p1)
            self.assertIsNone(reason)
            self.assertEqual(ko_streaks["p2_ko"], expected_streak)

        round_done, winner_p1, reason, ko_streaks = apply_round_end_policy(
            state,
            sample_flags=[],
            round_done=True,
            round_over_reason="p2_ko",
            ko_streaks=ko_streaks,
        )
        self.assertTrue(round_done)
        self.assertTrue(winner_p1)
        self.assertEqual(reason, "p2_ko")
        self.assertEqual(ko_streaks["p2_ko"], 5)

    def test_non_ko_sample_resets_ko_streaks(self) -> None:
        ko_streaks = {"p1_ko": 0, "p2_ko": 2, "double_ko": 0}
        state = FightState(p1_health=120, p2_health=60, timer=40)

        round_done, winner_p1, reason, ko_streaks = apply_round_end_policy(
            state,
            sample_flags=[],
            round_done=False,
            round_over_reason=None,
            ko_streaks=ko_streaks,
        )
        self.assertFalse(round_done)
        self.assertFalse(winner_p1)
        self.assertIsNone(reason)
        self.assertEqual(ko_streaks, {"p1_ko": 0, "p2_ko": 0, "double_ko": 0})

    def test_read_fight_state_uses_previous_good_health_when_direct_core_is_missing(self) -> None:
        previous_state = FightState(
            frame_id=6,
            p1_health=160,
            p2_health=160,
            timer=87,
            p1_x=-2.0,
            p2_x=3.0,
        )

        state = read_fight_state(
            _BadContractMissingP1Bridge(),
            frame_id=7,
            previous_state=previous_state,
        )

        self.assertEqual(state.p1_health, 160)
        self.assertEqual(state.p2_health, 80)
        self.assertEqual(state.timer, 0x56)
        self.assertEqual(state.debug_info["state_source"], "merged")
        self.assertEqual(state.debug_info["source_map"]["p1_health"], "previous")
        self.assertEqual(state.debug_info["source_map"]["p2_health"], "direct")
        self.assertFalse(state.debug_info["contract_core_trusted"])


if __name__ == "__main__":
    unittest.main()
