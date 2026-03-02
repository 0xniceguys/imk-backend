"""Tests for observation builder — specifically player-perspective mirroring."""
import pytest
from app.services.game_state import FightState
from app.agents.observation import build_obs, HEALTH_MAX, TIMER_MAX, X_NORM, DIST_NORM


@pytest.fixture
def asymmetric_state():
    return FightState(
        p1_health=120,
        p2_health=60,
        timer=50,
        p1_x=5.0,
        p2_x=-3.0,
    )


def test_build_obs_p1_default(asymmetric_state):
    """P1 perspective: own health at [0], own x at [3]."""
    obs = build_obs(asymmetric_state, player=1)
    assert len(obs) == 7
    assert abs(obs[0] - 120 / HEALTH_MAX) < 1e-5, "slot 0 should be P1 health"
    assert abs(obs[1] - 60 / HEALTH_MAX) < 1e-5, "slot 1 should be P2 health"
    assert abs(obs[3] - 5.0 / X_NORM) < 1e-5, "slot 3 should be P1 x"
    assert abs(obs[4] - (-3.0 / X_NORM)) < 1e-5, "slot 4 should be P2 x"


def test_build_obs_p2_mirrored(asymmetric_state):
    """P2 perspective: own health (60) at [0], opponent health (120) at [1]."""
    obs = build_obs(asymmetric_state, player=2)
    assert len(obs) == 7
    assert abs(obs[0] - 60 / HEALTH_MAX) < 1e-5, "slot 0 should be P2 (self) health"
    assert abs(obs[1] - 120 / HEALTH_MAX) < 1e-5, "slot 1 should be P1 (opp) health"
    assert abs(obs[3] - (-3.0 / X_NORM)) < 1e-5, "slot 3 should be P2 (self) x"
    assert abs(obs[4] - 5.0 / X_NORM) < 1e-5, "slot 4 should be P1 (opp) x"


def test_build_obs_perspectives_differ(asymmetric_state):
    """P1 and P2 observations are not the same for asymmetric state."""
    obs_p1 = build_obs(asymmetric_state, player=1)
    obs_p2 = build_obs(asymmetric_state, player=2)
    assert obs_p1 != obs_p2


def test_build_obs_symmetric_state():
    """When both players have identical stats, P1 and P2 obs are identical."""
    state = FightState(p1_health=100, p2_health=100, timer=50, p1_x=0.0, p2_x=0.0)
    obs_p1 = build_obs(state, player=1)
    obs_p2 = build_obs(state, player=2)
    for a, b in zip(obs_p1, obs_p2):
        assert abs(a - b) < 1e-5


def test_build_obs_p1_default_param():
    """build_obs with no player arg behaves identically to player=1."""
    state = FightState(p1_health=80, p2_health=40, timer=30, p1_x=2.0, p2_x=-1.0)
    assert build_obs(state) == build_obs(state, player=1)


def test_build_obs_distance_same_both_perspectives(asymmetric_state):
    """Distance (slot 5) is the absolute gap — same regardless of perspective."""
    obs_p1 = build_obs(asymmetric_state, player=1)
    obs_p2 = build_obs(asymmetric_state, player=2)
    assert abs(obs_p1[5] - obs_p2[5]) < 1e-5


def test_build_obs_facing_flipped_when_mirrored(asymmetric_state):
    """Facing (slot 6) flips sign between P1 and P2 perspectives when positions differ."""
    obs_p1 = build_obs(asymmetric_state, player=1)  # P2 is to left of P1: facing = -1
    obs_p2 = build_obs(asymmetric_state, player=2)  # P1 is to right of P2: facing = +1
    assert obs_p1[6] != obs_p2[6]
    assert obs_p1[6] * obs_p2[6] < 0  # opposite signs
