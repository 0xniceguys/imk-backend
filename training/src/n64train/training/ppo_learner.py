"""
ppo_learner.py — Shared PPO utilities for all MK4 agents.

Replaces REINFORCE (single update per episode) with:
  - GAE (Generalized Advantage Estimation) — lower-variance advantage estimates
  - Clipped surrogate objective (ε=0.2) — stable, bounded policy updates
  - K=4 epochs per rollout — reuse each episode's data more efficiently

All agents use recurrent PPO (full sequence re-forward each epoch) to
correctly handle LSTM/GRU/RSSM hidden state chains.
"""
from __future__ import annotations

import os
import torch
import torch.nn.functional as F
from torch.distributions import Categorical

# PPO hyperparameters — aligned with "37 PPO Implementation Details" (ICLR)
# Runtime override for throughput tuning (e.g. N64_PPO_EPOCHS=2 for faster updates).
PPO_EPOCHS   = max(1, int(os.environ.get('N64_PPO_EPOCHS', '4')))
PPO_EPSILON  = 0.2     # clipping range for policy ratio
PPO_VF_COEF  = 0.5     # value loss weight
PPO_ENT_COEF = 0.01    # entropy bonus (schedule decays 0.05→0.01)
GAE_GAMMA    = 0.99    # discount factor
GAE_LAMBDA   = 0.95    # GAE smoothing (1.0 = Monte Carlo, 0.0 = TD(1))
GRAD_CLIP    = 0.5     # tighter clipping for stability (was 1.0)
LR           = 2.5e-4  # single LR for all params (annealed linearly to 0)


def gae_advantages(
    rewards:       list[float],
    values:        torch.Tensor,   # (T,) value estimates from network
    gamma:         float = GAE_GAMMA,
    lam:           float = GAE_LAMBDA,
    bootstrap_val: float = 0.0,    # V(s_T+1): 0 if true terminal, V̂ if truncated
) -> tuple[torch.Tensor, torch.Tensor]:
    """
    Compute GAE advantages and TD-lambda returns.

    bootstrap_val: value-function estimate at the step AFTER the last collected
    step. Pass 0.0 for true terminal episodes (P2 died, P1 died, round over).
    Pass the network's V̂(last_obs) for truncated episodes (wall-clock timeout,
    bridge recovery). Getting this wrong biases value targets downward, causing
    the agent to underestimate future rewards in long fights.

    Returns:
        advantages: (T,) normalised advantage estimates
        returns:    (T,) target values for value function
    """
    T   = len(rewards)
    # Keep GAE tensors on the same device as value predictions to avoid
    # cross-device ops when learner runs on MPS/CUDA.
    adv = torch.zeros(T, dtype=torch.float32, device=values.device)
    last_gae = 0.0

    for t in reversed(range(T)):
        next_val = values[t + 1].item() if t < T - 1 else bootstrap_val
        delta    = rewards[t] + gamma * next_val - values[t].item()
        last_gae = delta + gamma * lam * last_gae
        adv[t]   = last_gae

    returns = adv + values[:T]

    # Normalise advantages — critical for PPO stability
    if adv.std() > 1e-6:
        adv = (adv - adv.mean()) / (adv.std() + 1e-8)

    return adv, returns


def ppo_loss(
    logits:    torch.Tensor,   # (T, n_actions)  — current policy
    values:    torch.Tensor,   # (T,)             — current value estimates
    actions:   torch.Tensor,   # (T,)             — sampled actions (long)
    old_lp:    torch.Tensor,   # (T,)             — old log-probs (no_grad)
    adv:       torch.Tensor,   # (T,)             — GAE advantages
    returns:   torch.Tensor,   # (T,)             — TD-lambda targets
    eps:       float = PPO_EPSILON,
    ent_coef:  float = PPO_ENT_COEF,
    vf_coef:   float = PPO_VF_COEF,
) -> tuple[torch.Tensor, dict]:
    """
    Compute PPO clipped loss.

    Returns:
        loss:    scalar tensor (policy + value + entropy)
        metrics: dict of sub-losses for logging
    """
    dist    = Categorical(logits=logits)
    new_lp  = dist.log_prob(actions)
    entropy = dist.entropy()

    # Policy — clipped surrogate
    ratio    = (new_lp - old_lp).exp()
    clip_adv = torch.clamp(ratio, 1.0 - eps, 1.0 + eps) * adv
    pol_loss = -torch.min(ratio * adv, clip_adv).mean()

    # Value — Huber loss (less sensitive to outliers than MSE)
    vf_loss = F.smooth_l1_loss(values, returns)

    # Total
    loss = pol_loss + vf_coef * vf_loss - ent_coef * entropy.mean()

    return loss, {
        'ppo_policy_loss': round(pol_loss.item(), 4),
        'ppo_value_loss':  round(vf_loss.item(), 4),
        'ppo_entropy':     round(entropy.mean().item(), 4),
        'ppo_ratio_mean':  round(ratio.mean().item(), 4),
        'ppo_clip_frac':   round(((ratio - 1.0).abs() > eps).float().mean().item(), 4),
    }


def entropy_schedule(episode: int, start: float = 0.05, end: float = 0.01, decay: int = 500) -> float:
    """Linear entropy decay from start→end over `decay` episodes."""
    frac = min(1.0, episode / decay)
    return start + (end - start) * frac


def anneal_lr(optimizer: torch.optim.Optimizer, episode: int, total_episodes: int = 25000) -> None:
    """Linear LR decay from initial LR → 0 over total_episodes."""
    frac = max(0.0, 1.0 - episode / total_episodes)
    for pg in optimizer.param_groups:
        pg['lr'] = LR * frac


def ortho_init(module: torch.nn.Module, gain: float = 2**0.5) -> None:
    """Orthogonal init on all Linear/LSTM/GRU weights in a module (recursive)."""
    for name, param in module.named_parameters():
        if 'weight' in name and param.dim() >= 2:
            torch.nn.init.orthogonal_(param, gain=gain)
        elif 'bias' in name:
            torch.nn.init.zeros_(param)
