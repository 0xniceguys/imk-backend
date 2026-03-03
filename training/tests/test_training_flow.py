"""
test_training_flow.py — End-to-end audit of the full MK4 training pipeline.

Tests (no emulator required):
  A. Button mapping  — every MacroAction produces correct button bitmask
  B. Obs builder     — normalisation, clipping, facing sign
  C. Reward extractor — damage, spam, approach, win/loss, edge cases
  D. mk4_train single-path — recurrent agent record() called correctly
  E. Learner _update — PPO buffers correctly injected for all 4 PPO agents
  F. Frame-stack     — correct dimensions, padding, ordering
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

import unittest
import struct

from n64train.runtime.actions import Button, ControllerState, MacroAction


# ─────────────────────────────────────────────────────────
# A. Button mapping
# ─────────────────────────────────────────────────────────

class TestButtonMapping(unittest.TestCase):
    """Verify every MacroAction produces the right hardware bitmask."""

    def setUp(self):
        from mk4_train import _BTN, _MACRO_MAP, macro_to_ctrl_state
        self._BTN = _BTN
        self._MACRO_MAP = _MACRO_MAP
        self.macro_to_ctrl_state = macro_to_ctrl_state

    def _mask(self, *buttons: Button) -> int:
        return sum(self._BTN[b] for b in buttons)

    def _ctrl_mask(self, macro: MacroAction) -> int:
        ctrl = self.macro_to_ctrl_state(macro)
        return sum(self._BTN.get(b, 0) for b in ctrl.pressed)

    def test_neutral_no_bits(self):
        self.assertEqual(self._ctrl_mask(MacroAction.NEUTRAL), 0)

    def test_advance_is_d_right(self):
        self.assertEqual(self._ctrl_mask(MacroAction.ADVANCE),
                         self._mask(Button.D_RIGHT))

    def test_retreat_is_d_left(self):
        self.assertEqual(self._ctrl_mask(MacroAction.RETREAT),
                         self._mask(Button.D_LEFT))

    def test_crouch_is_d_down(self):
        self.assertEqual(self._ctrl_mask(MacroAction.CROUCH),
                         self._mask(Button.D_DOWN))

    def test_jump_forward_has_both(self):
        self.assertEqual(self._ctrl_mask(MacroAction.JUMP_FORWARD),
                         self._mask(Button.D_UP, Button.D_RIGHT))

    def test_jump_back_has_both(self):
        self.assertEqual(self._ctrl_mask(MacroAction.JUMP_BACK),
                         self._mask(Button.D_UP, Button.D_LEFT))

    def test_stand_block_uses_c_left(self):
        # MK4 block = C-LEFT (NOT Z, even though Z also blocks — plugin sends C-LEFT)
        self.assertEqual(self._ctrl_mask(MacroAction.STAND_BLOCK),
                         self._mask(Button.C_LEFT))

    def test_low_punch_is_A(self):
        self.assertEqual(self._ctrl_mask(MacroAction.LOW_PUNCH),
                         self._mask(Button.A))

    def test_high_punch_is_B(self):
        self.assertEqual(self._ctrl_mask(MacroAction.HIGH_PUNCH),
                         self._mask(Button.B))

    def test_low_kick_is_c_right(self):
        self.assertEqual(self._ctrl_mask(MacroAction.LOW_KICK),
                         self._mask(Button.C_RIGHT))

    def test_high_kick_is_c_up(self):
        self.assertEqual(self._ctrl_mask(MacroAction.HIGH_KICK),
                         self._mask(Button.C_UP))

    def test_jab_combo_is_A_and_c_right(self):
        self.assertEqual(self._ctrl_mask(MacroAction.JAB_COMBO),
                         self._mask(Button.A, Button.C_RIGHT))

    def test_run_is_c_down_plus_d_right(self):
        self.assertEqual(self._ctrl_mask(MacroAction.RUN),
                         self._mask(Button.C_DOWN, Button.D_RIGHT))

    def test_special1_is_d_left_plus_A(self):
        self.assertEqual(self._ctrl_mask(MacroAction.SPECIAL_1),
                         self._mask(Button.D_LEFT, Button.A))

    def test_throw_does_not_overlap_special1(self):
        # THROW = D_RIGHT+A,  SPECIAL_1 = D_LEFT+A — must be different
        self.assertNotEqual(self._ctrl_mask(MacroAction.THROW_ATTEMPT),
                            self._ctrl_mask(MacroAction.SPECIAL_1))

    def test_crouch_block_has_c_left_and_d_down(self):
        self.assertEqual(self._ctrl_mask(MacroAction.CROUCH_BLOCK),
                         self._mask(Button.C_LEFT, Button.D_DOWN))

    def test_all_macros_are_in_map(self):
        for m in MacroAction:
            ctrl = self.macro_to_ctrl_state(m)
            self.assertIsInstance(ctrl, ControllerState,
                                  f"MacroAction.{m.name} missing from _MACRO_MAP")

    def test_bitmask_packed_fits_uint16(self):
        """The mmap write uses struct.pack('<Hbb') — mask must fit in uint16."""
        from mk4_train import _BTN
        for macro in MacroAction:
            ctrl = self.macro_to_ctrl_state(macro)
            mask = sum(_BTN.get(b, 0) for b in ctrl.pressed)
            self.assertLessEqual(mask, 0xFFFF,
                                 f"{macro.name} bitmask 0x{mask:X} overflows uint16")


# ─────────────────────────────────────────────────────────
# B. Observation builder
# ─────────────────────────────────────────────────────────

class FakeState:
    def __init__(self, p1_health=160, p2_health=160, timer=99,
                 p1_x=0.0, p2_x=5.0):
        self.p1_health = p1_health
        self.p2_health = p2_health
        self.timer = timer
        self.p1_x = p1_x
        self.p2_x = p2_x


class TestObsBuilder(unittest.TestCase):
    def setUp(self):
        from mk4_train import build_obs
        self.build_obs = build_obs

    def test_obs_length_is_14(self):
        obs = self.build_obs(FakeState())
        self.assertEqual(len(obs), 14)

    def test_full_health_normalises_to_1(self):
        obs = self.build_obs(FakeState(p1_health=160, p2_health=160))
        self.assertAlmostEqual(obs[0], 1.0)
        self.assertAlmostEqual(obs[1], 1.0)

    def test_zero_health_is_0(self):
        obs = self.build_obs(FakeState(p1_health=0, p2_health=0))
        self.assertAlmostEqual(obs[0], 0.0)
        self.assertAlmostEqual(obs[1], 0.0)

    def test_timer_99_normalises_to_1(self):
        obs = self.build_obs(FakeState(timer=99))
        self.assertAlmostEqual(obs[2], 1.0)

    def test_facing_sign_positive_when_p2_right(self):
        obs = self.build_obs(FakeState(p1_x=0.0, p2_x=5.0))
        self.assertEqual(obs[6], 1.0)

    def test_facing_sign_negative_when_p2_left(self):
        obs = self.build_obs(FakeState(p1_x=5.0, p2_x=0.0))
        self.assertEqual(obs[6], -1.0)

    def test_position_clipped_to_minus1_1(self):
        obs = self.build_obs(FakeState(p1_x=999.0, p2_x=-999.0))
        self.assertAlmostEqual(obs[3], 1.0)
        self.assertAlmostEqual(obs[4], -1.0)

    def test_dist_clipped_to_1(self):
        obs = self.build_obs(FakeState(p1_x=0.0, p2_x=999.0))
        self.assertAlmostEqual(obs[5], 1.0)

    def test_none_values_handled(self):
        state = FakeState()
        state.p1_health = None; state.p2_health = None
        state.timer = None; state.p1_x = None; state.p2_x = None
        obs = self.build_obs(state)
        self.assertEqual(len(obs), 14)
        self.assertFalse(any(v != v for v in obs), "NaN in obs")  # NaN check


# ─────────────────────────────────────────────────────────
# C. Reward extractor
# ─────────────────────────────────────────────────────────

class TestRewardExtractor(unittest.TestCase):
    def setUp(self):
        from n64train.runtime.rewards import Mk4ShapedRewardExtractor
        self.ext = Mk4ShapedRewardExtractor()

    def _state(self, **kw):
        from n64train.runtime.types import TracedState
        defaults = dict(frame_id=0, p1_health=160, p2_health=160,
                        timer=99, p1_x=0.0, p2_x=5.0)
        defaults.update(kw)
        return TracedState(**defaults)

    def test_dealing_damage_positive(self):
        prev = self._state(p2_health=160)
        nxt  = self._state(p2_health=140)
        terms = self.ext.compute(prev, nxt)
        self.assertGreater(terms.damage_dealt, 0)

    def test_taking_damage_negative(self):
        prev = self._state(p1_health=160)
        nxt  = self._state(p1_health=140)
        terms = self.ext.compute(prev, nxt)
        self.assertLess(terms.damage_taken, 0)

    def test_taking_damage_more_penalised_than_dealing(self):
        """Asymmetric scale: damage_taken_scale=1.5 > damage_dealt_scale=1.0"""
        prev_d = self._state(p2_health=160); nxt_d = self._state(p2_health=140)
        prev_t = self._state(p1_health=160); nxt_t = self._state(p1_health=140)
        dealt = self.ext.compute(prev_d, nxt_d).damage_dealt
        taken = abs(self.ext.compute(prev_t, nxt_t).damage_taken)
        self.assertGreater(taken, dealt)

    def test_win_bonus_fires_once(self):
        prev = self._state(p2_health=10)
        nxt  = self._state(p2_health=0)
        terms = self.ext.compute(prev, nxt)
        self.assertGreater(terms.win_bonus, 0)

    def test_win_bonus_does_not_fire_if_already_zero(self):
        prev = self._state(p2_health=0)
        nxt  = self._state(p2_health=0)
        terms = self.ext.compute(prev, nxt)
        self.assertEqual(terms.win_bonus, 0.0)

    def test_loss_penalty_fires_when_p1_dies(self):
        prev = self._state(p1_health=5)
        nxt  = self._state(p1_health=0)
        terms = self.ext.compute(prev, nxt)
        self.assertLess(terms.loss_penalty, 0)

    def test_approach_reward_when_closing(self):
        prev = self._state(p1_x=0.0, p2_x=10.0)
        nxt  = self._state(p1_x=1.0, p2_x=10.0)
        terms = self.ext.compute(prev, nxt)
        self.assertGreater(terms.approach_reward, 0)

    def test_no_approach_inside_fighting_range(self):
        """No approach reward if already within FIGHTING_RANGE (3 units)."""
        prev = self._state(p1_x=0.0, p2_x=2.0)
        nxt  = self._state(p1_x=0.5, p2_x=2.0)
        terms = self.ext.compute(prev, nxt)
        self.assertEqual(terms.approach_reward, 0.0)

    def test_spam_penalty_on_attack_spam(self):
        from n64train.runtime.rewards import SPAM_THRESHOLD
        hist = ['LOW_PUNCH'] * (SPAM_THRESHOLD + 2)
        prev = self._state(); nxt = self._state()
        terms = self.ext.compute(prev, nxt, action_history=hist)
        self.assertLess(terms.spam_penalty, 0)

    def test_no_spam_penalty_for_movement_spam(self):
        """ADVANCE/RETREAT spam should not be penalised (only attacks are)."""
        hist = ['ADVANCE'] * 20
        prev = self._state(); nxt = self._state()
        terms = self.ext.compute(prev, nxt, action_history=hist)
        self.assertEqual(terms.spam_penalty, 0.0)

    def test_scalar_is_sum_of_terms(self):
        prev = self._state(p2_health=160, p1_health=160)
        nxt  = self._state(p2_health=140, p1_health=150)
        terms = self.ext.compute(prev, nxt)
        expected = (terms.damage_dealt + terms.damage_taken + terms.win_bonus +
                    terms.loss_penalty + terms.approach_reward +
                    terms.distance_penalty + terms.survival + terms.spam_penalty)
        self.assertAlmostEqual(terms.scalar(), expected, places=5)

    def test_none_states_return_zero_reward(self):
        terms = self.ext.compute(None, None)
        self.assertEqual(terms.scalar(), 0.0)


# ─────────────────────────────────────────────────────────
# D. mk4_train single-path: recurrent agents get rewards
# ─────────────────────────────────────────────────────────

class TestMk4TrainRecurrentRewards(unittest.TestCase):
    """In mk4_train.run_training() only is_learning agents get record().
    Recurrent agents (LSTM etc) have learn() but also need record() or
    the learner path (parallel) to inject rewards. Single-path must work too."""

    def test_lstm_record_accumulates_rewards(self):
        from n64train.experiments.mk4_agent import Mk4LstmAgent, FrameStack
        agent = Mk4LstmAgent(device='cpu')
        agent.reset_episode()
        fs = FrameStack(obs_dim=14, n_frames=4)
        import random
        for _ in range(5):
            obs = fs.push([random.random() for _ in range(14)])
            agent(obs)
            agent.record(random.random())
        self.assertEqual(len(agent._rewards), 5)

    def test_lstm_learn_after_records(self):
        from n64train.experiments.mk4_agent import Mk4LstmAgent, FrameStack
        import random
        agent = Mk4LstmAgent(device='cpu')
        agent.reset_episode()
        fs = FrameStack(obs_dim=14, n_frames=4)
        for _ in range(6):
            obs = fs.push([random.random() for _ in range(14)])
            agent(obs)
            agent.record(random.random())
        m = agent.learn()
        self.assertIsNotNone(m)
        self.assertIn('ppo_policy_loss', m or {})


# ─────────────────────────────────────────────────────────
# E. Learner _update: PPO buffer injection for all 4 PPO agents
# ─────────────────────────────────────────────────────────

class TestLearnerPPOInjection(unittest.TestCase):
    """Verify the learner correctly injects _old_lp_buf / _val_buf
    for disc_rssm, transformer, obj_belief, and lstm."""

    def _build_rollout(self, n=6):
        import random
        return {
            'obs':     [[random.random()] * 56 for _ in range(n)],
            'acts':    [random.randint(0, 19) for _ in range(n)],
            'rewards': [random.random() for _ in range(n)],
            'old_lps': [random.gauss(0, 1) for _ in range(n)],
            'vals':    [random.gauss(0, 0.5) for _ in range(n)],
            'cpu_attacked': [0.0] * n,
        }

    def _run_update(self, agent_type: str):
        from n64train.training.learner import ParallelLearner
        from multiprocessing import Queue
        # We can't instantiate ParallelLearner (it calls build_agent which does build_arch_agent)
        # but we CAN test the agent's injection path directly
        import n64train.experiments.mk4_architectures as arch
        import n64train.experiments.mk4_agent as ag
        agents = {
            'lstm':        ag.Mk4LstmAgent,
            'disc_rssm':   arch.Mk4DiscRssmAgent,
            'transformer': arch.Mk4TransformerAgent,
            'obj_belief':  arch.Mk4ObjBeliefAgent,
        }
        agent = agents[agent_type](device='cpu')
        r = self._build_rollout()
        n = len(r['obs'])
        agent.reset_episode()
        agent._obs_buf  = r['obs']
        agent._act_buf  = r['acts']
        agent._rewards  = r['rewards']
        if hasattr(agent, '_cpu_attacked'):
            agent._cpu_attacked = r['cpu_attacked']
        agent._old_lp_buf = r['old_lps']
        agent._val_buf    = r['vals']
        m = agent.learn()
        self.assertIsNotNone(m, f"{agent_type} learn() returned None")

    def test_lstm_injection(self):
        self._run_update('lstm')

    def test_disc_rssm_injection(self):
        self._run_update('disc_rssm')

    def test_transformer_injection(self):
        self._run_update('transformer')

    def test_obj_belief_injection(self):
        self._run_update('obj_belief')


# ─────────────────────────────────────────────────────────
# F. FrameStack
# ─────────────────────────────────────────────────────────

class TestFrameStack(unittest.TestCase):
    def setUp(self):
        from n64train.experiments.mk4_agent import FrameStack
        self.FrameStack = FrameStack

    def test_output_dim_is_56(self):
        fs = self.FrameStack(obs_dim=14, n_frames=4)
        out = fs.push([1.0] * 14)
        self.assertEqual(len(out), 56)

    def test_first_push_pads_with_zeros(self):
        fs = self.FrameStack(obs_dim=14, n_frames=4)
        out = fs.push([1.0] * 14)
        # First 3 frames should be zero-padded (3 × 14 = 42 zeros)
        self.assertEqual(out[:42], [0.0] * 42)
        self.assertEqual(out[42:], [1.0] * 14)

    def test_four_pushes_fills_completely(self):
        fs = self.FrameStack(obs_dim=14, n_frames=4)
        for i in range(4):
            out = fs.push([float(i)] * 14)
        # Should be [0,0,...(14), 1,...(14), 2,...(14), 3,...(14)]
        expected = [float(i) for i in range(4) for _ in range(14)]
        self.assertEqual(out, expected)

    def test_oldest_frame_dropped_after_5_pushes(self):
        fs = self.FrameStack(obs_dim=14, n_frames=4)
        for i in range(5):
            out = fs.push([float(i)] * 14)
        # After 5 pushes, window = [1,2,3,4]
        expected = [float(i) for i in range(1, 5) for _ in range(14)]
        self.assertEqual(out, expected)

    def test_opponent_stack_gives_56_float(self):
        """Reproduce the Bug 2 fix: opponent uses its own FrameStack."""
        opp_fs = self.FrameStack(obs_dim=14, n_frames=4)
        raw = [0.5] * 14
        stacked = opp_fs.push(raw)
        self.assertEqual(len(stacked), 56)


if __name__ == '__main__':
    unittest.main(verbosity=2)
