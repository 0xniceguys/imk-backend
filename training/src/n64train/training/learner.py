"""
learner.py — Central gradient updater for parallel MK4 training.

Supports all 8 architectures via agent_type parameter.
Receives rollouts from N workers, runs REINFORCE updates, broadcasts weights.
"""
from __future__ import annotations

import json
import queue
import sys
import time
from multiprocessing import Queue
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

N64_ROOT = Path(__file__).resolve().parents[4]   # →n64train→src→training→n64
sys.path.insert(0, str(N64_ROOT / 'training/src'))
sys.path.insert(0, str(N64_ROOT / 'training/scripts'))

from n64train.experiments.mk4_agent import (
    N_ACTIONS, OBS_DIM,
    LR_POLICY, LR_VALUE, GAMMA, ENTROPY_COEF, GRAD_CLIP, CKPT_DIR,
)
from n64train.training.llm_coach import FighterCoach, RoundStats, MacroStats, MACRO_REVIEW_EVERY, FIGHTER_DESCRIPTIONS

LOG_DIR        = N64_ROOT / 'training/data/logs'
STATS_PATH     = CKPT_DIR / 'mk4_training_stats.jsonl'  # fallback for single-run
HEARTBEAT_DIR  = N64_ROOT / 'training/data/logs'


class ParallelLearner:
    """
    Central learner — supports all 8 architectures.
    The net, optimizer, and checkpoint path are all derived from agent_type.
    """

    def __init__(
        self,
        rollout_queue: Queue,
        weight_queues: list[Queue],
        n_workers: int,
        total_episodes: int,
        save_every: int = 10,
        batch_size: int = None,
        agent_type: str = 'mlp',
        run_id: str | None = None,          # Bug 4+5: isolate per-job files
    ) -> None:
        self.rollout_queue  = rollout_queue
        self.weight_queues  = weight_queues
        self.n_workers      = n_workers
        self.total_episodes = total_episodes
        self.save_every     = save_every
        self.batch_size     = batch_size or n_workers
        self.agent_type     = agent_type
        self.run_id         = run_id or agent_type

        # Bug 4: per-run-id heartbeat so watchdog can distinguish agents
        self.heartbeat_path = HEARTBEAT_DIR / f'learner_heartbeat_{self.run_id}'
        # Bug 5: per-run-id log + stats so concurrent jobs don't mix metrics
        self.log_path   = LOG_DIR / f'mk4_training_log_{self.run_id}.jsonl'
        self.stats_path = CKPT_DIR / f'mk4_training_stats_{self.run_id}.jsonl'

        CKPT_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)

        # Build the shared agent (learner owns the canonical weights)
        from mk4_train import build_agent
        self.agent = build_agent(agent_type)
        # Bug 5: point agent's stats writes at the per-run-id stats file
        self.agent._stats_path = self.stats_path

        # Learner always gets the underlying net for weight broadcast
        self.net = self.agent.net

        self.update_count  = 0
        self.episode_count = 0
        self.wins          = 0
        self.ep_rewards: list[float] = []
        self._acc_dealt:  float = 0.0   # running totals for macro coach stats
        self._acc_taken:  float = 0.0
        self._acc_steps:  int   = 0
        self._macro_win_count: int = 0
        self._macro_ep_count:  int = 0

        # ── LLM Coach ──────────────────────────────────────────────────
        # dry_run=True if no Ollama available — FighterCoach handles this gracefully
        self.coach = FighterCoach(
            agent_type=agent_type,
            description=FIGHTER_DESCRIPTIONS.get(agent_type, 'balanced fighter'),
            log_dir=LOG_DIR,
        )
        # Expose the live reward config so workers can consume it
        self.reward_config = self.coach.config

        print(f'[learner] agent={agent_type}  batch={self.batch_size}')

    def run(self) -> None:
        """Main learner loop."""
        print(f'[learner] Ready. Waiting for rollouts...')
        self._broadcast_weights()

        pending: list[dict] = []
        workers_done = 0
        start_time = time.time()
        ROLLOUT_TIMEOUT = 300.0   # seconds: max-ep (~99s) + savestate + PPO update overhead

        while workers_done < self.n_workers:
            # Bug 4: write heartbeat to per-run-id file
            try:
                self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                self.heartbeat_path.write_text(str(time.time()))
            except Exception:
                pass
            try:
                rollout = self.rollout_queue.get(timeout=ROLLOUT_TIMEOUT)
            except queue.Empty:
                print(f'[learner] WARNING: no rollout received in {ROLLOUT_TIMEOUT:.0f}s — '
                      f'workers may be stuck (done={workers_done}/{self.n_workers})')
                continue

            if rollout.get('done'):
                workers_done += 1
                print(f'[learner] Worker {rollout["worker_id"]} done ({workers_done}/{self.n_workers})')
                continue

            if rollout.get('error'):
                print(f'[learner] Worker {rollout["worker_id"]} skipped: {rollout["error"]}')
                continue

            # Fix 1: empty/short rollouts must not count as real episodes.
            # Guard both here and in _update() (n<2 skip). Prevents episode_count
            # and update_count from advancing without any gradient step.
            if len(rollout.get('obs', [])) < 2:
                print(f'[learner] Worker {rollout["worker_id"]} empty rollout — skipping')
                continue

            pending.append(rollout)
            self.episode_count += 1
            ep_r = sum(rollout['rewards']) if rollout['rewards'] else 0.0
            self.ep_rewards.append(ep_r)
            won = rollout.get('won', False)
            if won:
                self.wins += 1

            acc      = rollout.get('acc', {})
            ep_steps = rollout.get('ep_steps', 0)
            wr_frac  = self.wins / self.episode_count

            # Fix 4: acc['dealt_hp'] and acc['taken_hp'] store raw HP deltas
            # (positive values). The old acc['dealt'/'taken'] stored shaped reward
            # terms which are sign-flipped and scaled — useless as combat stats.
            raw_dealt = acc.get('dealt_hp', acc.get('dealt', 0.0))   # HP P2 lost
            raw_taken = acc.get('taken_hp', 0.0)                      # HP P1 lost

            # Accumulate for macro coach
            self._acc_dealt       += raw_dealt
            self._acc_taken       += raw_taken
            self._acc_steps       += ep_steps
            self._macro_ep_count  += 1
            if won:
                self._macro_win_count += 1

            # Reward config is frozen for this entire macro cycle (MACRO_REVIEW_EVERY
            # episodes) to keep PPO's MDP stationary. Only macro review updates it.
            # (micro_coach per-episode was removed — too frequent, caused non-stationarity)

            avg50  = sum(self.ep_rewards[-50:]) / len(self.ep_rewards[-50:])
            wr     = self.wins / self.episode_count * 100
            print(
                f'  ep {self.episode_count:4d}  w{rollout["worker_id"]}'
                f'  steps={rollout.get("ep_steps",0):3d}'
                f'  r={ep_r:+7.2f}'
                f'  [dealt={acc.get("dealt",0):+.1f}'
                f' taken={acc.get("taken",0):+.1f}'
                f' spam={acc.get("spam",0):+.1f}]'
                f'  won={"✓" if rollout.get("won") else "✗"}'
                f'  win%={wr:5.1f}'
                f'  avg50={avg50:+6.2f}'
            )

            # Log to JSONL for dashboard — Bug 5: per-run-id log file
            with open(self.log_path, 'a') as f:
                f.write(json.dumps({
                    'episode': self.episode_count, 'steps': rollout.get('ep_steps', 0),
                    'reward': round(ep_r, 4), 'won': rollout.get('won', False),
                    'win_rate': round(wr, 2), 'avg50': round(avg50, 4),
                    'r_dealt':   round(acc.get('dealt', 0), 3),
                    'r_taken':   round(acc.get('taken', 0), 3),
                    'r_spam':    round(acc.get('spam',  0), 3),
                    'r_approach':round(acc.get('approach',0),3),
                    'r_survival':round(acc.get('survival',0),3),
                    'agent': self.agent_type,
                }) + '\n')

            if len(pending) >= self.batch_size:
                metrics = self._update(pending)
                pending = []

                # ── LLM Macro coach: every MACRO_REVIEW_EVERY episodes ─────────────
                # Review BEFORE broadcast so the new config ships on the same
                # _broadcast_weights() call that workers consume at next episode start.
                if self._macro_ep_count >= MACRO_REVIEW_EVERY:
                    macro_stats = MacroStats(
                        episodes=self._macro_ep_count,
                        win_rate=self._macro_win_count / max(1, self._macro_ep_count),
                        avg_damage_dealt=self._acc_dealt / max(1, self._macro_ep_count),
                        avg_damage_taken=self._acc_taken / max(1, self._macro_ep_count),
                        avg_ep_steps=self._acc_steps / max(1, self._macro_ep_count),
                        current_config=None,
                    )
                    updated = self.coach.review_and_adjust(macro_stats)
                    self.reward_config = updated
                    # Reset accumulators
                    self._acc_dealt = self._acc_taken = 0.0
                    self._acc_steps = self._macro_win_count = self._macro_ep_count = 0

                self._broadcast_weights()

                if self.update_count % self.save_every == 0:
                    self._save()
                    # Fix 2: refresh heartbeat after save so watchdog doesn't
                    # false-positive during a long checkpoint write.
                    try: self.heartbeat_path.write_text(str(time.time()))
                    except Exception: pass
                with open(self.stats_path, 'a') as f:
                    f.write(json.dumps(metrics) + '\n')
                # Fix 2: refresh heartbeat after update so watchdog doesn't
                # false-positive during long PPO update on CPU.
                try: self.heartbeat_path.write_text(str(time.time()))
                except Exception: pass

        if pending:
            self._update(pending)
        self._save()
        elapsed = time.time() - start_time
        print(f'\n[learner] Done. {self.episode_count} eps in {elapsed:.1f}s')
        print(f'[learner] Win rate: {self.wins/max(1,self.episode_count)*100:.1f}%')

    def _update(self, rollouts: list[dict]) -> dict:
        """REINFORCE gradient update. Delegates to agent.learn() for recurrent archs,
           or does a batched update for MLP/GRU (stateless archs)."""

        # For stateless agents (MLP): batch all rollouts, single backward
        # For stateful agents (LSTM/GRU/RSSM/Transformer): update episode-by-episode
        has_recurrence = hasattr(self.agent, 'reset_episode') and self.agent_type not in ('mlp',)

        if not has_recurrence:
            return self._batched_update(rollouts)
        else:
            # Replay each rollout through the recurrent agent
            total_loss_val = 0.0
            learned_any    = False   # Fix 2: only count updates where learning actually happened
            for r in rollouts:
                obs = r.get('obs', []); acts = r.get('acts', [])
                rewards = r.get('rewards', []); cpu_atk = r.get('cpu_attacked', [])
                n = min(len(obs), len(acts), len(rewards))
                if n < 2: continue
                self.agent.reset_episode()
                self.agent._obs_buf  = obs[:n]
                self.agent._act_buf  = acts[:n]
                self.agent._rewards  = rewards[:n]
                if hasattr(self.agent, '_cpu_attacked'):
                    self.agent._cpu_attacked = cpu_atk[:n]
                # Inject PPO buffers — required for clipped loss ratio computation
                old_lps = r.get('old_lps', [])
                vals    = r.get('vals', [])
                if hasattr(self.agent, '_old_lp_buf'):
                    self.agent._old_lp_buf = old_lps[:n]
                if hasattr(self.agent, '_val_buf'):
                    self.agent._val_buf    = vals[:n]
                # PPO truncation bootstrap: if episode ended by timeout (not genuine
                # terminal), use the value-network's estimate of the last state instead
                # of 0.0. Getting this wrong biases returns downward for long fights.
                if hasattr(self.agent, '_bootstrap_val'):
                    self.agent._bootstrap_val = float(r.get('bootstrap_val', 0.0))
                m = self.agent.learn()
                if m:
                    total_loss_val += m.get('policy_loss', 0.0)
                    learned_any = True
            # Fix 2: only advance update_count if gradient step(s) actually ran
            if learned_any:
                self.update_count += 1
            return {'update': self.update_count, 'agent': self.agent_type,
                    'episode': self.episode_count, 'batch_eps': len(rollouts)}

    def _batched_update(self, rollouts: list[dict]) -> dict:
        """Single batched backward for memoryless archs (MLP)."""
        self.net.train()
        all_obs, all_acts, all_rets = [], [], []
        for r in rollouts:
            obs = r.get('obs', []); acts = r.get('acts', [])
            rewards = r.get('rewards', [])
            n = min(len(obs), len(acts), len(rewards))
            if n < 2: continue
            G = 0.0; ep_rets = []
            for rew in reversed(rewards[:n]):
                G = rew + GAMMA * G; ep_rets.insert(0, G)
            all_obs.extend(obs[:n]); all_acts.extend(acts[:n]); all_rets.extend(ep_rets)
        if not all_obs:
            return {}

        obs_t = torch.tensor(all_obs,  dtype=torch.float32)
        act_t = torch.tensor(all_acts, dtype=torch.long)
        ret_t = torch.tensor(all_rets, dtype=torch.float32)
        if ret_t.std() > 1e-6:
            ret_t = (ret_t - ret_t.mean()) / (ret_t.std() + 1e-8)

        # MLP has a specific forward signature
        if hasattr(self.net, 'trunk'):
            logits, values = self.net(obs_t)
        else:
            logits, values, _ = self.net(obs_t)

        dist = Categorical(logits=logits)
        lp   = dist.log_prob(act_t); ent = dist.entropy()
        adv  = ret_t - values.detach()
        loss = -(lp*adv).mean() + 0.5*F.mse_loss(values, ret_t) - ENTROPY_COEF*ent.mean()

        for opt in [o for o in [getattr(self.agent,'opt_policy',None),
                                 getattr(self.agent,'opt_val',None)] if o]:
            opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), GRAD_CLIP)
        for opt in [o for o in [getattr(self.agent,'opt_policy',None),
                                 getattr(self.agent,'opt_val',None)] if o]:
            opt.step()

        self.update_count += 1
        return {'update': self.update_count, 'agent': self.agent_type,
                'policy_loss': round((-(lp*adv).mean()).item(), 4),
                'episode': self.episode_count, 'batch_steps': len(all_obs)}

    def _broadcast_weights(self) -> None:
        from dataclasses import asdict
        state = {k: v.cpu().clone() for k, v in self.net.state_dict().items()}
        # Bundle reward_config alongside weights so workers can hot-swap their
        # reward extractor every episode (None if coach not yet initialised).
        try:
            reward_cfg_dict = asdict(self.reward_config) if self.reward_config is not None else None
        except Exception:
            reward_cfg_dict = None
        bundle = {'weights': state, 'reward_config': reward_cfg_dict}
        for q in self.weight_queues:
            # Fix 5: pure get_nowait drain — no racy q.empty() guard
            while True:
                try: q.get_nowait()
                except: break
            q.put(bundle)

    def _save(self) -> None:
        # Build a run-id-scoped checkpoint path so concurrent same-arch runs
        # don't clobber each other.  E.g.:  mk4_policy.pt → mk4_policy_run0.pt
        base = self.agent.CKPT          # e.g. .../checkpoints/mk4_policy.pt
        scoped = base.parent / f'{base.stem}_{self.run_id}{base.suffix}'
        self.agent.save(scoped)
        print(f'  [ckpt] saved → {scoped.name}  update={self.update_count} ep={self.episode_count}')


def run_learner(
    rollout_queue: Queue,
    weight_queues: list[Queue],
    n_workers: int,
    total_episodes: int,
    save_every: int,
    batch_size: int,
    agent_type: str = 'mlp',
    run_id: str | None = None,   # Bug 4+5: isolate per-job files
) -> None:
    """Entry point for learner process."""
    learner = ParallelLearner(
        rollout_queue=rollout_queue,
        weight_queues=weight_queues,
        n_workers=n_workers,
        total_episodes=total_episodes,
        save_every=save_every,
        batch_size=batch_size,
        agent_type=agent_type,
        run_id=run_id,
    )
    learner.run()
