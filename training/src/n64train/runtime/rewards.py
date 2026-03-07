from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from n64train.runtime.types import RewardTerms, TracedState

HEALTH_MAX = 160.0    # normalized from u32 0x10000 in mk4_tracing.py
FIGHTING_RANGE = 4.0   # units — slightly wider range to reward sustained pressure
MAX_DIST = 30.0        # units — normalisation ceiling (actual distances reach 30+)

# Anti-spam: which actions are "attacks" (have a cooldown enforcement)
ATTACK_ACTIONS = {
    'LOW_PUNCH', 'HIGH_PUNCH', 'LOW_KICK', 'HIGH_KICK',
    'JAB_COMBO', 'PUNISH', 'SPECIAL_1', 'SPECIAL_2', 'THROW_ATTEMPT',
}
MOVEMENT_ACTIONS = {
    'ADVANCE', 'RETREAT', 'RUN', 'JUMP_FORWARD', 'JUMP_BACK',
    'JUMP_NEUTRAL', 'SIDE_STEP_IN', 'SIDE_STEP_OUT', 'CROUCH',
}
JUMP_ACTIONS = {'JUMP_FORWARD', 'JUMP_BACK', 'JUMP_NEUTRAL'}
FORWARD_ENGAGE_ACTIONS = {'ADVANCE', 'RUN', 'JUMP_FORWARD'}
RUNAWAY_ACTIONS = {'RETREAT', 'JUMP_BACK', 'NEUTRAL'}

SPAM_THRESHOLD      = 6     # tighter threshold to catch alternating spam loops
SPAM_SCALE          = 0.20  # stronger streak penalty once threshold is exceeded
ATTACK_COOLDOWN     = 6     # force more commitment between repeated attack attempts
COOLDOWN_PENALTY    = 0.18  # flat penalty per cooldown violation
WHIFF_PENALTY       = 0.12  # restore whiff penalty; scaled up when whiffing at range
MOVE_FLIP_PENALTY   = 0.05  # penalize immediate ADVANCE<->RETREAT direction jitter
JUMP_SPAM_PENALTY   = 0.03  # penalize excessive jump frequency in a short window
JUMP_SPAM_WINDOW    = 8


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
    approach_scale:      float = 1.0
    dist_penalty_scale:  float = 0.15

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
    aggression:          float = 0.5   # bonus when attack CONNECTS in range (hit confirmation)
    idle_penalty:        float = 0.0   # per-step penalty for NEUTRAL action
    positioning_bonus:   float = 0.15  # per-step reward for being at fighting range (footsies)
    move_flip_penalty:   float = MOVE_FLIP_PENALTY
    jump_spam_penalty:   float = JUMP_SPAM_PENALTY
    # Engagement shaping while outside fighting range.
    engage_forward_bonus: float = 0.35
    retreat_far_penalty:  float = 0.24
    jump_back_far_penalty: float = 0.28
    neutral_far_penalty:  float = 0.18
    runaway_step_penalty: float = 0.24

    # ── New signals-based terms (verified RAM data 2026-03-03) ────────────────
    # Reward hitting P2 while P2 is airborne — teaches anti-air
    anti_air_bonus:      float = 3.0
    # Reward attacking during verified P2 hitstun windows. Disabled by default
    # until p2_hitstun address isolation is re-verified.
    punish_bonus:        float = 0.0
    # Penalty for attacking while P1 is airborne (random jump attacks)
    reckless_jump_pen:   float = 0.5
    # Extra damage multiplier when P2 was in hitstun during our hit (confirm on committal)
    hitstun_damage_mult: float = 0.5

    def clamp(self) -> 'RewardConfig':
        """Clamp all floats to safe ranges. Called after every LLM update."""
        self.damage_dealt_scale  = max(0.5, min(5.0, self.damage_dealt_scale))   # min 0.5: never zero out combat signal
        self.damage_taken_scale  = max(0.8, min(5.0, self.damage_taken_scale))   # keep defense pressure meaningful
        self.approach_scale      = max(0.0, min(1.0, self.approach_scale))
        self.dist_penalty_scale  = max(0.0, min(1.0, self.dist_penalty_scale))
        self.win_bonus           = max(0.0, min(200.0, self.win_bonus))
        self.loss_penalty        = max(0.0, min(100.0, self.loss_penalty))
        self.survival_per_step   = max(0.0, min(0.1,  self.survival_per_step))
        self.spam_scale_mult     = max(0.0, min(2.0,  self.spam_scale_mult))   # max 2.0: prevent spam from dominating
        self.cooldown_mult       = max(0.0, min(2.0,  self.cooldown_mult))   # max 2.0: same
        self.whiff_mult          = max(0.0, min(1.0,  self.whiff_mult))      # max 1.0: whiff is disabled by default
        self.aggression          = max(0.0, min(2.0,  self.aggression))
        self.idle_penalty        = max(-0.15, min(0.0, self.idle_penalty))
        self.positioning_bonus   = max(0.0, min(0.2, self.positioning_bonus))
        self.move_flip_penalty   = max(0.0, min(0.2, self.move_flip_penalty))
        self.jump_spam_penalty   = max(0.0, min(0.2, self.jump_spam_penalty))
        self.engage_forward_bonus = max(0.0, min(0.4, self.engage_forward_bonus))
        self.retreat_far_penalty = max(0.0, min(0.6, self.retreat_far_penalty))
        self.jump_back_far_penalty = max(0.0, min(0.6, self.jump_back_far_penalty))
        self.neutral_far_penalty = max(0.0, min(0.6, self.neutral_far_penalty))
        self.runaway_step_penalty = max(0.0, min(0.6, self.runaway_step_penalty))
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
    approach_scale:     float = 1.0
    dist_penalty_scale: float = 0.15
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
        move_flip_p   = self._get('move_flip_penalty')
        jump_spam_p   = self._get('jump_spam_penalty')
        engage_forward_b = self._get('engage_forward_bonus')
        retreat_far_pen = self._get('retreat_far_penalty')
        jump_back_far_pen = self._get('jump_back_far_penalty')
        neutral_far_pen = self._get('neutral_far_penalty')
        runaway_step_pen = self._get('runaway_step_penalty')

        # ── Health delta ──────────────────────────────────────────────────────
        dealt = 0.0
        taken = 0.0

        # Pull RAM-verified extras from both states
        prev_ex = (prev_state.extras or {}) if hasattr(prev_state, 'extras') else {}
        next_ex = (next_state.extras or {}) if hasattr(next_state, 'extras') else {}

        # Was P2 airborne *before* this step? (anti-air context)
        p2_was_airborne = float(prev_ex.get('p2_airborne', 0.0)) > 0.5
        # Punish context:
        # 1) Preferred path: real P2 recovery/hitstun signal when verified.
        # 2) Fallback path: decoded move signatures + short recent-attack window.
        p2_hitstun_verified = float(prev_ex.get('p2_hitstun_verified', 0.0)) > 0.5
        p2_attack_sig_verified = float(prev_ex.get('p2_attack_sig_verified', 0.0)) > 0.5
        p2_attack_active = float(prev_ex.get('p2_hitstun', 0.0)) > 0.0
        p2_recent_attack = float(prev_ex.get('p2_recent_attack', 0.0)) > 0.0
        if p2_hitstun_verified:
            p2_was_committing = p2_attack_active
        else:
            p2_was_committing = p2_attack_sig_verified and (p2_attack_active or p2_recent_attack)
        # Is P1 currently airborne? (reckless jump attack)
        p1_is_airborne = float(next_ex.get('p1_airborne', 0.0)) > 0.5

        if prev_state.p2_health is not None and next_state.p2_health is not None:
            hp2_lost = max(0.0, float(prev_state.p2_health - next_state.p2_health))
            # Base damage reward
            dealt = hp2_lost * dealt_scale
            # Bonus multiplier: extra reward when we landed hit while P2 was committing
            if hp2_lost > 0 and p2_was_committing:
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
        prev_dist = None
        next_dist = None
        pos_bonus_val = self._get('positioning_bonus')
        if (prev_state.p1_x is not None and prev_state.p2_x is not None and
                next_state.p1_x is not None and next_state.p2_x is not None):

            prev_dist    = abs(prev_state.p2_x - prev_state.p1_x)
            next_dist    = abs(next_state.p2_x - next_state.p1_x)
            current_dist = next_dist

            if next_dist < prev_dist and prev_dist > FIGHTING_RANGE:
                approach = (prev_dist - next_dist) * app_scale
            elif next_dist > prev_dist and next_dist > FIGHTING_RANGE:
                # Penalize moving AWAY when already outside fighting range
                approach = -(next_dist - prev_dist) * app_scale * 0.5
            if next_dist > FIGHTING_RANGE:
                # Scale distance penalty by how far outside fighting range we are.
                overshoot = min(1.0, max(0.0, (next_dist - FIGHTING_RANGE) / (MAX_DIST - FIGHTING_RANGE)))
                dist_pen = -(dist_pen_s * (1.0 + 2.0 * overshoot))
            elif next_dist <= FIGHTING_RANGE:
                # Reward staying in fighting range — teaches the agent to hold position
                approach += pos_bonus_val

        # ── Survival ──────────────────────────────────────────────────────────
        survival = survival_s

        # ── Hit confirmation bonus (LLM-tunable) ──────────────────────────────
        # Only rewards attacks that actually CONNECT — prevents button mashing
        current_action = action_history[-1] if action_history else None
        aggression_bonus = 0.0
        if (aggression > 0 and dealt > 0
                and current_action in ATTACK_ACTIONS
                and current_dist is not None and current_dist <= FIGHTING_RANGE):
            aggression_bonus = aggression

        positioning = 0.0

        # ── Idle penalty (LLM-tunable) ────────────────────────────────────────
        idle_bonus = idle_pen if current_action == 'NEUTRAL' else 0.0
        engagement_bonus = 0.0
        engagement_penalty = 0.0
        if current_dist is not None and current_dist > FIGHTING_RANGE:
            if current_action in FORWARD_ENGAGE_ACTIONS:
                # Reward forward pressure even if opponent is backing away.
                engagement_bonus += engage_forward_b
                if prev_dist is not None and next_dist is not None:
                    if next_dist < prev_dist:
                        engagement_bonus += 0.5 * engage_forward_b
                    elif next_dist > prev_dist:
                        engagement_bonus -= 0.25 * engage_forward_b
            if current_action == 'RETREAT':
                engagement_penalty -= retreat_far_pen
            elif current_action == 'JUMP_BACK':
                engagement_penalty -= jump_back_far_pen
            elif current_action == 'NEUTRAL':
                engagement_penalty -= neutral_far_pen
        if (current_action in RUNAWAY_ACTIONS and prev_dist is not None and next_dist is not None
                and next_dist > prev_dist):
            engagement_penalty -= runaway_step_pen

        # ── RAM-signal shaped bonuses ─────────────────────────────────────────
        # 1. Anti-air: bonus when we dealt damage AND P2 was airborne beforehand
        anti_air = 0.0
        if dealt > 0 and p2_was_airborne:
            anti_air = anti_air_b

        # 2. Punish bonus: counter-attack that actually CONNECTS during P2 recovery
        punish = 0.0
        if dealt > 0 and p2_was_committing:
            punish = punish_b

        # 3. Reckless jump penalty: penalise attacking while P1 is in the air
        reckless = 0.0
        if current_action in ATTACK_ACTIONS and p1_is_airborne:
            reckless = -reckless_j

        # ── Anti-spam (multipliers now LLM-tunable) ───────────────────────────
        spam = 0.0
        if current_action and action_history and len(action_history) >= 2:
            prev_action = action_history[-2]
            if ((current_action == 'ADVANCE' and prev_action == 'RETREAT')
                    or (current_action == 'RETREAT' and prev_action == 'ADVANCE')):
                spam -= move_flip_p
        if current_action in JUMP_ACTIONS and action_history:
            recent = action_history[-JUMP_SPAM_WINDOW:]
            jump_count = sum(1 for act in recent if act in JUMP_ACTIONS)
            if jump_count > 2:
                spam -= jump_spam_p * float(jump_count - 2)
        if current_action and action_history:
            # 1. Attack cooldown (always active; no threshold gate)
            if current_action in ATTACK_ACTIONS:
                steps_since_last_attack = ATTACK_COOLDOWN
                for i, act in enumerate(reversed(action_history[:-1])):
                    if act in ATTACK_ACTIONS:
                        steps_since_last_attack = i + 1
                        break
                if steps_since_last_attack < ATTACK_COOLDOWN:
                    spam -= COOLDOWN_PENALTY * cool_mult

            # 2. Whiff penalty (always active; heavier when too far to hit)
            if current_action in ATTACK_ACTIONS and dealt == 0.0:
                far_mult = 1.0
                if current_dist is not None and current_dist > FIGHTING_RANGE:
                    far_mult = 1.75
                spam -= WHIFF_PENALTY * whiff_mult * far_mult

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

        return RewardTerms(
            damage_dealt=dealt,
            damage_taken=taken,
            win_bonus=win,
            loss_penalty=loss,
            approach_reward=approach + aggression_bonus + anti_air + punish + positioning + engagement_bonus,
            distance_penalty=dist_pen + idle_bonus + reckless + engagement_penalty,
            survival=survival,
            spam_penalty=spam,
        )
