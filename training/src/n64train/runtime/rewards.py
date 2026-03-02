from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from n64train.runtime.types import RewardTerms, TracedState

HEALTH_MAX = 160.0
FIGHTING_RANGE = 3.0   # units — arena is only ~10-12 wide, attacks land at ~3 units
MAX_DIST = 15.0        # units — normalisation ceiling

# Anti-spam: which actions are "attacks" (have a cooldown enforcement)
ATTACK_ACTIONS = {
    'LOW_PUNCH', 'HIGH_PUNCH', 'LOW_KICK', 'HIGH_KICK',
    'JAB_COMBO', 'PUNISH', 'SPECIAL_1', 'SPECIAL_2', 'THROW_ATTEMPT',
}

SPAM_THRESHOLD      = 3     # consecutive same moves before penalty fires
SPAM_SCALE          = 1.0   # penalty per step OVER threshold (doubled from 0.5)
ATTACK_COOLDOWN     = 12    # steps — min gap between attacks (~1.2s, matches MK4 recovery)
COOLDOWN_PENALTY    = 2.0   # flat penalty per cooldown violation (doubled)
WHIFF_PENALTY       = 0.5   # penalty for attacking and dealing 0 damage (out-of-range attack)


class RewardExtractor:
    def compute(
        self,
        prev_state: TracedState | None,
        next_state: TracedState | None,
        action_history: list[str] | None = None,
    ) -> RewardTerms:
        raise NotImplementedError


@dataclass
class DeltaHealthRewardExtractor(RewardExtractor):
    """Simple health-delta only. Useful for debugging."""

    damage_scale: float = 1.0

    def compute(
        self,
        prev_state: TracedState | None,
        next_state: TracedState | None,
        action_history: list[str] | None = None,
    ) -> RewardTerms:
        if prev_state is None or next_state is None:
            return RewardTerms()
        dealt = 0.0
        taken = 0.0
        if prev_state.p2_health is not None and next_state.p2_health is not None:
            dealt = float(prev_state.p2_health - next_state.p2_health) * self.damage_scale
        if prev_state.p1_health is not None and next_state.p1_health is not None:
            hp1_lost = float(prev_state.p1_health - next_state.p1_health)  # positive when P1 takes damage
            taken = -hp1_lost * self.damage_scale  # negative reward
        return RewardTerms(damage_dealt=dealt, damage_taken=taken)


@dataclass
class Mk4ShapedRewardExtractor(RewardExtractor):
    """
    Rich shaped reward for a smart MK4 agent.

    Design goals:
    - Reward dealing damage, penalise taking damage (asymmetric — survival matters)
    - Approach reward: incentivise closing distance to be in attack range
    - Distance penalty: punish camping far away doing nothing
    - Win bonus / loss penalty: clear episode-level objectives
    - Survival bonus: small reward per step alive
    - Anti-spam: penalise repeating the same move and violating attack cooldown

    Anti-spam system:
      action_history: list of recent MacroAction.value strings, most recent LAST
      - If last SPAM_THRESHOLD+ actions are the same → scale penalty per extra repeat
      - If an attack is used within ATTACK_COOLDOWN steps of the last attack → cooldown penalty
    """

    damage_dealt_scale: float = 1.0
    damage_taken_scale: float = 1.5
    approach_scale:     float = 0.20   # 4× stronger than before — walking in is now properly rewarded
    dist_penalty_scale: float = 0.05   # slightly stronger too
    win_bonus:          float = 50.0
    loss_penalty:       float = 25.0
    survival_per_step:  float = 0.001

    def compute(
        self,
        prev_state: TracedState | None,
        next_state: TracedState | None,
        action_history: list[str] | None = None,
    ) -> RewardTerms:
        if prev_state is None or next_state is None:
            return RewardTerms()

        # ── Health delta ──────────────────────────────────────────────────────
        dealt = 0.0
        taken = 0.0
        if prev_state.p2_health is not None and next_state.p2_health is not None:
            hp2_lost = max(0.0, float(prev_state.p2_health - next_state.p2_health))
            dealt = hp2_lost * self.damage_dealt_scale
        if prev_state.p1_health is not None and next_state.p1_health is not None:
            # positive when P1 takes damage (prev > next); no max() clamp so real damage registers
            hp1_lost = float(prev_state.p1_health - next_state.p1_health)
            taken = -max(0.0, hp1_lost) * self.damage_taken_scale

        # Win/loss: trigger ONCE when health crosses zero
        win  = 0.0
        loss = 0.0
        if (next_state.p2_health is not None and next_state.p2_health <= 0
                and prev_state.p2_health is not None and prev_state.p2_health > 0):
            win = self.win_bonus
        if (next_state.p1_health is not None and next_state.p1_health <= 0
                and prev_state.p1_health is not None and prev_state.p1_health > 0):
            loss = -self.loss_penalty

        # ── Positional signals ────────────────────────────────────────────────
        approach = 0.0
        dist_pen = 0.0
        if (prev_state.p1_x is not None and prev_state.p2_x is not None and
                next_state.p1_x is not None and next_state.p2_x is not None):

            prev_dist = abs(prev_state.p2_x - prev_state.p1_x)
            next_dist = abs(next_state.p2_x - next_state.p1_x)

            if next_dist < prev_dist and prev_dist > FIGHTING_RANGE:
                approach = (prev_dist - next_dist) * self.approach_scale

            if next_dist > FIGHTING_RANGE:
                dist_pen = -self.dist_penalty_scale

        # ── Survival ──────────────────────────────────────────────────────────
        survival = self.survival_per_step

        # ── Anti-spam ─────────────────────────────────────────────────────────
        spam = 0.0
        if action_history and len(action_history) >= SPAM_THRESHOLD:
            current_action = action_history[-1]

            # 1. Repeat-action penalty — ONLY for attacks, not movement/defense.
            # Penalising ADVANCE/RETREAT/STAND_BLOCK spam overwhelms approach/survival
            # rewards and discourages normal spacing and blocking.
            if current_action in ATTACK_ACTIONS:
                streak = 0
                for act in reversed(action_history):
                    if act == current_action:
                        streak += 1
                    else:
                        break
                if streak >= SPAM_THRESHOLD:
                    spam -= SPAM_SCALE * (streak - SPAM_THRESHOLD + 1)

            # 2. Attack cooldown
            if current_action in ATTACK_ACTIONS:
                steps_since_last_attack = len(action_history)
                for i, act in enumerate(reversed(action_history[:-1])):
                    if act in ATTACK_ACTIONS:
                        steps_since_last_attack = i + 1
                        break
                if steps_since_last_attack < ATTACK_COOLDOWN:
                    spam -= COOLDOWN_PENALTY

            # 3. Whiff penalty — attack that dealt 0 damage = wasted move out of range
            if current_action in ATTACK_ACTIONS and dealt == 0.0:
                spam -= WHIFF_PENALTY

        return RewardTerms(
            damage_dealt=dealt,
            damage_taken=taken,
            win_bonus=win,
            loss_penalty=loss,
            approach_reward=approach,
            distance_penalty=dist_pen,
            survival=survival,
            spam_penalty=spam,
        )

