"""
mk4_agent.py — MK4 Policy Agents (MLP + LSTM) with REINFORCE + Baseline
────────────────────────────────────────────────────────────────────────

Observation: RAW_OBS_DIM raw floats × 4 stacked frames
  Frame stacking gives the model velocity, damage rate, temporal context.

Architectures:
  MLP  : Linear(OBS_DIM→128→128) + policy/value heads  (fast, memoryless within stack)
  LSTM : Linear(OBS_DIM→64) → LSTM(128) + policy/value heads  (full episode memory, BPTT)

Algorithm: REINFORCE with baseline (Monte Carlo policy gradient)
  - MLP:  obs replayed at episode end with gradients
  - LSTM: BPTT over full episode sequence with fresh hidden state

Hyperparams:
  lr_policy   = 3e-4   (Adam)
  lr_value    = 1e-3   (Adam)
  gamma       = 0.99
  entropy_coef= 0.02
  grad_clip   = 1.0

Checkpoints:
  MLP  → training/data/checkpoints/mk4_policy.pt
  LSTM → training/data/checkpoints/mk4_lstm_policy.pt
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from n64train.runtime.actions import MacroAction

# ── Constants ──────────────────────────────────────────────────────────────────
N64_ROOT    = Path(__file__).resolve().parents[4]   # mk4_agent.py → experiments → n64train → src → training → n64
CKPT_DIR    = N64_ROOT / 'training/data/checkpoints'
CKPT_PATH   = CKPT_DIR / 'mk4_policy.pt'
STATS_PATH  = CKPT_DIR / 'mk4_training_stats.jsonl'

RAW_OBS_DIM = 22   # single-frame obs size (mirrors mk4_train.RAW_OBS_DIM)
OBS_DIM     = RAW_OBS_DIM * 4
N_ACTIONS   = len(MacroAction)
ACTIONS     = list(MacroAction)

GAMMA       = 0.99


# ── Network ────────────────────────────────────────────────────────────────────

class Mk4PolicyNet(nn.Module):
    """Shared-trunk MLP with separate policy and value heads."""

    def __init__(self, obs_dim: int = OBS_DIM, n_actions: int = N_ACTIONS,
                 hidden: int = 128) -> None:
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.LayerNorm(hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
            nn.ReLU(),
        )
        self.policy_head = nn.Linear(hidden, n_actions)
        self.value_head  = nn.Linear(hidden, 1)

        # Initialise last layers small so policy starts near-uniform
        nn.init.orthogonal_(self.policy_head.weight, gain=0.01)
        nn.init.orthogonal_(self.value_head.weight, gain=1.0)

    def forward(self, x: torch.Tensor):
        h = self.trunk(x)
        return self.policy_head(h), self.value_head(h).squeeze(-1)

    def action_dist(self, x: torch.Tensor) -> Categorical:
        logits, _ = self.forward(x)
        return Categorical(logits=logits)

    def act(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Returns (action_idx, log_prob, value_estimate)."""
        logits, value = self.forward(x)
        dist = Categorical(logits=logits)
        action = dist.sample()
        return action, dist.log_prob(action), value


# ── Agent ──────────────────────────────────────────────────────────────────────

class Mk4MlpAgent:
    """
    Stateful REINFORCE agent. Call from training loop as:

        action = agent(obs)           # get action each step
        agent.record(reward, done)    # record step reward
        agent.learn()                 # update at episode end
    """
    CKPT = CKPT_PATH   # class-level path — learner uses this to build run-scoped saves
    ARCH = 'mlp'

    def __init__(self, device: str = 'cpu') -> None:
        self.device = torch.device(device)
        self.net = Mk4PolicyNet().to(self.device)

        # Separate optimisers — value net needs to move faster
        self.opt_policy = torch.optim.Adam(
            list(self.net.trunk.parameters()) + list(self.net.policy_head.parameters()),
            lr=LR_POLICY)
        self.opt_value = torch.optim.Adam(
            self.net.value_head.parameters(), lr=LR_VALUE)

        # Episode buffer (obs + actions replayed in learn(), rewards accumulated)
        self._obs_buf   : list[list[float]]  = []
        self._act_buf   : list[int]          = []
        self._rewards   : list[float]        = []

        # Stats
        self.episode = 0
        self.total_updates = 0

        CKPT_DIR.mkdir(parents=True, exist_ok=True)
        self._try_load()

    # ── Public interface ───────────────────────────────────────────────────────

    def __call__(self, obs: list[float]) -> MacroAction:
        """Select an action given raw 7-float observation list."""
        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)

        self.net.eval()
        with torch.no_grad():
            logits, value = self.net(x)
            dist  = Categorical(logits=logits)
            action = dist.sample()

        # Store obs + action for replay in learn(); NOT the tensors (no grad)
        self._obs_buf.append(obs)
        self._act_buf.append(action.item())
        return ACTIONS[action.item()]

    def record(self, reward: float, done: bool = False) -> None:
        """Record the reward received after the last action."""
        self._rewards.append(reward)
        if done:
            self.learn()

    def learn(self) -> dict[str, float] | None:
        """
        Run one REINFORCE update by replaying obs through net with grad enabled.
        Returns a dict of training metrics or None if buffer is empty.
        """
        n = min(len(self._obs_buf), len(self._act_buf), len(self._rewards))
        if n < 2:
            self._clear_buffers()
            return None

        self.net.train()
        self.episode += 1

        # ── Compute discounted returns ─────────────────────────────────────
        returns: list[float] = []
        G = 0.0
        for r in reversed(self._rewards[:n]):
            G = r + GAMMA * G
            returns.insert(0, G)

        ret_t = torch.tensor(returns, dtype=torch.float32, device=self.device)
        if ret_t.std() > 1e-6:
            ret_t = (ret_t - ret_t.mean()) / (ret_t.std() + 1e-8)

        # ── Replay forward pass WITH grad ──────────────────────────────────
        obs_t  = torch.tensor(self._obs_buf[:n], dtype=torch.float32, device=self.device)
        act_t  = torch.tensor(self._act_buf[:n], dtype=torch.long,    device=self.device)

        logits, values = self.net(obs_t)           # (n, n_actions), (n,)
        dist       = Categorical(logits=logits)
        log_probs  = dist.log_prob(act_t)          # (n,)
        entropies  = dist.entropy()                # (n,)

        # ── Advantage ─────────────────────────────────────────────────────
        advantages = (ret_t - values.detach())

        # ── Combined loss — single backward ───────────────────────────────
        policy_loss  = -(log_probs * advantages).mean()
        value_loss   = F.mse_loss(values, ret_t)
        entropy_loss = -entropies.mean()
        total_loss   = policy_loss + 0.5 * value_loss + ENTROPY_COEF * entropy_loss

        self.opt_policy.zero_grad()
        self.opt_value.zero_grad()
        total_loss.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), GRAD_CLIP)
        self.opt_policy.step()
        self.opt_value.step()

        self.total_updates += 1
        metrics = {
            'episode':      self.episode,
            'policy_loss':  round(policy_loss.item(), 4),
            'value_loss':   round(value_loss.item(), 4),
            'entropy':      round(-entropy_loss.item(), 4),
            'mean_return':  round(ret_t.mean().item(), 4),
            'n_steps':      n,
        }

        # Fix 4: use per-run-id stats path if set by learner, else global fallback
        _stats = getattr(self, '_stats_path', STATS_PATH)
        with open(_stats, 'a') as f:
            f.write(json.dumps(metrics) + '\n')

        self._clear_buffers()
        return metrics


    def save(self, path: Path | None = None) -> None:
        path = path or CKPT_PATH
        torch.save({
            'net':           self.net.state_dict(),
            'opt_policy':    self.opt_policy.state_dict(),
            'opt_value':     self.opt_value.state_dict(),
            'episode':       self.episode,
            'total_updates': self.total_updates,
        }, path)

    def load(self, path: Path | None = None) -> None:
        path = path or CKPT_PATH
        ckpt = torch.load(path, map_location=self.device)
        self.net.load_state_dict(ckpt['net'])
        self.opt_policy.load_state_dict(ckpt['opt_policy'])
        self.opt_value.load_state_dict(ckpt['opt_value'])
        self.episode       = ckpt.get('episode', 0)
        self.total_updates = ckpt.get('total_updates', 0)
        print(f'[agent] Loaded checkpoint — ep={self.episode} updates={self.total_updates}')

    # ── Private ────────────────────────────────────────────────────────────────

    def _clear_buffers(self) -> None:
        self._obs_buf  = []
        self._act_buf  = []
        self._rewards  = []

    def _try_load(self) -> None:
        if CKPT_PATH.exists():
            try:
                self.load()
            except Exception as e:
                print(f'[agent] Could not load checkpoint ({e}) — starting fresh')
        else:
            print(f'[agent] No checkpoint found — starting fresh')


# ── Frame Stack ────────────────────────────────────────────────────────────────

class FrameStack:
    """
    Stacks the last N observation frames into a single flat vector.

    Gives the policy implicit access to velocity (position delta),
    damage rate (hp delta), and recent temporal context without an RNN.

    obs_dim  : size of a single observation (default RAW_OBS_DIM)
    n_frames : number of frames to stack         (default 4)
    out_dim  : obs_dim × n_frames                (default OBS_DIM)
    """

    def __init__(self, obs_dim: int = RAW_OBS_DIM, n_frames: int = 4) -> None:
        self.obs_dim  = obs_dim
        self.n_frames = n_frames
        self.out_dim  = obs_dim * n_frames
        self._buf: list[list[float]] = []

    def push(self, obs: list[float]) -> list[float]:
        """Add newest obs, return stacked vector (oldest → newest)."""
        self._buf.append(obs)
        if len(self._buf) > self.n_frames:
            self._buf.pop(0)
        # Pad with zeros if we don't have enough frames yet
        pad = self.n_frames - len(self._buf)
        frames = [[0.0] * self.obs_dim] * pad + self._buf
        # Flatten: [frame0, frame1, frame2, frame3]
        out: list[float] = []
        for f in frames:
            out.extend(f)
        return out

    def reset(self) -> None:
        self._buf.clear()


# ── LSTM Network ───────────────────────────────────────────────────────────────

LSTM_HIDDEN = 256
LSTM_LAYERS = 1
LSTM_ENC_DIM = 256
LSTM_CKPT_PATH = CKPT_DIR / 'mk4_lstm_policy.pt'


class Mk4LstmNet(nn.Module):
    """
    LSTM policy network — upgraded per "37 PPO Implementation Details".

    Input per step: obs_dim floats (raw obs, NOT stacked — LSTM handles memory)
    Architecture:
      Linear(obs_dim → 128) → LayerNorm → ReLU
      Linear(128 → 256) → LayerNorm → ReLU
      LSTM(256, hidden_size=256, num_layers=1)
      policy_head: Linear(256 → n_actions)
      value_head:  Linear(256 → 1)
    """

    def __init__(self, obs_dim: int = OBS_DIM, n_actions: int = N_ACTIONS,
                 hidden: int = LSTM_HIDDEN) -> None:
        super().__init__()
        self.hidden = hidden
        self.encoder = nn.Sequential(
            nn.Linear(obs_dim, 128),
            nn.LayerNorm(128),
            nn.ReLU(),
            nn.Linear(128, LSTM_ENC_DIM),
            nn.LayerNorm(LSTM_ENC_DIM),
            nn.ReLU(),
        )
        self.lstm = nn.LSTM(input_size=LSTM_ENC_DIM, hidden_size=hidden,
                            num_layers=LSTM_LAYERS, batch_first=True)
        self.policy_head = nn.Linear(hidden, n_actions)
        self.value_head  = nn.Linear(hidden, 1)

        # Orthogonal init on ALL layers (37 PPO details)
        from n64train.training.ppo_learner import ortho_init
        ortho_init(self.encoder, gain=2**0.5)
        ortho_init(self.lstm, gain=1.0)
        nn.init.orthogonal_(self.policy_head.weight, gain=0.01)
        nn.init.zeros_(self.policy_head.bias)
        nn.init.orthogonal_(self.value_head.weight, gain=1.0)
        nn.init.zeros_(self.value_head.bias)

    def forward(
        self,
        x: torch.Tensor,
        hc: tuple[torch.Tensor, torch.Tensor] | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, tuple[torch.Tensor, torch.Tensor]]:
        """
        x  : (batch, seq_len, obs_dim) or (batch, obs_dim) for single step
        Returns: logits(batch, n_actions), values(batch,), hc_new
        """
        single_step = (x.dim() == 2)
        if single_step:
            x = x.unsqueeze(1)  # (batch, 1, obs_dim)

        enc = self.encoder(x)                    # (batch, seq, 256)
        out, hc_new = self.lstm(enc, hc)         # (batch, seq, hidden)
        last = out[:, -1, :]                     # (batch, hidden)

        logits = self.policy_head(last)          # (batch, n_actions)
        values = self.value_head(last).squeeze(-1)  # (batch,)
        return logits, values, hc_new

    def init_hidden(self, batch: int = 1, device: torch.device | None = None):
        d = device or next(self.parameters()).device
        h = torch.zeros(LSTM_LAYERS, batch, self.hidden, device=d)
        c = torch.zeros(LSTM_LAYERS, batch, self.hidden, device=d)
        return h, c


# ── LSTM Agent ─────────────────────────────────────────────────────────────────

class Mk4LstmAgent:
    """
    PPO agent using an LSTM policy — upgraded per 37 PPO Implementation Details.

    - Hidden state (h, c) persists across EVERY STEP within an episode
    - Resets to zeros at the start of each new episode
    - During learn(): BPTT over the full episode sequence
    - Obs input: OBS_DIM floats per step (stacked frames)
    - Single optimizer with LR annealing
    """
    CKPT = LSTM_CKPT_PATH   # class-level path — learner uses this for run-scoped saves
    ARCH = 'lstm'

    def __init__(self, device: str = 'cpu') -> None:
        from n64train.training.ppo_learner import LR
        self.device = torch.device(device)
        self.net = Mk4LstmNet().to(self.device)

        # Single optimizer for all params (37 PPO details)
        self.optimizer = torch.optim.Adam(self.net.parameters(), lr=LR, eps=1e-5)

        # Episode buffers
        self._obs_buf:    list[list[float]] = []
        self._act_buf:    list[int]         = []
        self._rewards:    list[float]       = []

        # LSTM hidden state — persists across steps, reset per episode
        self._hc: tuple[torch.Tensor, torch.Tensor] | None = None

        self.episode       = 0
        self.total_updates = 0

        CKPT_DIR.mkdir(parents=True, exist_ok=True)
        self._try_load()

    def reset_episode(self) -> None:
        """Reset LSTM hidden state and episode buffers for a new episode."""
        self._hc = self.net.init_hidden(batch=1, device=self.device)
        self._obs_buf  = []
        self._act_buf  = []
        self._rewards  = []
        self._old_lp_buf: list[float] = []   # old log-probs for PPO ratio
        self._val_buf:    list[float] = []   # value estimates for GAE
        self._bootstrap_val: float    = 0.0  # V̂(s_T+1): set by learner for truncated eps

    def __call__(self, obs: list[float]) -> MacroAction:
        """Select action — maintains LSTM hidden state across calls.
        Also records old log_prob and value estimate for PPO."""
        if self._hc is None:
            self._hc = self.net.init_hidden(batch=1, device=self.device)

        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        self.net.eval()
        with torch.no_grad():
            logits, value, hc_new = self.net(x, self._hc)
            self._hc = hc_new
            dist   = Categorical(logits=logits)
            action = dist.sample()
            old_lp = dist.log_prob(action)

        self._obs_buf.append(obs)
        self._act_buf.append(action.item())
        self._old_lp_buf.append(old_lp.item())
        self._val_buf.append(value.item())
        return ACTIONS[action.item()]

    def record(self, reward: float, done: bool = False) -> None:
        self._rewards.append(reward)
        if done:
            self.learn()

    def learn(self) -> dict[str, float] | None:
        """PPO update over full episode rollout — K epochs of clipped surrogate."""
        from n64train.training.ppo_learner import (
            gae_advantages, ppo_loss, entropy_schedule, anneal_lr,
            PPO_EPOCHS, GRAD_CLIP,
        )

        n = min(len(self._obs_buf), len(self._act_buf), len(self._rewards))
        if n < 2:
            self.reset_episode()
            return None

        self.episode += 1
        ent_coef = entropy_schedule(self.episode)  # 0.05 → 0.01 over 500 eps
        anneal_lr(self.optimizer, self.episode)     # linear LR decay

        # Tensors for this episode
        obs_seq  = torch.tensor(self._obs_buf[:n],  dtype=torch.float32, device=self.device)
        act_t    = torch.tensor(self._act_buf[:n],  dtype=torch.long,    device=self.device)
        old_lp_t = torch.tensor(self._old_lp_buf[:n], dtype=torch.float32, device=self.device)
        val_t    = torch.tensor(self._val_buf[:n],  dtype=torch.float32, device=self.device)

        # GAE advantages + TD-lambda returns (computed once, reused across epochs)
        adv_t, ret_t = gae_advantages(self._rewards[:n], val_t,
                                       bootstrap_val=getattr(self, '_bootstrap_val', 0.0))
        adv_t = adv_t.to(self.device)
        ret_t = ret_t.to(self.device)

        # K epochs of PPO — full sequence re-forward each epoch (recurrent PPO)
        metrics_last = {}
        self.net.train()
        for _ in range(PPO_EPOCHS):
            hc0 = self.net.init_hidden(batch=1, device=self.device)
            enc = self.net.encoder(obs_seq.unsqueeze(0))      # (1, n, enc_dim)
            lstm_out, _ = self.net.lstm(enc, hc0)             # (1, n, hidden)
            logits_seq  = self.net.policy_head(lstm_out[0])   # (n, n_actions)
            values_seq  = self.net.value_head(lstm_out[0]).squeeze(-1)  # (n,)

            loss, metrics_last = ppo_loss(
                logits_seq, values_seq, act_t, old_lp_t, adv_t, ret_t,
                ent_coef=ent_coef,
            )

            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.net.parameters(), GRAD_CLIP)
            self.optimizer.step()

        self.total_updates += 1
        metrics = {
            'episode':     self.episode,
            'agent':       'lstm',
            'n_steps':     n,
            'mean_return': round(ret_t.mean().item(), 4),
            'ent_coef':    round(ent_coef, 4),
            **metrics_last,
        }
        # Fix 4: use per-run-id stats path if set by learner, else global fallback
        _stats = getattr(self, '_stats_path', STATS_PATH)
        with open(_stats, 'a') as f:
            f.write(json.dumps(metrics) + '\n')

        self.reset_episode()
        return metrics

    def save(self, path: Path | None = None) -> None:
        path = path or LSTM_CKPT_PATH
        torch.save({
            'net':           self.net.state_dict(),
            'optimizer':     self.optimizer.state_dict(),
            'episode':       self.episode,
            'total_updates': self.total_updates,
        }, path)

    def load(self, path: Path | None = None) -> None:
        path = path or LSTM_CKPT_PATH
        ckpt = torch.load(path, map_location=self.device)
        self.net.load_state_dict(ckpt['net'], strict=False)
        if 'optimizer' in ckpt:
            try:
                self.optimizer.load_state_dict(ckpt['optimizer'])
            except (ValueError, KeyError):
                print('[lstm] Optimizer state mismatch — using fresh optimizer')
        self.episode       = ckpt.get('episode', 0)
        self.total_updates = ckpt.get('total_updates', 0)
        print(f'[lstm] Loaded checkpoint — ep={self.episode} updates={self.total_updates}')

    def _try_load(self) -> None:
        if LSTM_CKPT_PATH.exists():
            try:
                self.load()
            except Exception as e:
                print(f'[lstm] Could not load checkpoint ({e}) — starting fresh')
        else:
            print(f'[lstm] No checkpoint found — starting fresh')
