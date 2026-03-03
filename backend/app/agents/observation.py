"""
Observation builder — converts FightState into the 14-float vector
that trained agents expect.

Matches training/scripts/mk4_train.py:build_obs exactly.
"""

from __future__ import annotations

from app.services.game_state import FightState

# Normalization constants (from training code)
HEALTH_MAX = 160.0
TIMER_MAX = 99.0
X_NORM = 15.0
DIST_NORM = 15.0


def build_obs(state: FightState, player: int = 1) -> list[float]:
    """14-float observation vector with combat signals.

    Layout: [self_hp, opp_hp, timer, self_x, opp_x, dist, facing,
             self_action, opp_action, self_y_vel, opp_airborne,
             self_hitstun, opp_hitstun, self_airborne]

    All values normalised to roughly [-1, 1] / [0, 1].

    Always built from the perspective of *player* (1 or 2): slot 0 is the
    calling agent's own health, slot 3 is its own X position.  This matches
    the self-play training environment where each worker observed itself as P1.
    """
    # Raw values — pick which side is "self" vs "opponent"
    if player == 2:
        self_hp_raw = state.p2_health or 0
        opp_hp_raw = state.p1_health or 0
        self_x_raw = state.p2_x or 0.0
        opp_x_raw = state.p1_x or 0.0
        self_action_raw = state.p2_action or 0.0
        opp_action_raw = state.p1_action or 0.0
        self_y_vel_raw = 0.0  # P2 Y velocity not tracked yet
        opp_airborne_raw = state.p1_airborne or 0.0
        self_hitstun_raw = state.p2_hitstun or 0.0
        opp_hitstun_raw = state.p1_hitstun or 0.0
        self_airborne_raw = state.p2_airborne or 0.0
    else:
        self_hp_raw = state.p1_health or 0
        opp_hp_raw = state.p2_health or 0
        self_x_raw = state.p1_x or 0.0
        opp_x_raw = state.p2_x or 0.0
        self_action_raw = state.p1_action or 0.0
        opp_action_raw = state.p2_action or 0.0
        self_y_vel_raw = state.p1_y_vel or 0.0
        opp_airborne_raw = state.p2_airborne or 0.0
        self_hitstun_raw = state.p1_hitstun or 0.0
        opp_hitstun_raw = state.p2_hitstun or 0.0
        self_airborne_raw = state.p1_airborne or 0.0

    # Normalize base signals
    self_hp = self_hp_raw / HEALTH_MAX
    opp_hp = opp_hp_raw / HEALTH_MAX
    timer = (state.timer or 99) / TIMER_MAX
    self_x = max(-1.0, min(1.0, self_x_raw / X_NORM))
    opp_x = max(-1.0, min(1.0, opp_x_raw / X_NORM))
    dist = min(1.0, abs(opp_x_raw - self_x_raw) / DIST_NORM)
    facing = 1.0 if opp_x_raw >= self_x_raw else -1.0

    # Combat signals (already normalized in game_state.py)
    self_action = self_action_raw
    opp_action = opp_action_raw
    self_y_vel = self_y_vel_raw
    opp_airborne = opp_airborne_raw
    self_hitstun = self_hitstun_raw
    opp_hitstun = opp_hitstun_raw
    self_airborne = self_airborne_raw

    return [
        self_hp, opp_hp, timer, self_x, opp_x, dist, facing,
        self_action, opp_action, self_y_vel, opp_airborne,
        self_hitstun, opp_hitstun, self_airborne
    ]


# Constants matching training code
RAW_OBS_DIM = 14


class FrameStack:
    """Stacks the last N observation frames into a single flat vector.

    Gives the MLP policy implicit access to velocity (position delta),
    damage rate (hp delta), and recent temporal context without an RNN.

    Copied from training/src/n64train/experiments/mk4_agent.py:FrameStack
    """

    def __init__(self, obs_dim: int = RAW_OBS_DIM, n_frames: int = 4) -> None:
        self.obs_dim = obs_dim
        self.n_frames = n_frames
        self.out_dim = obs_dim * n_frames
        self._buf: list[list[float]] = []

    def push(self, obs: list[float]) -> list[float]:
        """Add newest obs, return stacked vector (oldest -> newest)."""
        self._buf.append(obs)
        if len(self._buf) > self.n_frames:
            self._buf.pop(0)
        pad = self.n_frames - len(self._buf)
        frames = [[0.0] * self.obs_dim] * pad + self._buf
        out: list[float] = []
        for f in frames:
            out.extend(f)
        return out

    def reset(self) -> None:
        self._buf.clear()
