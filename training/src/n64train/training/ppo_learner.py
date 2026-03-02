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

import torch
import torch.nn.functional as F
from torch.distributions import Categorical

# PPO hyperparameters
PPO_EPOCHS   = 4       # epochs over each collected rollout
PPO_EPSILON  = 0.2     # clipping range for policy ratio
PPO_VF_COEF  = 0.5     # value loss weight
PPO_ENT_COEF = 0.02    # entropy bonus (decays with schedule)
GAE_GAMMA    = 0.99    # discount factor
GAE_LAMBDA   = 0.95    # GAE smoothing (1.0 = Monte Carlo, 0.0 = TD(1))
GRAD_CLIP    = 1.0


def gae_advantages(
    rewards: list[float],
    values: torch.Tensor,   # (T,) value estimates from network
    gamma: float = GAE_GAMMA,
    lam: float   = GAE_LAMBDA,
) -> tuple[torch.Tensor, torch.Tensor]:
    """
    Compute GAE advantages and TD-lambda returns.

    Returns:
        advantages: (T,) normalised advantage estimates
        returns:    (T,) target values for value function
    """
    T   = len(rewards)
    adv = torch.zeros(T, dtype=torch.float32)
    last_gae = 0.0

    # Bootstrap value for the last step is 0 (episode ended)
    for t in reversed(range(T)):
        next_val = values[t + 1].item() if t < T - 1 else 0.0
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

    # Value — clipped MSE (prevents value explosion)
    vf_loss = F.mse_loss(values, returns)

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
