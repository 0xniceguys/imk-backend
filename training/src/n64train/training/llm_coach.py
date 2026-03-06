"""
llm_coach.py — LLM-driven dynamic reward coaching for MK4 agents.

Backend: Amazon Bedrock — Claude 3.5 Haiku via HTTP bearer-token API.
Set the env var before launching training:

  export AWS_BEARER_TOKEN_BEDROCK="ABSKQm..."
  export AWS_DEFAULT_REGION="us-east-1"   # or your region

Architecture: Three-layer coaching stack
  1. Philosopher  (once at startup) — generates fighter identity + locked philosophy
  2. Macro coach  (every N episodes) — deep analysis → 2-4 weight adjustments
  3. Micro coach  (every episode)   — fast low-token nudge → ±2 weight changes
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from n64train.runtime.rewards import RewardConfig

# ── Bedrock client setup ───────────────────────────────────────────────────────
_BEARER_TOKEN = os.environ.get('AWS_BEARER_TOKEN_BEDROCK', '')
_REGION       = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
_AVAILABLE    = bool(_BEARER_TOKEN)

if not _AVAILABLE:
    print('[llm_coach] WARNING: AWS_BEARER_TOKEN_BEDROCK not set — coach in dry_run mode')

# Use the US cross-region inference profile ID — required for API key auth in us-east-1.
# Plain regional IDs (anthropic.claude-*) work with IAM SigV4 but not bearer token keys.
# See: https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
MODEL_ID       = 'us.anthropic.claude-3-5-haiku-20241022-v1:0'

MICRO_MAX_TOKENS = 200
MACRO_MAX_TOKENS = 600

# Dead-zone: skip coaching when win rate is in a healthy competitive band
COACHING_DEADZONE_LO = 0.45
COACHING_DEADZONE_HI = 0.55

# Episodes between macro review cycles
# 200 episodes = ~10 PPO updates = enough for policy to converge under stable rewards
# before the LLM coach re-evaluates (was 20 — too frequent, caused non-stationarity)
MACRO_REVIEW_EVERY = 200

MAX_HISTORY = 5  # keep more history so LLM sees longer trends

# ── Cost tracking (Claude 3.5 Haiku pricing, USD per 1K tokens) ───────────────
_COST_PER_1K_IN  = 0.0008   # $0.0008 per 1K input tokens
_COST_PER_1K_OUT = 0.004    # $0.004  per 1K output tokens

_session_tokens_in:  int   = 0
_session_tokens_out: int   = 0
_session_calls:      int   = 0
_session_cost_usd:   float = 0.0


def cost_summary() -> dict:
    """Return and print Bedrock session cost summary."""
    summary = {
        'calls':      _session_calls,
        'tokens_in':  _session_tokens_in,
        'tokens_out': _session_tokens_out,
        'cost_usd':   round(_session_cost_usd, 4),
    }
    print(
        f'[llm_coach] Bedrock session: '
        f'{_session_calls} calls  '
        f'in={_session_tokens_in:,}  out={_session_tokens_out:,}  '
        f'cost=${_session_cost_usd:.4f}'
    )
    return summary


# ── Stats dataclasses ──────────────────────────────────────────────────────────

@dataclass
class RoundStats:
    """Stats from a single episode. Passed to micro_coach()."""
    won:                  bool
    damage_dealt:         float
    damage_taken:         float
    ep_steps:             int
    win_rate:             float
    action_diversity_pct: float = 0.0


@dataclass
class MacroStats:
    """Aggregated stats over N episodes. Passed to review_and_adjust()."""
    episodes:             int
    win_rate:             float
    avg_damage_dealt:     float
    avg_damage_taken:     float
    avg_ep_steps:         float
    wins_by_ko:           int = 0
    close_losses:         int = 0
    avg_spam_penalty:     float = 0.0   # avg spam penalty per episode (negative)
    avg_reward:           float = 0.0   # avg total reward per episode
    # Behavioral stats — what the agent is actually doing
    hit_rate_pct:         float = 0.0   # attacks that connected / total attacks (%)
    attack_pct:           float = 0.0   # % of steps spent attacking
    move_pct:             float = 0.0   # % of steps spent moving
    idle_pct:             float = 0.0   # % of steps doing nothing
    current_config:       dict[str, Any] | None = None
    # Terminal outcome breakdown
    ko_wins:              int = 0
    timer_wins:           int = 0
    wall_wins:            int = 0
    ko_losses:            int = 0
    timer_losses:         int = 0
    wall_losses:          int = 0
    avg_dist_pen:         float = 0.0   # avg distance penalty per episode (includes engagement penalty)
    avg_approach:         float = 0.0   # avg approach reward per episode (includes engagement bonus)


@dataclass
class CoachingMemoryEntry:
    cycle:           int
    win_rate_before: float
    win_rate_after:  float
    changes:         dict[str, float]
    coach_note:      str = ''


# ── Bedrock HTTP helper ────────────────────────────────────────────────────────

_DISABLED_PERMANENTLY = False   # set True after first unreachable failure (DNS, timeout, etc.)


def _call_bedrock(prompt: str, max_tokens: int, retries: int = 2) -> str | None:
    """
    Call Claude 3.5 Haiku via Bedrock Converse API — the correct unified API
    for multi-model Bedrock access. Uses /model/{id}/converse endpoint.

    Converse API request schema:
      { "messages": [{"role": "user", "content": [{"text": "..."}]}],
        "inferenceConfig": {"maxTokens": N, "temperature": 0.3} }

    Response:
      { "output": {"message": {"content": [{"text": "..."}]}}, "usage": {...} }

    Auto-disables permanently after first DNS/connection failure to avoid
    wasting ~90s per call on unreachable endpoints.
    """
    global _session_tokens_in, _session_tokens_out, _session_calls, _session_cost_usd
    global _DISABLED_PERMANENTLY

    if not _AVAILABLE or _DISABLED_PERMANENTLY:
        return None

    converse_url = (
        f'https://bedrock-runtime.{_REGION}.amazonaws.com'
        f'/model/{MODEL_ID}/converse'
    )

    body = json.dumps({
        'messages': [
            {'role': 'user', 'content': [{'text': prompt}]}
        ],
        'inferenceConfig': {
            'maxTokens':   max_tokens,
            'temperature': 0.3,
        },
    })

    headers = {
        'Authorization': f'Bearer {_BEARER_TOKEN}',
        'Content-Type':  'application/json',
        'Accept':        'application/json',
    }

    for attempt in range(retries + 1):
        try:
            resp = requests.post(converse_url, headers=headers, data=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            # Track token usage and cost
            usage       = data.get('usage', {})
            tokens_in   = usage.get('inputTokens',  0)
            tokens_out  = usage.get('outputTokens', 0)
            call_cost   = (tokens_in / 1000) * _COST_PER_1K_IN + (tokens_out / 1000) * _COST_PER_1K_OUT
            _session_tokens_in  += tokens_in
            _session_tokens_out += tokens_out
            _session_calls      += 1
            _session_cost_usd   += call_cost
            print(f'[llm_coach] call #{_session_calls}: in={tokens_in} out={tokens_out} '
                  f'cost=${call_cost:.5f}  session_total=${_session_cost_usd:.4f}')

            # Converse API response path
            return data['output']['message']['content'][0]['text']
        except requests.HTTPError as e:
            status = e.response.status_code if e.response else 0
            print(f'[llm_coach] Bedrock HTTP {status}: {e}')
            if status in (401, 403):
                print('[llm_coach] Auth failed — check AWS_BEARER_TOKEN_BEDROCK. Running dry.')
                return None
            if attempt < retries:
                time.sleep(1.0)
        except Exception as e:
            if attempt == retries:
                print(f'[llm_coach] Bedrock Converse call failed: {e}')
                # Auto-disable on DNS/connection failures — no point retrying
                # every macro cycle if the endpoint is unreachable.
                err_str = str(e).lower()
                if any(k in err_str for k in ['resolve', 'nodename', 'connectionrefused',
                                                'timeout', 'unreachable']):
                    _DISABLED_PERMANENTLY = True
                    print('[llm_coach] Bedrock unreachable — disabling coach for this session')
                return None
            time.sleep(1.0)
    return None



# ── Main coach class ───────────────────────────────────────────────────────────

class FighterCoach:
    """
    Bedrock Claude 3.5 Haiku meta-optimizer. One coach per agent.

    Usage:
        coach = FighterCoach('lstm', 'aggressive rushdown brawler')
        extractor.update_config(coach.config)

        # After every episode:
        coach.micro_coach(round_stats)
        extractor.update_config(coach.config)

        # After every MACRO_REVIEW_EVERY episodes:
        coach.review_and_adjust(macro_stats)
        extractor.update_config(coach.config)
    """

    def __init__(
        self,
        agent_type:  str,
        description: str = 'balanced fighter',
        *,
        log_dir:  Path | None = None,
        dry_run:  bool = False,
    ) -> None:
        self.agent_type    = agent_type
        self.description   = description
        self.dry_run       = dry_run or not _AVAILABLE
        self.cycle         = 0
        self.episode_count = 0
        self._history: list[CoachingMemoryEntry] = []
        self._last_win_rate      = 0.0
        self._micro_quiet_until  = 0

        self.log_dir   = log_dir or Path('/tmp/llm_coach')
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self.log_dir / f'coach_{agent_type}.jsonl'
        self._prev_config: RewardConfig | None = None   # rollback snapshot

        if not self.dry_run:
            self.config = self._generate_initial_config()
        else:
            self.config = self._default_config()

        model_tag = MODEL_ID.split('.')[1].split('-20')[0] if not self.dry_run else 'dry_run'
        print(f'[coach-{agent_type}] "{self.config.name}" ({self.config.style}) '
              f'— model={model_tag}')

    # ── Public API ─────────────────────────────────────────────────────────────

    def micro_coach(self, stats: RoundStats) -> RewardConfig:
        self.episode_count += 1
        if COACHING_DEADZONE_LO <= stats.win_rate <= COACHING_DEADZONE_HI:
            return self.config
        if self.episode_count < self._micro_quiet_until:
            return self.config
        if self.dry_run:
            return self.config

        raw = _call_bedrock(self._micro_prompt(stats), MICRO_MAX_TOKENS)
        if raw:
            deltas = self._extract_json(raw)
            if deltas:
                self._apply_deltas(deltas, max_delta=0.2)
                note = self._extract_note(raw)
                self._log({'event': 'micro', 'ep': self.episode_count,
                           'won': stats.won, 'deltas': deltas, 'note': note})
        return self.config

    def review_and_adjust(self, stats: MacroStats) -> RewardConfig:
        self.cycle += 1
        win_rate_before    = self._last_win_rate
        self._last_win_rate = stats.win_rate

        # Rollback: if win rate dropped >10% after last cycle's changes, revert
        if (self._prev_config is not None
                and self.cycle > 2
                and win_rate_before > 0
                and stats.win_rate < win_rate_before - 0.10):
            print(f'[coach-{self.agent_type}] Cycle {self.cycle}: '
                  f'wr dropped {win_rate_before:.1%}→{stats.win_rate:.1%} — '
                  f'ROLLING BACK to previous config')
            self.config = copy.deepcopy(self._prev_config)
            self._prev_config = None
            self._log({'event': 'rollback', 'cycle': self.cycle,
                       'wr_before': win_rate_before, 'wr_after': stats.win_rate})
            return self.config

        if COACHING_DEADZONE_LO <= stats.win_rate <= COACHING_DEADZONE_HI:
            print(f'[coach-{self.agent_type}] Cycle {self.cycle}: '
                  f'wr={stats.win_rate:.1%} in dead zone — no changes')
            return self.config
        if self.dry_run:
            return self.config

        # Snapshot current config before LLM modifies it (for rollback)
        self._prev_config = copy.deepcopy(self.config)

        prompt   = self._macro_prompt(stats)
        raw      = _call_bedrock(prompt, MACRO_MAX_TOKENS)
        note     = self._extract_note(raw) if raw else ''
        new_vals: dict[str, float] = {}

        if raw:
            new_vals = self._extract_json(raw)
            if new_vals:
                self._apply_overrides(new_vals)
                self.config.clamp()

        self._history.append(CoachingMemoryEntry(
            cycle=self.cycle, win_rate_before=win_rate_before,
            win_rate_after=stats.win_rate, changes=new_vals, coach_note=note,
        ))
        if len(self._history) > MAX_HISTORY:
            self._history.pop(0)

        self._micro_quiet_until = self.episode_count + 10

        print(f'[coach-{self.agent_type}] Cycle {self.cycle}: "{note}"')
        print(f'[coach-{self.agent_type}]   wr={stats.win_rate:.1%}  changes={new_vals}')
        self._log({
            'event': 'macro', 'cycle': self.cycle,
            'win_rate': stats.win_rate,
            'changes': new_vals, 'note': note,
            'prompt_input': prompt,
            'llm_output': raw or '',
            'stats': {
                'episodes': stats.episodes, 'avg_dealt': stats.avg_damage_dealt,
                'avg_taken': stats.avg_damage_taken, 'avg_steps': stats.avg_ep_steps,
                'hit_rate': stats.hit_rate_pct, 'attack_pct': stats.attack_pct,
                'move_pct': stats.move_pct, 'idle_pct': stats.idle_pct,
                'avg_spam': stats.avg_spam_penalty, 'avg_reward': stats.avg_reward,
                'ko_wins': stats.wins_by_ko, 'close_losses': stats.close_losses,
            },
        })
        return self.config

    # ── Prompts ────────────────────────────────────────────────────────────────

    def _micro_prompt(self, s: RoundStats) -> str:
        cfg = self.config
        return (
            f'You are coaching a Mortal Kombat 4 AI fighter.\n'
            f'Fighter: {cfg.name} | Style: {cfg.style}\n'
            f'Philosophy: {cfg.philosophy}\n\n'
            f'Last round: {"WIN" if s.won else "LOSS"} | '
            f'dealt={s.damage_dealt:.0f}hp  taken={s.damage_taken:.0f}hp | '
            f'diversity={s.action_diversity_pct:.0f}%  win_rate={s.win_rate:.1%}\n'
            f'Current weights: damage_dealt={cfg.damage_dealt_scale:.2f}, '
            f'damage_taken={cfg.damage_taken_scale:.2f}, '
            f'aggression={cfg.aggression:.2f}, '
            f'spam_scale_mult={cfg.spam_scale_mult:.2f}, '
            f'approach_scale={cfg.approach_scale:.2f}\n\n'
            f'Nudge at most 2 weights by ±0.2. '
            f'Output ONLY compact JSON like {{"aggression": 0.3}}.\n'
            f'Then on a new line: NOTE: <1 sentence of coaching advice>'
        )

    def _macro_prompt(self, s: MacroStats) -> str:
        cfg = self.config
        history_text = ''
        for h in self._history[-MAX_HISTORY:]:
            diff = h.win_rate_after - h.win_rate_before
            history_text += (
                f'  Cycle {h.cycle}: {h.changes} → '
                f'wr {h.win_rate_before:.1%}→{h.win_rate_after:.1%} '
                f'({"+" if diff >= 0 else ""}{diff:.1%})\n'
            )
        current = json.dumps(
            {k: v for k, v in asdict(cfg).items() if isinstance(v, float)},
            indent=2,
        )
        # Diagnose behavioral problems for the LLM
        problems = []
        if s.idle_pct > 40:
            problems.append(f'PASSIVE: {s.idle_pct:.0f}% idle — agent is not fighting')
        if s.hit_rate_pct < 10 and s.attack_pct > 35:
            problems.append(f'BUTTON MASHING: {s.attack_pct:.0f}% attacks but only {s.hit_rate_pct:.0f}% hit rate')
        if s.attack_pct < 25 and s.move_pct > 60:
            problems.append(f'RUNNING AWAY: {s.move_pct:.0f}% movement, only {s.attack_pct:.0f}% attacks')
        if abs(s.avg_spam_penalty) > s.avg_damage_dealt * 0.5:
            problems.append(f'SPAM PENALTY TOO HIGH: |spam|={abs(s.avg_spam_penalty):.0f} > dealt={s.avg_damage_dealt:.0f}×0.5')
        problem_text = '\n'.join(f'  ⚠ {p}' for p in problems) if problems else '  (none detected)'

        return (
            f'You are coaching a Mortal Kombat 4 RL agent. Adjust reward shaping weights.\n\n'
            f'FIGHTER (never change identity):\n'
            f'Name: {cfg.name} | Style: {cfg.style}\n'
            f'Philosophy: {cfg.philosophy}\n\n'
            f'═══ TRAINING SUMMARY ({s.episodes} episodes) ═══\n'
            f'Win rate: {s.win_rate:.1%} | KO wins: {s.wins_by_ko}/{s.episodes} | '
            f'Close losses: {s.close_losses}\n'
            f'Avg HP dealt: {s.avg_damage_dealt:.1f}/160 | '
            f'Avg HP taken: {s.avg_damage_taken:.1f}/160\n'
            f'Avg fight length: {s.avg_ep_steps:.0f} steps (~{s.avg_ep_steps*0.033:.0f}s)\n\n'
            f'═══ AGENT BEHAVIOR ═══\n'
            f'Attack actions: {s.attack_pct:.0f}% of steps | Hit rate: {s.hit_rate_pct:.0f}%\n'
            f'Movement: {s.move_pct:.0f}% | Idle/neutral: {s.idle_pct:.0f}%\n'
            f'Avg spam penalty: {s.avg_spam_penalty:.1f} | Avg total reward: {s.avg_reward:.1f}\n\n'
            f'═══ DIAGNOSED PROBLEMS ═══\n{problem_text}\n\n'
            f'WEIGHT REFERENCE:\n'
            f'- damage_dealt_scale: multiplier on HP dealt (primary combat learning signal)\n'
            f'- damage_taken_scale: multiplier on HP lost (negative reward)\n'
            f'- approach_scale: reward for closing distance when far away\n'
            f'- aggression: bonus when attack CONNECTS in range (hit confirmation)\n'
            f'- positioning_bonus: per-step reward for being at fighting range (footsies)\n'
            f'- spam_scale_mult: amplifies repeat-attack penalty\n'
            f'- cooldown_mult: amplifies too-fast-attack penalty\n'
            f'- anti_air_bonus: reward for hitting airborne opponent\n'
            f'- punish_bonus: reward for counter-attacking during opponent recovery\n'
            f'- idle_penalty: per-step penalty for doing nothing (set negative, e.g. -0.1)\n\n'
            f'RULES (hard constraints):\n'
            f'- damage_dealt_scale MUST stay >= 0.5\n'
            f'- spam_scale_mult MUST stay <= 2.0\n'
            f'- If agent is PASSIVE (idle >30%): increase aggression, add idle_penalty\n'
            f'- If agent BUTTON MASHES (low hit rate): increase aggression (rewards connecting)\n'
            f'- If agent RUNS AWAY: increase approach_scale + positioning_bonus\n'
            f'- Do NOT increase spam penalties if agent is already passive\n\n'
            f'COACHING HISTORY:\n{history_text or "  (none yet)"}\n\n'
            f'CURRENT CONFIG:\n{current}\n\n'
            f'Based on the diagnosed problems, adjust 2-4 float parameters.\n'
            f'Output ONLY compact JSON with new absolute values, e.g. '
            f'{{"aggression": 0.8, "idle_penalty": -0.05}}.\n'
            f'Then: NOTE: <1-2 sentences explaining your adjustment reasoning>'
        )

    # ── JSON extraction (4-strategy cascade) ──────────────────────────────────

    def _extract_json(self, text: str) -> dict[str, float]:
        text = text.split('NOTE:')[0].strip()
        import re

        for parser in [
            lambda t: json.loads(t),
            lambda t: json.loads(re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.DOTALL).group(1)),
            lambda t: json.loads(re.search(r'\{[^{}]*\}', t, re.DOTALL).group(0)),
        ]:
            try:
                obj = parser(text)
                if isinstance(obj, dict):
                    return {k: float(v) for k, v in obj.items() if isinstance(v, (int, float))}
            except Exception:
                pass

        # Depth-counting fallback
        depth, start = 0, text.find('{')
        if start >= 0:
            for i, c in enumerate(text[start:], start=start):
                depth += (c == '{') - (c == '}')
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i+1])
                        return {k: float(v) for k, v in obj.items() if isinstance(v, (int, float))}
                    except Exception:
                        break

        print(f'[coach-{self.agent_type}] Could not parse JSON from Claude response')
        return {}

    def _extract_note(self, text: str) -> str:
        for line in (text or '').splitlines():
            if line.strip().startswith('NOTE:'):
                return line.strip()[5:].strip()
        return ''

    # ── Config mutation ────────────────────────────────────────────────────────

    def _apply_deltas(self, deltas: dict[str, float], max_delta: float = 0.2) -> None:
        for key, delta in deltas.items():
            if hasattr(self.config, key) and isinstance(getattr(self.config, key), float):
                clamped = max(-max_delta, min(max_delta, delta))
                setattr(self.config, key, getattr(self.config, key) + clamped)
        self.config.clamp()

    def _apply_overrides(self, overrides: dict[str, float]) -> None:
        for key, val in overrides.items():
            if hasattr(self.config, key) and isinstance(getattr(self.config, key), float):
                setattr(self.config, key, float(val))

    # ── Philosopher (initial config generation) ────────────────────────────────

    def _generate_initial_config(self) -> RewardConfig:
        prompt = (
            f'Create a Mortal Kombat 4 AI fighter personality.\n'
            f'Description: "{self.description}"\n\n'
            f'Output JSON with these float fields (stay in ranges):\n'
            f'  damage_dealt_scale (0.5–3.0), damage_taken_scale (0.5–3.0),\n'
            f'  approach_scale (0.0–1.0), dist_penalty_scale (0.0–0.3),\n'
            f'  aggression (0.0–2.0), spam_scale_mult (0.5–2.0),\n'
            f'  win_bonus (20–100), loss_penalty (10–50),\n'
            f'  survival_per_step (0.0–0.01), idle_penalty (-0.5–0.0)\n\n'
            f'Then:\nNAME: <fighter name, 1-2 words>\n'
            f'STYLE: <style tag, 1-3 words>\n'
            f'NOTE: <locked philosophy, 2-3 sentences that will never change>'
        )
        raw = _call_bedrock(prompt, 450)
        cfg = self._default_config()

        if raw:
            vals = self._extract_json(raw)
            if vals:
                self._apply_overrides_to(cfg, vals)
                cfg.clamp()
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped.startswith('NAME:'):
                    cfg.name = stripped[5:].strip()[:30]
                elif stripped.startswith('STYLE:'):
                    cfg.style = stripped[6:].strip()[:30]
                elif stripped.startswith('NOTE:'):
                    cfg.philosophy = stripped[5:].strip()[:300]

        cfg.description = self.description
        cfg.name  = cfg.name  or self.agent_type
        cfg.style = cfg.style or self.description.split()[0]
        return cfg

    def _default_config(self) -> RewardConfig:
        cfg             = RewardConfig()
        cfg.name        = self.agent_type
        cfg.style       = 'balanced'
        cfg.description = self.description
        return cfg

    def _apply_overrides_to(self, cfg: RewardConfig, overrides: dict[str, float]) -> None:
        for key, val in overrides.items():
            if hasattr(cfg, key) and isinstance(getattr(cfg, key), float):
                setattr(cfg, key, float(val))

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log(self, entry: dict) -> None:
        try:
            with open(self._log_path, 'a') as f:
                f.write(json.dumps({'ts': time.time(), **entry}) + '\n')
        except Exception:
            pass


# ── Fighter personality presets ────────────────────────────────────────────────

FIGHTER_DESCRIPTIONS: dict[str, str] = {
    'lstm':        'patient counterpuncher who waits for openings and punishes mistakes',
    'transformer': 'adaptive reader who identifies patterns and changes gameplan mid-fight',
    'disc_rssm':   'aggressive rushdown brawler who overwhelms with relentless pressure',
    'obj_belief':  'defensive space-controller who zones with footsies and punishes overcommits',
}


def build_coaches(
    agent_types: list[str] | None = None,
    log_dir:     Path | None = None,
    dry_run:     bool = False,
) -> dict[str, FighterCoach]:
    """Build one FighterCoach per agent type. Returns {agent_type: coach}."""
    types = agent_types or list(FIGHTER_DESCRIPTIONS.keys())
    return {
        t: FighterCoach(
            agent_type=t,
            description=FIGHTER_DESCRIPTIONS.get(t, 'balanced fighter'),
            log_dir=log_dir,
            dry_run=dry_run,
        )
        for t in types
    }
