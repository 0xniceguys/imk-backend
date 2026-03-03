from __future__ import annotations

from dataclasses import dataclass, field
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

SPAM_THRESHOLD      = 5     # consecutive same moves before penalty fires (more steps per second)
SPAM_SCALE          = 1.0   # penalty per step OVER threshold
ATTACK_COOLDOWN     = 24    # steps — min gap between attacks (~0.8s at 33ms/step, matches MK4 recovery)
COOLDOWN_PENALTY    = 2.0   # flat penalty per cooldown violation
WHIFF_PENALTY       = 0.5   # penalty for attacking and dealing 0 damage (out-of-range attack)


@dataclass
class RewardConfig:
    """
    Live-tunable reward weights controlled by the LLM coach.

    All fields match the defaults of Mk4ShapedRewardExtractor so the system
    is fully backward-compatible. The LLM coach updates this between episodes;
    the extractor reads it on every compute() call.
    """
    # Fighter identity (set once at init, never overwritten by LLM)
    name:        str = 'unnamed'
    style:       str = 'balanced'
    description: str = ''
    philosophy:  str = ''  # locked personality — never overwritten

    # Damage scaling
    damage_dealt_scale:  float = 1.0
    damage_taken_scale:  float = 1.5

    # Positional signals
    approach_scale:      float = 0.20
    dist_penalty_scale:  float = 0.05

    # Episode outcome
    win_bonus:           float = 50.0
    loss_penalty:        float = 25.0

    # Per-step survival
    survival_per_step:   float = 0.001

    # Anti-spam amplifiers (multiplied into base constants)
    spam_scale_mult:     float = 1.0
    cooldown_mult:       float = 1.0
    whiff_mult:          float = 1.0

    # LLM-introduced extras
    aggression:          float = 0.0   # bonus per attacking step in range
    idle_penalty:        float = 0.0   # per-step penalty for NEUTRAL action

    # ── New signals-based terms (verified RAM data 2026-03-03) ────────────────
    # Reward hitting P2 while P2 is airborne — teaches anti-air
    anti_air_bonus:      float = 3.0
    # Reward attacking while P2 hitstun>0 (P2 whiffed, we punish the opening)
    punish_bonus:        float = 2.0
    # Penalty for attacking while P1 is airborne (random jump attacks)
    reckless_jump_pen:   float = 0.5
    # Extra damage multiplier when P2 was in hitstun during our hit (confirm on committal)
    hitstun_damage_mult: float = 0.5

    def clamp(self) -> 'RewardConfig':
        """Clamp all floats to safe ranges. Called after every LLM update."""
        self.damage_dealt_scale  = max(0.0, min(5.0, self.damage_dealt_scale))
        self.damage_taken_scale  = max(0.0, min(5.0, self.damage_taken_scale))
        self.approach_scale      = max(0.0, min(2.0, self.approach_scale))
        self.dist_penalty_scale  = max(0.0, min(1.0, self.dist_penalty_scale))
        self.win_bonus           = max(0.0, min(200.0, self.win_bonus))
        self.loss_penalty        = max(0.0, min(100.0, self.loss_penalty))
        self.survival_per_step   = max(0.0, min(0.1,  self.survival_per_step))
        self.spam_scale_mult     = max(0.0, min(5.0,  self.spam_scale_mult))
        self.cooldown_mult       = max(0.0, min(5.0,  self.cooldown_mult))
        self.whiff_mult          = max(0.0, min(5.0,  self.whiff_mult))
        self.aggression          = max(0.0, min(3.0,  self.aggression))
        self.idle_penalty        = max(-1.0, min(0.0, self.idle_penalty))
        self.anti_air_bonus      = max(0.0, min(10.0, self.anti_air_bonus))
        self.punish_bonus        = max(0.0, min(10.0, self.punish_bonus))
        self.reckless_jump_pen   = max(0.0, min(5.0,  self.reckless_jump_pen))
        self.hitstun_damage_mult = max(0.0, min(3.0,  self.hitstun_damage_mult))
        return self


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

    Accepts an optional RewardConfig for live LLM-driven weight tuning.
    When config=None, falls back to the original hardcoded defaults.
    """

    damage_dealt_scale: float = 1.0
    damage_taken_scale: float = 1.5
    approach_scale:     float = 0.20
    dist_penalty_scale: float = 0.05
    win_bonus:          float = 50.0
    loss_penalty:       float = 25.0
    survival_per_step:  float = 0.001

    # Live-swappable config from LLM coach (overrides field defaults when set)
    config: RewardConfig | None = None

    def update_config(self, cfg: RewardConfig) -> None:
        """Hot-swap the reward config mid-training. Thread-safe for reads since
        Python assignment is atomic. Called by the coaching thread."""
        self.config = cfg.clamp()

    def _get(self, attr: str) -> float:
        """Return value from live config if set, otherwise use extractor field
        default, or the RewardConfig dataclass default for LLM-only fields."""
        if self.config is not None:
            return getattr(self.config, attr)
        # Field exists on the extractor itself (the original hardcoded weights)
        if hasattr(self, attr):
            return getattr(self, attr)
        # LLM-only field (aggression, idle_penalty, spam_scale_mult, etc.)
        # — default to RewardConfig's own default value when no config is set.
        return getattr(RewardConfig, attr, 0.0) if hasattr(RewardConfig, attr) \
            else RewardConfig.__dataclass_fields__[attr].default if attr in RewardConfig.__dataclass_fields__ \
            else 0.0

    def compute(
        self,
        prev_state: TracedState | None,
        next_state: TracedState | None,
        action_history: list[str] | None = None,
    ) -> RewardTerms:
        if prev_state is None or next_state is None:
            return RewardTerms()

        # Resolve all weights through _get() so LLM config updates take effect.
        dealt_scale   = self._get('damage_dealt_scale')
        taken_scale   = self._get('damage_taken_scale')
        app_scale     = self._get('approach_scale')
        dist_pen_s    = self._get('dist_penalty_scale')
        win_b         = self._get('win_bonus')
        loss_p        = self._get('loss_penalty')
        survival_s    = self._get('survival_per_step')
        aggression    = self._get('aggression')
        idle_pen      = self._get('idle_penalty')
        spam_mult     = self._get('spam_scale_mult')
        cool_mult     = self._get('cooldown_mult')
        whiff_mult    = self._get('whiff_mult')
        anti_air_b    = self._get('anti_air_bonus')
        punish_b      = self._get('punish_bonus')
        reckless_j    = self._get('reckless_jump_pen')
        hst_mult      = self._get('hitstun_damage_mult')

        # ── Health delta ──────────────────────────────────────────────────────
        dealt = 0.0
        taken = 0.0

        # Pull RAM-verified extras from both states
        prev_ex = (prev_state.extras or {}) if hasattr(prev_state, 'extras') else {}
        next_ex = (next_state.extras or {}) if hasattr(next_state, 'extras') else {}

        # Was P2 airborne *before* this step? (anti-air context)
        p2_was_airborne = float(prev_ex.get('p2_airborne', 0.0)) > 0.5
        # Was P2's hitbox active *before* this step? (punish window)
        p2_was_attacking = float(prev_ex.get('p2_hitstun', 0.0)) > 0.0
        # Is P1 currently airborne? (reckless jump attack)
        p1_is_airborne = float(next_ex.get('p1_airborne', 0.0)) > 0.5

        if prev_state.p2_health is not None and next_state.p2_health is not None:
            hp2_lost = max(0.0, float(prev_state.p2_health - next_state.p2_health))
            # Base damage reward
            dealt = hp2_lost * dealt_scale
            # Bonus multiplier: extra reward when we landed hit while P2 was committing
            if hp2_lost > 0 and p2_was_attacking:
                dealt += hp2_lost * hst_mult
        if prev_state.p1_health is not None and next_state.p1_health is not None:
            hp1_lost = float(prev_state.p1_health - next_state.p1_health)
            taken = -max(0.0, hp1_lost) * taken_scale

        # Win/loss: trigger ONCE when health crosses zero
        win  = 0.0
        loss = 0.0
        if (next_state.p2_health is not None and next_state.p2_health <= 0
                and prev_state.p2_health is not None and prev_state.p2_health > 0):
            win = win_b
        if (next_state.p1_health is not None and next_state.p1_health <= 0
                and prev_state.p1_health is not None and prev_state.p1_health > 0):
            loss = -loss_p

        # ── Positional signals ────────────────────────────────────────────────
        approach = 0.0
        dist_pen = 0.0
        current_dist = None
        if (prev_state.p1_x is not None and prev_state.p2_x is not None and
                next_state.p1_x is not None and next_state.p2_x is not None):

            prev_dist    = abs(prev_state.p2_x - prev_state.p1_x)
            next_dist    = abs(next_state.p2_x - next_state.p1_x)
            current_dist = next_dist

            if next_dist < prev_dist and prev_dist > FIGHTING_RANGE:
                approach = (prev_dist - next_dist) * app_scale
            if next_dist > FIGHTING_RANGE:
                dist_pen = -dist_pen_s

        # ── Survival ──────────────────────────────────────────────────────────
        survival = survival_s

        # ── Aggression bonus (LLM-tunable) ────────────────────────────────────
        current_action = action_history[-1] if action_history else None
        aggression_bonus = 0.0
        if (aggression > 0 and current_action in ATTACK_ACTIONS
                and current_dist is not None and current_dist <= FIGHTING_RANGE):
            aggression_bonus = aggression

        # ── Idle penalty (LLM-tunable) ────────────────────────────────────────
        idle_bonus = idle_pen if current_action == 'NEUTRAL' else 0.0

        # ── RAM-signal shaped bonuses ─────────────────────────────────────────
        # 1. Anti-air: bonus when we dealt damage AND P2 was airborne beforehand
        anti_air = 0.0
        if dealt > 0 and p2_was_airborne:
            anti_air = anti_air_b

        # 2. Punish bonus: bonus for attacking while P2 had active hitbox (punish whiff)
        punish = 0.0
        if current_action in ATTACK_ACTIONS and p2_was_attacking:
            punish = punish_b

        # 3. Reckless jump penalty: penalise attacking while P1 is in the air
        reckless = 0.0
        if current_action in ATTACK_ACTIONS and p1_is_airborne:
            reckless = -reckless_j

        # ── Anti-spam (multipliers now LLM-tunable) ───────────────────────────
        spam = 0.0
        if current_action and action_history and len(action_history) >= SPAM_THRESHOLD:
            # 1. Repeat-attack penalty
            if current_action in ATTACK_ACTIONS:
                streak = 0
                for act in reversed(action_history):
                    if act == current_action:
                        streak += 1
                    else:
                        break
                if streak >= SPAM_THRESHOLD:
                    spam -= SPAM_SCALE * spam_mult * (streak - SPAM_THRESHOLD + 1)

            # 2. Attack cooldown
            if current_action in ATTACK_ACTIONS:
                steps_since_last_attack = len(action_history)
                for i, act in enumerate(reversed(action_history[:-1])):
                    if act in ATTACK_ACTIONS:
                        steps_since_last_attack = i + 1
                        break
                if steps_since_last_attack < ATTACK_COOLDOWN:
                    spam -= COOLDOWN_PENALTY * cool_mult

            # 3. Whiff penalty
            if current_action in ATTACK_ACTIONS and dealt == 0.0:
                spam -= WHIFF_PENALTY * whiff_mult

        return RewardTerms(
            damage_dealt=dealt,
            damage_taken=taken,
            win_bonus=win,
            loss_penalty=loss,
            approach_reward=approach + aggression_bonus + anti_air + punish,
            distance_penalty=dist_pen + idle_bonus + reckless,
            survival=survival,
            spam_penalty=spam,
        )
