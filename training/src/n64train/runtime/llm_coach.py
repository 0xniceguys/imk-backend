"""
llm_coach.py — Two-tier LLM coaching for MK4 RL training.

Architecture
────────────
Two non-blocking coaching layers that plug into mk4_train.py:

  1. Episode Coach  (runs between episodes, every N episodes)
     • Reviews per-episode stats (rewards, win rate, health delta, action mix)
     • Calls LLM once per review, gets back a JSON RewardConfig patch
     • Applies the patch to Mk4ShapedRewardExtractor via update_config()
     • Also writes a human-readable "philosophy" log for the training run

  2. Micro Coach  (runs inside episodes, every N steps)
     • Runs in a daemon thread — NEVER blocks the 30 Hz training loop
     • Reads current game state (health, distance, timer) via MicroCoachState
     • Returns a 3-float tactical hint: [attack_weight, advance_weight, defend_weight]
     • Hint is appended to the observation vector (obs dims: 14 → 18)
     • If LLM hasn't responded yet, last hint is reused (staleness is tracked)

Usage
─────
    coach = LlmCoach(provider="openai", model="gpt-4o-mini", coach_every=10)
    micro = MicroCoach(provider="openai", model="gpt-4o-mini", interval_steps=90)

    # Local (free, recommended for training — no API costs):
    coach = LlmCoach(provider="ollama", model="llama3.2", coach_every=10)
    micro = MicroCoach(provider="ollama", model="llama3.2", interval_steps=90)
    # Requires: ollama pull llama3.2 && ollama serve

    # Between episodes:
    if ep_num % coach.coach_every == 0:
        coach.review(ep_stats, reward_extractor)

    # Inside episode (non-blocking):
    micro.tick(game_state)          # called every step
    hint = micro.latest_hint()      # [attack, advance, defend, freshness]
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Provider detection helpers ────────────────────────────────────────────────

def _openai_chat(model: str, messages: list[dict]) -> str:
    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(model=model, messages=messages,
                                          response_format={"type": "json_object"})
    return resp.choices[0].message.content or "{}"


def _anthropic_chat(model: str, messages: list[dict]) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    # Convert OpenAI-style messages to Anthropic format
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    human_msgs = [m for m in messages if m["role"] != "system"]
    resp = client.messages.create(
        model=model, max_tokens=512, system=system,
        messages=[{"role": m["role"], "content": m["content"]} for m in human_msgs],
    )
    return resp.content[0].text


def _gemini_chat(model: str, messages: list[dict]) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    gmodel = genai.GenerativeModel(model)
    prompt = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    resp = gmodel.generate_content(prompt)
    return resp.text


def _ollama_chat(model: str, messages: list[dict]) -> str:
    """Ollama local inference via its OpenAI-compatible /v1 endpoint."""
    import openai
    client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",   # Ollama ignores the key but the client requires one
    )
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or "{}"


def _call_llm(provider: str, model: str, messages: list[dict]) -> str:
    """Unified LLM call. Returns raw string (expected to be JSON)."""
    if provider == "openai":
        return _openai_chat(model, messages)
    if provider == "anthropic":
        return _anthropic_chat(model, messages)
    if provider == "gemini":
        return _gemini_chat(model, messages)
    if provider == "ollama":
        return _ollama_chat(model, messages)
    raise ValueError(f"Unknown provider: {provider!r}. Use 'openai', 'anthropic', 'gemini', or 'ollama'.")


def _parse_json_safe(text: str) -> dict:
    """Extract JSON from LLM output, tolerating markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("[LlmCoach] JSON parse failed: %s", text[:200])
        return {}


# ── Episode-level coach ───────────────────────────────────────────────────────

_EPISODE_SYSTEM = """
You are a Mortal Kombat 4 fighting game coach training an RL agent (P1).
The agent controls a fighter against a CPU opponent (P2).

You receive per-episode training stats and return a JSON patch for the reward
config. Your job: tune reward weights to push the agent toward better strategy.

Rules:
- Output ONLY valid JSON. No prose, no markdown.
- Only include keys you want to change. Omit unchanged keys.
- All floats must be within the allowed ranges shown below.
- Fighter philosophy / personality must never be changed.

Allowed keys and ranges:
  damage_dealt_scale  [0.5 – 3.0]
  damage_taken_scale  [0.5 – 3.0]
  approach_scale      [0.0 – 1.5]
  dist_penalty_scale  [0.0 – 0.5]
  win_bonus           [10.0 – 150.0]
  loss_penalty        [5.0 – 75.0]
  survival_per_step   [0.0 – 0.05]
  aggression          [0.0 – 2.0]
  idle_penalty        [-0.5 – 0.0]
  spam_scale_mult     [0.5 – 3.0]
  cooldown_mult       [0.5 – 3.0]
  whiff_mult          [0.5 – 3.0]
  anti_air_bonus      [0.0 – 8.0]
  punish_bonus        [0.0 – 8.0]
  reckless_jump_pen   [0.0 – 4.0]
  coach_note          (string — your reasoning, max 120 chars)
""".strip()

_EPISODE_USER_TMPL = """
Fighter: {name} | Style: {style}
Episodes reviewed: {n_eps} (every {coach_every} episodes)

Recent stats (last {n_eps} episodes):
  Win rate  : {win_rate:.1f}%
  Avg reward: {avg_reward:+.2f}
  Avg damage dealt  : {avg_dealt:+.1f}
  Avg damage taken  : {avg_taken:+.1f}
  Top 3 actions used: {top_actions}
  Most common action: {most_common}
  Approach reward   : {avg_approach:+.2f}
  Spam penalty      : {avg_spam:+.2f}

Current config:
{current_config}

Suggest reward weight changes to improve win rate and combat quality.
Remember: output ONLY JSON.
"""


@dataclass
class EpisodeStats:
    """Accumulated stats for a block of episodes passed to the coach."""
    n_eps:       int   = 0
    wins:        int   = 0
    total_reward: float = 0.0
    total_dealt:  float = 0.0
    total_taken:  float = 0.0
    total_approach: float = 0.0
    total_spam:   float = 0.0
    action_counts: dict[str, int] = field(default_factory=dict)

    def record(self, ep_log: dict) -> None:
        self.n_eps        += 1
        self.wins         += int(ep_log.get("won", False))
        self.total_reward += ep_log.get("reward", 0.0)
        self.total_dealt  += ep_log.get("r_dealt", 0.0)
        self.total_taken  += ep_log.get("r_taken", 0.0)
        self.total_approach += ep_log.get("r_approach", 0.0)
        self.total_spam   += ep_log.get("r_spam", 0.0)
        for action in ep_log.get("action_mix", {}).keys():
            self.action_counts[action] = self.action_counts.get(action, 0) + \
                                         ep_log["action_mix"][action]

    def reset(self) -> None:
        self.__init__()

    def summary(self) -> dict:
        n = max(1, self.n_eps)
        sorted_actions = sorted(self.action_counts.items(),
                                key=lambda x: x[1], reverse=True)
        return {
            "win_rate":    self.wins / n * 100,
            "avg_reward":  self.total_reward / n,
            "avg_dealt":   self.total_dealt / n,
            "avg_taken":   self.total_taken / n,
            "avg_approach": self.total_approach / n,
            "avg_spam":    self.total_spam / n,
            "top_actions": [a for a, _ in sorted_actions[:3]],
            "most_common": sorted_actions[0][0] if sorted_actions else "N/A",
        }


class LlmCoach:
    """
    Episode-level LLM coach. Fires every `coach_every` episodes,
    reviews stats, and updates the reward extractor's RewardConfig.

    Runs synchronously (between episodes — we have time).
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        coach_every: int = 10,
        fighter_name: str = "Fighter",
        fighter_style: str = "balanced",
        fighter_philosophy: str = "",
    ) -> None:
        self.provider          = provider
        self.model             = model
        self.coach_every       = coach_every
        self.stats             = EpisodeStats()
        self.last_config_patch: dict = {}
        self.notes: list[str]  = []
        self._fighter_name     = fighter_name
        self._fighter_style    = fighter_style
        self._philosophy       = fighter_philosophy

    def record_episode(self, ep_log: dict) -> None:
        """Call after every episode to accumulate stats."""
        self.stats.record(ep_log)

    def review(self, reward_extractor: Any) -> dict | None:
        """
        Call every `coach_every` episodes.
        Asks the LLM to review stats and patch the RewardConfig.
        Returns the patch dict (or None if LLM unavailable).
        """
        from n64train.runtime.rewards import RewardConfig

        summary = self.stats.summary()

        # Build current config snapshot for the prompt
        if reward_extractor.config is not None:
            cfg = reward_extractor.config
        else:
            cfg = RewardConfig(name=self._fighter_name, style=self._fighter_style)
        cfg_fields = {k: getattr(cfg, k) for k in RewardConfig.__dataclass_fields__
                      if k not in ("name", "style", "description", "philosophy")}
        current_config_str = json.dumps(cfg_fields, indent=2)

        prompt = _EPISODE_USER_TMPL.format(
            name=self._fighter_name,
            style=self._fighter_style,
            n_eps=self.stats.n_eps,
            coach_every=self.coach_every,
            win_rate=summary["win_rate"],
            avg_reward=summary["avg_reward"],
            avg_dealt=summary["avg_dealt"],
            avg_taken=summary["avg_taken"],
            avg_approach=summary["avg_approach"],
            avg_spam=summary["avg_spam"],
            top_actions=summary["top_actions"],
            most_common=summary["most_common"],
            current_config=current_config_str,
        )

        messages = [
            {"role": "system", "content": _EPISODE_SYSTEM},
            {"role": "user",   "content": prompt},
        ]

        try:
            raw = _call_llm(self.provider, self.model, messages)
        except Exception as exc:
            logger.warning("[LlmCoach] LLM call failed: %s", exc)
            self.stats.reset()
            return None

        patch = _parse_json_safe(raw)
        note  = patch.pop("coach_note", "")

        # Build new config by merging patch onto existing
        cfg_dict = {k: getattr(cfg, k) for k in RewardConfig.__dataclass_fields__}
        cfg_dict.update({k: v for k, v in patch.items()
                         if k in RewardConfig.__dataclass_fields__})
        cfg_dict["name"]      = self._fighter_name
        cfg_dict["style"]     = self._fighter_style
        cfg_dict["philosophy"] = self._philosophy  # never overwrite

        new_cfg = RewardConfig(**{k: v for k, v in cfg_dict.items()
                                  if k in RewardConfig.__dataclass_fields__})
        new_cfg.clamp()
        reward_extractor.update_config(new_cfg)

        self.last_config_patch = patch
        if note:
            self.notes.append(note)
            logger.info("[LlmCoach] Coach note: %s", note)

        logger.info("[LlmCoach] Config updated after %d episodes | win=%.1f%% | patch_keys=%s",
                    self.stats.n_eps, summary["win_rate"], list(patch.keys()))

        self.stats.reset()
        return patch


# ── Micro coach ───────────────────────────────────────────────────────────────

_MICRO_SYSTEM = """
You are a real-time tactics advisor for a Mortal Kombat 4 AI fighter.

Given the current mid-fight game state, output a JSON object with three weights
that sum to 1.0, representing tactical priorities RIGHT NOW:
  attack  — probability the fighter should try to land a hit
  advance — probability the fighter should close distance
  defend  — probability the fighter should block/retreat

Rules:
- Output ONLY valid JSON with exactly these keys: attack, advance, defend
- Values must be floats between 0.0 and 1.0 that sum to 1.0
- No prose, no extra keys.
""".strip()

_MICRO_USER_TMPL = """
P1 health: {p1_hp:.0f}/160  P2 health: {p2_hp:.0f}/160
Timer: {timer}s  Distance: {distance:.1f} units
P2 airborne: {p2_air}  P2 attacking: {p2_atk}
P1 airborne: {p1_air}
Last 5 actions: {last_actions}

What should P1 prioritize right now?
"""

# Static hint when LLM is unavailable: equal weights
_NEUTRAL_HINT = [1/3, 1/3, 1/3, 0.0]   # [attack, advance, defend, freshness]


@dataclass
class MicroCoachState:
    """Snapshot of game state passed to the micro coach."""
    p1_hp:       float = 160.0
    p2_hp:       float = 160.0
    timer:       int   = 99
    distance:    float = 5.0
    p2_airborne: bool  = False
    p2_attacking: bool  = False
    p1_airborne: bool  = False
    last_actions: list[str] = field(default_factory=list)


class MicroCoach:
    """
    Intra-episode micro coach. Runs every `interval_steps` steps.
    LLM call happens in a daemon thread — never blocks the 30 Hz loop.

    latest_hint() returns [attack, advance, defend, freshness]
      freshness = 1.0 if hint is recent, decays toward 0 as it gets stale.
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        interval_steps: int = 90,   # ~3 seconds at 33ms/step
        staleness_half_life: int = 180,  # hint decays to 0.5 freshness after this many steps
    ) -> None:
        self.provider          = provider
        self.model             = model
        self.interval_steps    = interval_steps
        self.staleness_half_life = staleness_half_life

        self._step_count       = 0
        self._last_hint_step   = -interval_steps  # force first call immediately
        self._hint             = list(_NEUTRAL_HINT)  # [attack, advance, defend, freshness]
        self._pending_state: MicroCoachState | None = None
        self._result_queue: queue.Queue = queue.Queue()
        self._lock             = threading.Lock()

    def tick(self, state: MicroCoachState) -> None:
        """
        Call every training step. Fires an async LLM call every interval_steps.
        Non-blocking — returns immediately.
        """
        self._step_count += 1

        # Poll for completed LLM result
        try:
            result = self._result_queue.get_nowait()
            with self._lock:
                self._hint = result
                self._last_hint_step = self._step_count
        except queue.Empty:
            pass

        # Check if it's time for a new micro-coach call
        steps_since_last = self._step_count - self._last_hint_step
        if steps_since_last >= self.interval_steps:
            self._last_hint_step = self._step_count  # reset so we don't spam
            state_snap = state  # already a dataclass copy
            threading.Thread(
                target=self._worker,
                args=(state_snap,),
                daemon=True,
            ).start()

    def latest_hint(self) -> list[float]:
        """
        Returns [attack_w, advance_w, defend_w, freshness] (4 floats).
        freshness: 1.0 = just got a new hint, decays exponentially with staleness.
        Add these 4 floats to the agent's observation vector.
        """
        with self._lock:
            steps_since = self._step_count - self._last_hint_step
            freshness = 0.5 ** (steps_since / max(1, self.staleness_half_life))
            return self._hint[:3] + [freshness]

    def reset(self) -> None:
        """Call at episode start to reset step counter and stale hint."""
        self._step_count = 0
        self._last_hint_step = -self.interval_steps
        with self._lock:
            self._hint = list(_NEUTRAL_HINT)

    def _worker(self, state: MicroCoachState) -> None:
        """Background thread: calls LLM and puts result in queue."""
        prompt = _MICRO_USER_TMPL.format(
            p1_hp=state.p1_hp,
            p2_hp=state.p2_hp,
            timer=state.timer,
            distance=state.distance,
            p2_air="yes" if state.p2_airborne else "no",
            p2_atk="yes" if state.p2_attacking else "no",
            p1_air="yes" if state.p1_airborne else "no",
            last_actions=", ".join(state.last_actions[-5:]) or "none",
        )
        messages = [
            {"role": "system", "content": _MICRO_SYSTEM},
            {"role": "user",   "content": prompt},
        ]
        try:
            raw  = _call_llm(self.provider, self.model, messages)
            data = _parse_json_safe(raw)
            atk  = float(data.get("attack",  1/3))
            adv  = float(data.get("advance", 1/3))
            dfd  = float(data.get("defend",  1/3))
            # Normalize to sum=1 (guard against malformed output)
            total = atk + adv + dfd
            if total > 0:
                atk, adv, dfd = atk/total, adv/total, dfd/total
            else:
                atk = adv = dfd = 1/3
            self._result_queue.put([atk, adv, dfd, 1.0])
        except Exception as exc:
            logger.warning("[MicroCoach] LLM call failed: %s", exc)
            # Don't update — keep last hint
