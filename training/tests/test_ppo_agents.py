"""
test_ppo_agents.py — Smoke tests for PPO buffer fixes.

Verifies (without an emulator):
  1. disc_rssm.learn() no longer raises AttributeError on _old_lp / _val / _act
  2. transformer.learn() no longer raises AttributeError on _old_lp / _val
  3. obj_belief.learn() no longer raises AttributeError on _old_lp / _val / _rew
  4. obj_belief.reset_episode() clears _rewards (not a stale _rew)
  5. Opponent agent receives stacked obs (not raw single-frame obs)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

import torch
import unittest
import random
from n64train.experiments.mk4_agent import OBS_DIM, RAW_OBS_DIM

random.seed(0); torch.manual_seed(0)

N_STEPS = 6


def _fake_episode(agent, n=N_STEPS):
    """Run `n` fake steps through agent, injecting synthetic PPO bufs."""
    agent.reset_episode()
    for _ in range(n):
        obs = [random.random() for _ in range(OBS_DIM)]
        agent(obs)
    # Manually set rewards
    agent._rewards = [random.random() for _ in range(n)]
    # Verify _old_lp_buf and _val_buf were populated by __call__
    assert len(agent._old_lp_buf) == n, f"_old_lp_buf length mismatch: {len(agent._old_lp_buf)} != {n}"
    assert len(agent._val_buf)    == n, f"_val_buf length mismatch: {len(agent._val_buf)} != {n}"


class TestDiscRssm(unittest.TestCase):
    def setUp(self):
        from n64train.experiments.mk4_architectures import Mk4DiscRssmAgent
        self.agent = Mk4DiscRssmAgent(device='cpu')

    def test_learn_no_attribute_error(self):
        _fake_episode(self.agent)
        m = self.agent.learn()
        self.assertIsNotNone(m, "disc_rssm.learn() returned None — too few steps?")
        self.assertIn('ppo_policy_loss', m)

    def test_act_buf_used_in_replay(self):
        """Verify _act_buf is set before learn() so no KeyError on [i-1]."""
        _fake_episode(self.agent)
        # Should not raise — previously used self._act[i-1] which didn't exist
        self.agent.learn()


class TestTransformer(unittest.TestCase):
    def setUp(self):
        from n64train.experiments.mk4_architectures import Mk4TransformerAgent
        self.agent = Mk4TransformerAgent(device='cpu')

    def test_learn_no_attribute_error(self):
        _fake_episode(self.agent)
        m = self.agent.learn()
        self.assertIsNotNone(m)
        self.assertIn('ppo_policy_loss', m)


class TestObjBelief(unittest.TestCase):
    def setUp(self):
        from n64train.experiments.mk4_architectures import Mk4ObjBeliefAgent
        self.agent = Mk4ObjBeliefAgent(device='cpu')

    def test_learn_no_attribute_error(self):
        _fake_episode(self.agent)
        # ObjBelief also needs _cpu_attacked
        self.agent._cpu_attacked = [0.0] * N_STEPS
        m = self.agent.learn()
        self.assertIsNotNone(m)
        self.assertIn('ppo_policy_loss', m)

    def test_reset_episode_clears_rewards(self):
        """Bug 4: reset_episode was clearing self._rew (not _rewards), leaving stale data."""
        _fake_episode(self.agent)
        self.agent._cpu_attacked = [0.0] * N_STEPS
        # Put stale data in _rewards then reset
        self.agent._rewards = [99.9] * N_STEPS
        self.agent.reset_episode()
        self.assertEqual(self.agent._rewards, [],
                         "_rewards should be [] after reset_episode()")

    def test_no_stale_rewards_across_episodes(self):
        """Stale rewards from ep1 must not pollute ep2."""
        _fake_episode(self.agent)
        self.agent._cpu_attacked = [0.0] * N_STEPS
        self.agent.learn()   # ep1 — resets internally
        # ep2: rewards should start empty
        self.assertEqual(self.agent._rewards, [])


class TestSelfPlayObsStacking(unittest.TestCase):
    def test_opponent_receives_stacked_obs(self):
        """Opponent must receive OBS_DIM stacked obs, not RAW_OBS_DIM raw obs."""
        from n64train.experiments.mk4_agent import FrameStack
        opp_frame_stack = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)
        raw_obs = [0.5] * RAW_OBS_DIM
        stacked = opp_frame_stack.push(raw_obs)
        self.assertEqual(
            len(stacked),
            OBS_DIM,
            f"Stacked obs should be {OBS_DIM}-float, got {len(stacked)}",
        )

    def test_lstm_accepts_stacked_obs(self):
        """LSTM agent should not crash when given OBS_DIM stacked obs."""
        from n64train.experiments.mk4_agent import Mk4LstmAgent, FrameStack
        agent = Mk4LstmAgent(device='cpu')
        agent.reset_episode()
        fs = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)
        obs_stacked = fs.push([random.random() for _ in range(RAW_OBS_DIM)])
        result = agent(obs_stacked)
        self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
