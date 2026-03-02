"""
mk4_architectures.py — Architectures 3–8 for MK4 Training
All operate on 28-float stacked RAM observations.
All trained with REINFORCE + architecture-specific auxiliary losses.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Optional
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical, Normal
from n64train.runtime.actions import MacroAction

N64_ROOT   = Path(__file__).resolve().parents[4]   # …/n64train/experiments → src → n64train → training → n64
CKPT_DIR   = N64_ROOT / 'training/data/checkpoints'
STATS_PATH = CKPT_DIR / 'mk4_training_stats.jsonl'

OBS_DIM    = 28
N_ACTIONS  = len(MacroAction)
ACTIONS    = list(MacroAction)

LR_POLICY  = 3e-4
LR_VALUE   = 1e-3
LR_AUX     = 1e-3
GAMMA      = 0.99
ENT_COEF   = 0.02
GRAD_CLIP  = 1.0


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

def _discounted_returns(rewards, gamma=GAMMA):
    G, ret = 0.0, []
    for r in reversed(rewards):
        G = r + gamma * G
        ret.insert(0, G)
    t = torch.tensor(ret, dtype=torch.float32)
    if t.std() > 1e-6:
        t = (t - t.mean()) / (t.std() + 1e-8)
    return t


# Bug 5: per-run-id stats override — set by ParallelLearner after building agent
_STATS_PATH_OVERRIDE: Path | None = None


def _log_stats(metrics: dict, agent=None):
    """Write stats to per-agent stats file if available, else fall back to global."""
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    # Prefer agent-level path (set by learner), then module override, then global
    path = (
        getattr(agent, '_stats_path', None)
        or _STATS_PATH_OVERRIDE
        or STATS_PATH
    )
    with open(path, 'a') as f:
        f.write(json.dumps(metrics) + '\n')


def _try_load(agent, path: Path):
    if path.exists():
        try:
            agent.load(path)
        except Exception as e:
            print(f'[{agent.__class__.__name__}] checkpoint load failed ({e}) — fresh start')
    else:
        print(f'[{agent.__class__.__name__}] no checkpoint — fresh start')


# ─────────────────────────────────────────────────────────
# ARCH 3: GRU REACTIVE BASELINE
# ─────────────────────────────────────────────────────────

class _GruNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc  = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.ReLU())
        self.gru  = nn.GRUCell(64, 128)
        self.pol  = nn.Linear(128, N_ACTIONS)
        self.val  = nn.Linear(128, 1)
        nn.init.orthogonal_(self.pol.weight, 0.01)

    def forward(self, x, h):
        e = self.enc(x)
        h = self.gru(e, h)
        return self.pol(h), self.val(h).squeeze(-1), h

    def init_h(self, dev):
        return torch.zeros(1, 128, device=dev)


class Mk4GruAgent:
    CKPT = CKPT_DIR / 'mk4_gru.pt'
    ARCH = 'cnn_rnn_reactive_baseline'

    def __init__(self, device='cpu'):
        self.device = torch.device(device)
        self.net = _GruNet().to(self.device)
        self.opt_pol = torch.optim.Adam(
            list(self.net.enc.parameters()) + list(self.net.gru.parameters()) + list(self.net.pol.parameters()), LR_POLICY)
        self.opt_val = torch.optim.Adam(self.net.val.parameters(), LR_VALUE)
        self._h = None
        self._obs, self._act, self._rew = [], [], []
        self.episode = self.total_updates = 0
        _try_load(self, self.CKPT)

    def reset_episode(self):
        self._h = self.net.init_h(self.device)
        self._obs, self._act, self._rew = [], [], []

    def __call__(self, obs):
        if self._h is None:
            self._h = self.net.init_h(self.device)
        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        self.net.eval()
        with torch.no_grad():
            logits, _, h = self.net(x, self._h)
            self._h = h
            a = Categorical(logits=logits).sample()
        self._obs.append(obs); self._act.append(a.item())
        return ACTIONS[a.item()]

    def record(self, r, done=False):
        self._rew.append(r)
        if done: self.learn()

    def learn(self):
        n = min(len(self._obs), len(self._act), len(self._rew))
        if n < 2:
            self.reset_episode(); return None
        self.net.train(); self.episode += 1
        ret = _discounted_returns(self._rew[:n]).to(self.device)
        obs_t = torch.tensor(self._obs[:n], dtype=torch.float32, device=self.device)
        act_t = torch.tensor(self._act[:n], dtype=torch.long, device=self.device)
        h = self.net.init_h(self.device)
        logits_all, vals_all = [], []
        for i in range(n):
            lg, v, h = self.net(obs_t[i:i+1], h)
            logits_all.append(lg); vals_all.append(v)
        logits_t = torch.cat(logits_all, 0)
        vals_t   = torch.cat(vals_all, 0)
        dist = Categorical(logits=logits_t)
        lp = dist.log_prob(act_t); ent = dist.entropy()
        adv = ret - vals_t.detach()
        loss = -(lp * adv).mean() + 0.5 * F.mse_loss(vals_t, ret) - ENT_COEF * ent.mean()
        self.opt_pol.zero_grad(); self.opt_val.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), GRAD_CLIP)
        self.opt_pol.step(); self.opt_val.step()
        self.total_updates += 1
        m = {'episode': self.episode, 'arch': self.ARCH,
             'policy_loss': round((-(lp*adv).mean()).item(),4), 'n_steps': n}
        _log_stats(m, self); self.reset_episode(); return m

    def save(self, path=None):
        path = path or self.CKPT
        torch.save({'net': self.net.state_dict(), 'opt_pol': self.opt_pol.state_dict(),
                    'opt_val': self.opt_val.state_dict(), 'episode': self.episode,
                    'total_updates': self.total_updates}, path)

    def load(self, path=None):
        path = path or self.CKPT
        c = torch.load(path, map_location=self.device)
        self.net.load_state_dict(c['net'])
        self.episode = c.get('episode', 0); self.total_updates = c.get('total_updates', 0)
        print(f'[GRU] loaded ep={self.episode}')


# ─────────────────────────────────────────────────────────
# ARCH 4: CONTINUOUS RSSM + HIERARCHICAL AC
# ─────────────────────────────────────────────────────────

Z_DIM  = 32   # latent z size
DET    = 128  # deterministic GRU hidden
ACT_EMB = 16  # action embedding dim
GOAL   = 32   # manager goal dim
MGR_K  = 10   # manager fires every K steps


class _ContRssmNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc     = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.ReLU())
        self.act_emb = nn.Embedding(N_ACTIONS, ACT_EMB)
        self.det_gru = nn.GRUCell(64 + ACT_EMB, DET)
        self.prior   = nn.Sequential(nn.Linear(DET, 64), nn.ReLU(), nn.Linear(64, Z_DIM*2))
        self.post    = nn.Sequential(nn.Linear(DET + 64, 64), nn.ReLU(), nn.Linear(64, Z_DIM*2))
        lat = DET + Z_DIM
        self.manager_net = nn.Sequential(nn.Linear(lat, 64), nn.ReLU(), nn.Linear(64, GOAL))
        self.worker_pol  = nn.Linear(lat + GOAL, N_ACTIONS)
        self.worker_val  = nn.Linear(lat + GOAL, 1)
        nn.init.orthogonal_(self.worker_pol.weight, 0.01)

    def encode(self, obs, h_det, prev_action_idx):
        e   = self.enc(obs)
        ae  = self.act_emb(prev_action_idx)
        h   = self.det_gru(torch.cat([e, ae], -1), h_det)
        pri = self.prior(h)
        pos = self.post(torch.cat([h, e], -1))
        mu_pr, ls_pr = pri.chunk(2, -1)
        mu_po, ls_po = pos.chunk(2, -1)
        z = mu_po + torch.randn_like(mu_po) * (ls_po.exp() + 1e-5)
        kl = Normal(mu_po, ls_po.exp()+1e-5).log_prob(z) - Normal(mu_pr, ls_pr.exp()+1e-5).log_prob(z)
        return h, z, kl.mean()

    def init_h(self, dev):
        return torch.zeros(1, DET, device=dev)


class Mk4ContRssmAgent:
    CKPT = CKPT_DIR / 'mk4_cont_rssm.pt'
    ARCH = 'continuous_rssm_hier_ac'

    def __init__(self, device='cpu'):
        self.device = torch.device(device)
        self.net = _ContRssmNet().to(self.device)
        params_pol = list(self.net.enc.parameters()) + list(self.net.det_gru.parameters()) + \
                     list(self.net.prior.parameters()) + list(self.net.post.parameters()) + \
                     list(self.net.act_emb.parameters()) + list(self.net.manager_net.parameters()) + \
                     list(self.net.worker_pol.parameters())
        self.opt_pol = torch.optim.Adam(params_pol, LR_POLICY)
        self.opt_val = torch.optim.Adam(self.net.worker_val.parameters(), LR_VALUE)
        self._h = None; self._goal = None; self._prev_act = 0; self._step = 0
        self._obs, self._act, self._rew = [], [], []
        self.episode = self.total_updates = 0
        _try_load(self, self.CKPT)

    def reset_episode(self):
        self._h = self.net.init_h(self.device)
        self._goal = torch.zeros(1, GOAL, device=self.device)
        self._prev_act = 0; self._step = 0
        self._obs, self._act, self._rew = [], [], []

    def __call__(self, obs):
        if self._h is None: self.reset_episode()
        self.net.eval()
        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        pa = torch.tensor([self._prev_act], dtype=torch.long, device=self.device)
        with torch.no_grad():
            e = self.net.enc(x); ae = self.net.act_emb(pa)
            h = self.net.det_gru(torch.cat([e, ae], -1), self._h)
            pri = self.net.prior(h); mu, ls = pri.chunk(2, -1)
            z = mu + torch.randn_like(mu) * (ls.exp() + 1e-5)
            lat = torch.cat([h, z], -1)
            if self._step % MGR_K == 0:
                self._goal = self.net.manager_net(lat)
            logits = self.net.worker_pol(torch.cat([lat, self._goal], -1))
            a = Categorical(logits=logits).sample()
        self._h = h; self._prev_act = a.item(); self._step += 1
        self._obs.append(obs); self._act.append(a.item())
        return ACTIONS[a.item()]

    def record(self, r, done=False):
        self._rew.append(r)
        if done: self.learn()

    def learn(self):
        n = min(len(self._obs), len(self._act), len(self._rew))
        if n < 2: self.reset_episode(); return None
        self.net.train(); self.episode += 1
        ret = _discounted_returns(self._rew[:n]).to(self.device)
        obs_t = torch.tensor(self._obs[:n], dtype=torch.float32, device=self.device)
        act_t = torch.tensor(self._act[:n], dtype=torch.long, device=self.device)
        h = self.net.init_h(self.device)
        goal = torch.zeros(1, GOAL, device=self.device)
        logits_all, vals_all, kls = [], [], []
        for i in range(n):
            pa = torch.tensor([self._act[i-1] if i>0 else 0], dtype=torch.long, device=self.device)
            h, z, kl = self.net.encode(obs_t[i:i+1], h, pa)
            lat = torch.cat([h, z], -1)
            if i % MGR_K == 0: goal = self.net.manager_net(lat)
            logits_all.append(self.net.worker_pol(torch.cat([lat, goal], -1)))
            vals_all.append(self.net.worker_val(torch.cat([lat, goal], -1)).squeeze(-1))
            kls.append(kl)
        logits_t = torch.cat(logits_all, 0); vals_t = torch.cat(vals_all, 0)
        dist = Categorical(logits=logits_t); lp = dist.log_prob(act_t); ent = dist.entropy()
        adv = ret - vals_t.detach()
        kl_loss = torch.stack(kls).mean()
        loss = -(lp*adv).mean() + 0.5*F.mse_loss(vals_t, ret) - ENT_COEF*ent.mean() + 0.1*kl_loss
        self.opt_pol.zero_grad(); self.opt_val.zero_grad()
        loss.backward(); nn.utils.clip_grad_norm_(self.net.parameters(), GRAD_CLIP)
        self.opt_pol.step(); self.opt_val.step()
        self.total_updates += 1
        m = {'episode': self.episode, 'arch': self.ARCH, 'kl': round(kl_loss.item(),4), 'n_steps': n}
        _log_stats(m, self); self.reset_episode(); return m

    def save(self, path=None):
        path = path or self.CKPT
        torch.save({'net': self.net.state_dict(), 'episode': self.episode,
                    'total_updates': self.total_updates}, path)

    def load(self, path=None):
        path = path or self.CKPT
        c = torch.load(path, map_location=self.device)
        self.net.load_state_dict(c['net'])
        self.episode = c.get('episode', 0); self.total_updates = c.get('total_updates', 0)
        print(f'[ContRSSM] loaded ep={self.episode}')


# ─────────────────────────────────────────────────────────
# ARCH 5: DISCRETE RSSM + HIERARCHICAL AC
# ─────────────────────────────────────────────────────────

N_CATS  = 8    # number of categorical classes
CAT_SZ  = 8    # classes per category
DISC_Z  = N_CATS * CAT_SZ   # 64-d discrete latent


class _DiscRssmNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc     = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.ReLU())
        self.act_emb = nn.Embedding(N_ACTIONS, ACT_EMB)
        self.det_gru = nn.GRUCell(64 + ACT_EMB, DET)
        self.prior   = nn.Linear(DET, N_CATS * CAT_SZ)
        self.post    = nn.Linear(DET + 64, N_CATS * CAT_SZ)
        lat = DET + DISC_Z
        self.manager_net = nn.Sequential(nn.Linear(lat, 64), nn.ReLU(), nn.Linear(64, GOAL))
        self.worker_pol  = nn.Linear(lat + GOAL, N_ACTIONS)
        self.worker_val  = nn.Linear(lat + GOAL, 1)
        nn.init.orthogonal_(self.worker_pol.weight, 0.01)

    def encode(self, obs, h_det, prev_act):
        e = self.enc(obs); ae = self.act_emb(prev_act)
        h = self.det_gru(torch.cat([e, ae], -1), h_det)
        po_logits = self.post(torch.cat([h, e], -1)).view(-1, N_CATS, CAT_SZ)
        pr_logits = self.prior(h).view(-1, N_CATS, CAT_SZ)
        z_st = F.gumbel_softmax(po_logits, tau=1.0, hard=True).view(-1, DISC_Z)
        kl = (F.softmax(po_logits, -1) * (F.log_softmax(po_logits,-1) - F.log_softmax(pr_logits,-1))).sum(-1).mean()
        return h, z_st, kl

    def init_h(self, dev):
        return torch.zeros(1, DET, device=dev)


class Mk4DiscRssmAgent:
    CKPT = CKPT_DIR / 'mk4_disc_rssm.pt'
    ARCH = 'discrete_rssm_hier_ac'

    def __init__(self, device='cpu'):
        self.device = torch.device(device)
        self.net = _DiscRssmNet().to(self.device)
        params_pol = [p for n,p in self.net.named_parameters() if 'worker_val' not in n]
        self.opt_pol = torch.optim.Adam(params_pol, LR_POLICY)
        self.opt_val = torch.optim.Adam(self.net.worker_val.parameters(), LR_VALUE)
        self._h = None; self._goal = None; self._prev_act = 0; self._step = 0
        self._obs_buf, self._act_buf, self._rewards = [], [], []
        self.episode = self.total_updates = 0
        _try_load(self, self.CKPT)

    def reset_episode(self):
        self._h = self.net.init_h(self.device)
        self._goal = torch.zeros(1, GOAL, device=self.device)
        self._prev_act = 0; self._step = 0
        self._obs_buf, self._act_buf, self._rewards = [], [], []
        self._old_lp_buf: list[float] = []
        self._val_buf:    list[float] = []

    def __call__(self, obs):
        if self._h is None: self.reset_episode()
        self.net.eval()
        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        pa = torch.tensor([self._prev_act], dtype=torch.long, device=self.device)
        with torch.no_grad():
            h, z, _ = self.net.encode(x, self._h, pa)
            lat = torch.cat([h, z], -1)
            if self._step % MGR_K == 0:
                self._goal = self.net.manager_net(lat)
            inp = torch.cat([lat, self._goal], -1)
            logits = self.net.worker_pol(inp)
            val    = self.net.worker_val(inp).squeeze(-1)
            a      = Categorical(logits=logits).sample()
            old_lp = Categorical(logits=logits).log_prob(a)
        self._h = h; self._prev_act = a.item(); self._step += 1
        self._obs_buf.append(obs); self._act_buf.append(a.item())
        self._old_lp_buf.append(old_lp.item()); self._val_buf.append(val.item())
        return ACTIONS[a.item()]

    def record(self, r, done=False):
        self._rewards.append(r)
        if done: self.learn()

    def learn(self):
        from n64train.training.ppo_learner import (
            gae_advantages, ppo_loss, entropy_schedule, PPO_EPOCHS, GRAD_CLIP as PGRAD,
        )
        n = min(len(self._obs_buf), len(self._act_buf), len(self._rewards))
        if n < 2: self.reset_episode(); return None
        self.net.train(); self.episode += 1
        ent_coef = entropy_schedule(self.episode)
        obs_t    = torch.tensor(self._obs_buf[:n],    dtype=torch.float32, device=self.device)
        act_t    = torch.tensor(self._act_buf[:n],    dtype=torch.long,    device=self.device)
        old_lp_t = torch.tensor(self._old_lp_buf[:n], dtype=torch.float32, device=self.device)
        val_t    = torch.tensor(self._val_buf[:n],    dtype=torch.float32, device=self.device)
        adv_t, ret_t = gae_advantages(self._rewards[:n], val_t)
        adv_t = adv_t.to(self.device); ret_t = ret_t.to(self.device)
        metrics_last = {}; kl_last = torch.tensor(0.0)
        for _ in range(PPO_EPOCHS):
            h = self.net.init_h(self.device)
            goal = torch.zeros(1, GOAL, device=self.device)
            logits_all, vals_all, kls = [], [], []
            for i in range(n):
                pa = torch.tensor([self._act_buf[i-1] if i>0 else 0], dtype=torch.long, device=self.device)
                h, z, kl = self.net.encode(obs_t[i:i+1], h, pa)
                lat = torch.cat([h, z], -1)
                if i % MGR_K == 0: goal = self.net.manager_net(lat)
                inp = torch.cat([lat, goal], -1)
                logits_all.append(self.net.worker_pol(inp))
                vals_all.append(self.net.worker_val(inp).squeeze(-1))
                kls.append(kl)
            logits_t = torch.cat(logits_all, 0); vals_t = torch.cat(vals_all, 0)
            kl_last  = torch.stack(kls).mean()
            loss, metrics_last = ppo_loss(
                logits_t, vals_t, act_t, old_lp_t, adv_t, ret_t, ent_coef=ent_coef)
            loss = loss + 0.25 * kl_last
            self.opt_pol.zero_grad(); self.opt_val.zero_grad()
            loss.backward(); nn.utils.clip_grad_norm_(self.net.parameters(), PGRAD)
            self.opt_pol.step(); self.opt_val.step()
        self.total_updates += 1
        m = {'episode': self.episode, 'arch': self.ARCH, 'n_steps': n,
             'kl': round(kl_last.item(), 4), **metrics_last}
        _log_stats(m, self); self.reset_episode(); return m

    def save(self, path=None):
        path = path or self.CKPT
        torch.save({
            'net':           self.net.state_dict(),
            'opt_pol':       self.opt_pol.state_dict(),
            'opt_val':       self.opt_val.state_dict(),
            'episode':       self.episode,
            'total_updates': self.total_updates,
        }, path)

    def load(self, path=None):
        path = path or self.CKPT
        c = torch.load(path, map_location=self.device)
        self.net.load_state_dict(c['net'])
        self.episode = c.get('episode', 0); self.total_updates = c.get('total_updates', self.total_updates)
        if 'opt_pol' in c:
            try: self.opt_pol.load_state_dict(c['opt_pol'])
            except Exception: pass
        if 'opt_val' in c:
            try: self.opt_val.load_state_dict(c['opt_val'])
            except Exception: pass
        print(f'[DiscRSSM] loaded ep={self.episode}')


# ─────────────────────────────────────────────────────────
# ARCH 6: TRANSFORMER WM + HIERARCHICAL AC
# ─────────────────────────────────────────────────────────

TRF_D     = 64   # transformer model dim
TRF_HEADS = 4
TRF_LAYERS= 2
TRF_SEQ   = 16   # history window


class _CausalTransformerBlock(nn.Module):
    def __init__(self):
        super().__init__()
        self.attn = nn.MultiheadAttention(TRF_D, TRF_HEADS, batch_first=True)
        self.ff   = nn.Sequential(nn.Linear(TRF_D, TRF_D*2), nn.ReLU(), nn.Linear(TRF_D*2, TRF_D))
        self.ln1  = nn.LayerNorm(TRF_D); self.ln2 = nn.LayerNorm(TRF_D)

    def forward(self, x, mask):
        a, _ = self.attn(x, x, x, attn_mask=mask, need_weights=False)
        x = self.ln1(x + a); x = self.ln2(x + self.ff(x))
        return x


class _TransformerWMNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.inp_proj = nn.Linear(OBS_DIM, TRF_D)
        self.pos_emb  = nn.Parameter(torch.zeros(TRF_SEQ, TRF_D))
        nn.init.trunc_normal_(self.pos_emb, std=0.02)
        self.blocks = nn.ModuleList([_CausalTransformerBlock() for _ in range(TRF_LAYERS)])
        self.manager_net = nn.Sequential(nn.Linear(TRF_D, 64), nn.ReLU(), nn.Linear(64, GOAL))
        self.worker_pol  = nn.Linear(TRF_D + GOAL, N_ACTIONS)
        self.worker_val  = nn.Linear(TRF_D + GOAL, 1)
        nn.init.orthogonal_(self.worker_pol.weight, 0.01)
        self.register_buffer('_causal_mask', None)

    def get_mask(self, seq_len, dev):
        m = torch.full((seq_len, seq_len), float('-inf'), device=dev)
        m = torch.triu(m, diagonal=1)
        return m

    def forward(self, obs_seq):
        # obs_seq: (seq_len, obs_dim)
        S = obs_seq.size(0)
        x = self.inp_proj(obs_seq).unsqueeze(0)             # (1, S, D)
        x = x + self.pos_emb[:S].unsqueeze(0)
        mask = self.get_mask(S, obs_seq.device)
        for blk in self.blocks:
            x = blk(x, mask)
        return x[0, -1]   # last token (1, D) → (D,)


class Mk4TransformerAgent:
    CKPT = CKPT_DIR / 'mk4_transformer.pt'
    ARCH = 'transformer_wm_hier_ac'

    def __init__(self, device='cpu'):
        self.device = torch.device(device)
        self.net = _TransformerWMNet().to(self.device)
        params_pol = [p for n,p in self.net.named_parameters() if 'worker_val' not in n]
        self.opt_pol = torch.optim.Adam(params_pol, LR_POLICY)
        self.opt_val = torch.optim.Adam(self.net.worker_val.parameters(), LR_VALUE)
        self._buf: list[list[float]] = []  # ring buffer of obs
        self._goal = None; self._step = 0
        self._obs_buf, self._act_buf, self._rewards = [], [], []
        self.episode = self.total_updates = 0
        _try_load(self, self.CKPT)

    def reset_episode(self):
        self._buf = []; self._goal = None; self._step = 0
        self._obs_buf, self._act_buf, self._rewards = [], [], []
        self._old_lp_buf: list[float] = []
        self._val_buf:    list[float] = []

    def _context(self):
        pad = TRF_SEQ - len(self._buf)
        frames = [[0.0]*OBS_DIM]*pad + self._buf[-TRF_SEQ:]
        return torch.tensor(frames, dtype=torch.float32, device=self.device)

    def __call__(self, obs):
        self._buf.append(obs)
        if len(self._buf) > TRF_SEQ: self._buf.pop(0)
        self.net.eval()
        with torch.no_grad():
            ctx = self.net(self._context())
            if self._step % MGR_K == 0 or self._goal is None:
                self._goal = self.net.manager_net(ctx.unsqueeze(0))
            inp    = torch.cat([ctx.unsqueeze(0), self._goal], -1)
            logits = self.net.worker_pol(inp)
            val    = self.net.worker_val(inp).squeeze(-1)
            a      = Categorical(logits=logits).sample()
            old_lp = Categorical(logits=logits).log_prob(a)
        self._step += 1
        self._obs_buf.append(obs); self._act_buf.append(a.item())
        self._old_lp_buf.append(old_lp.item()); self._val_buf.append(val.item())
        return ACTIONS[a.item()]

    def record(self, r, done=False):
        self._rewards.append(r)
        if done: self.learn()

    def learn(self):
        from n64train.training.ppo_learner import (
            gae_advantages, ppo_loss, entropy_schedule, PPO_EPOCHS, GRAD_CLIP as PGRAD,
        )
        n = min(len(self._obs_buf), len(self._act_buf), len(self._rewards))
        if n < 2: self.reset_episode(); return None
        self.net.train(); self.episode += 1
        ent_coef = entropy_schedule(self.episode)
        act_t    = torch.tensor(self._act_buf[:n],     dtype=torch.long,    device=self.device)
        old_lp_t = torch.tensor(self._old_lp_buf[:n], dtype=torch.float32, device=self.device)
        val_t    = torch.tensor(self._val_buf[:n],     dtype=torch.float32, device=self.device)
        adv_t, ret_t = gae_advantages(self._rewards[:n], val_t)
        adv_t = adv_t.to(self.device); ret_t = ret_t.to(self.device)
        metrics_last = {}
        for _ in range(PPO_EPOCHS):
            logits_all, vals_all = [], []
            goal = torch.zeros(1, GOAL, device=self.device)
            hist: list[list[float]] = []
            for i, o in enumerate(self._obs_buf[:n]):
                hist.append(o)
                if len(hist) > TRF_SEQ: hist.pop(0)
                pad = TRF_SEQ - len(hist)
                frames = [[0.0]*OBS_DIM]*pad + hist
                seq = torch.tensor(frames, dtype=torch.float32, device=self.device)
                ctx = self.net(seq)
                if i % MGR_K == 0: goal = self.net.manager_net(ctx.unsqueeze(0))
                inp = torch.cat([ctx.unsqueeze(0), goal], -1)
                logits_all.append(self.net.worker_pol(inp))
                vals_all.append(self.net.worker_val(inp).squeeze(-1))
            logits_t = torch.cat(logits_all, 0); vals_t = torch.cat(vals_all, 0)
            loss, metrics_last = ppo_loss(
                logits_t, vals_t, act_t, old_lp_t, adv_t, ret_t, ent_coef=ent_coef)
            self.opt_pol.zero_grad(); self.opt_val.zero_grad()
            loss.backward(); nn.utils.clip_grad_norm_(self.net.parameters(), PGRAD)
            self.opt_pol.step(); self.opt_val.step()
        self.total_updates += 1
        m = {'episode': self.episode, 'arch': self.ARCH, 'n_steps': n, **metrics_last}
        _log_stats(m, self); self.reset_episode(); return m

    def save(self, path=None):
        path = path or self.CKPT
        torch.save({
            'net':           self.net.state_dict(),
            'opt_pol':       self.opt_pol.state_dict(),
            'opt_val':       self.opt_val.state_dict(),
            'episode':       self.episode,
            'total_updates': self.total_updates,
        }, path)

    def load(self, path=None):
        path = path or self.CKPT
        c = torch.load(path, map_location=self.device)
        self.net.load_state_dict(c['net'])
        self.episode = c.get('episode', 0); self.total_updates = c.get('total_updates', self.total_updates)
        if 'opt_pol' in c:
            try: self.opt_pol.load_state_dict(c['opt_pol'])
            except Exception: pass
        if 'opt_val' in c:
            try: self.opt_val.load_state_dict(c['opt_val'])
            except Exception: pass
        print(f'[Transformer] loaded ep={self.episode}')


# ─────────────────────────────────────────────────────────
# ARCH 7: OBJECT-CENTRIC + OPPONENT BELIEF (FLAGSHIP)
# ─────────────────────────────────────────────────────────
# obs(28) = 7 semantic features × 4 frames
# Reshape → (7, 4), embed each slot → (7, SLOT_D)
# Self-attention across slots → context
# Belief head: predicts opponent attacked (auxiliary BCE loss)

SLOT_D = 16
N_SLOTS = 7   # [p1_hp, p2_hp, timer, p1_x, p2_x, dist, facing]


class _ObjBeliefNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.slot_emb  = nn.Linear(4, SLOT_D)              # 4 time steps per slot
        self.attn      = nn.MultiheadAttention(SLOT_D, 2, batch_first=True)
        self.ln        = nn.LayerNorm(SLOT_D)
        ctx_dim = N_SLOTS * SLOT_D                          # 7*16=112
        self.belief_head = nn.Linear(ctx_dim, 1)            # binary: did CPU attack?
        self.policy_head = nn.Linear(ctx_dim, N_ACTIONS)
        self.value_head  = nn.Linear(ctx_dim, 1)
        nn.init.orthogonal_(self.policy_head.weight, 0.01)

    def forward(self, obs):
        # obs: (batch, 28) — FrameStack layout is FRAME-major:
        #   [frame0_feat0..frame0_feat6, frame1_feat0..frame1_feat6, ...] (4×7)
        # We want SLOT-major for attention: each "slot" = one feature across all frames.
        # Reshape to (B, 4_frames, 7_feats) then transpose → (B, 7_slots, 4_times).
        x = obs.view(-1, 4, N_SLOTS).transpose(1, 2)  # (B, 7, 4) slot-major
        slots = self.slot_emb(x)                   # (B, 7, 16)
        slots_a, _ = self.attn(slots, slots, slots, need_weights=False)
        slots = self.ln(slots + slots_a)           # (B, 7, 16)
        ctx = slots.view(-1, N_SLOTS * SLOT_D)     # (B, 112)
        belief = self.belief_head(ctx).squeeze(-1) # (B,) logit
        logits = self.policy_head(ctx)
        value  = self.value_head(ctx).squeeze(-1)
        return logits, value, belief


class Mk4ObjBeliefAgent:
    CKPT = CKPT_DIR / 'mk4_obj_belief.pt'
    ARCH = 'mk4_object_belief_hier_wm'

    def __init__(self, device='cpu'):
        self.device = torch.device(device)
        self.net = _ObjBeliefNet().to(self.device)
        pol_params = list(self.net.slot_emb.parameters()) + list(self.net.attn.parameters()) + \
                     list(self.net.ln.parameters()) + list(self.net.policy_head.parameters()) + \
                     list(self.net.belief_head.parameters())
        self.opt_pol = torch.optim.Adam(pol_params, LR_POLICY)
        self.opt_val = torch.optim.Adam(self.net.value_head.parameters(), LR_VALUE)
        self._obs_buf, self._act_buf, self._rewards = [], [], []
        self._cpu_attacked: list[float] = []   # auxiliary: did cpu attack this step?
        self.episode = self.total_updates = 0
        _try_load(self, self.CKPT)

    def reset_episode(self):
        self._obs_buf, self._act_buf, self._rewards, self._cpu_attacked = [], [], [], []
        self._old_lp_buf: list[float] = []
        self._val_buf:    list[float] = []

    def __call__(self, obs):
        self.net.eval()
        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        with torch.no_grad():
            logits, val, _ = self.net(x)
            a      = Categorical(logits=logits).sample()
            old_lp = Categorical(logits=logits).log_prob(a)
        self._obs_buf.append(obs); self._act_buf.append(a.item())
        self._old_lp_buf.append(old_lp.item()); self._val_buf.append(val.item())
        return ACTIONS[a.item()]

    def record(self, r, done=False, cpu_attacked: float = 0.0):
        """cpu_attacked=1.0 if P1 took damage this step (CPU attacked)."""
        self._rewards.append(r)
        self._cpu_attacked.append(cpu_attacked)
        if done: self.learn()

    def learn(self):
        from n64train.training.ppo_learner import (
            gae_advantages, ppo_loss, entropy_schedule, PPO_EPOCHS, GRAD_CLIP as PGRAD,
        )
        n = min(len(self._obs_buf), len(self._act_buf), len(self._rewards))
        if n < 2: self.reset_episode(); return None
        self.net.train(); self.episode += 1
        ent_coef = entropy_schedule(self.episode)
        obs_t    = torch.tensor(self._obs_buf[:n],     dtype=torch.float32, device=self.device)
        act_t    = torch.tensor(self._act_buf[:n],     dtype=torch.long,    device=self.device)
        cpu_t    = torch.tensor(self._cpu_attacked[:n], dtype=torch.float32, device=self.device)
        old_lp_t = torch.tensor(self._old_lp_buf[:n], dtype=torch.float32, device=self.device)
        val_t    = torch.tensor(self._val_buf[:n],     dtype=torch.float32, device=self.device)
        adv_t, ret_t = gae_advantages(self._rewards[:n], val_t)
        adv_t = adv_t.to(self.device); ret_t = ret_t.to(self.device)
        metrics_last = {}; bel_last = torch.tensor(0.0)
        for _ in range(PPO_EPOCHS):
            logits, vals, belief = self.net(obs_t)
            bel_last = F.binary_cross_entropy_with_logits(belief, cpu_t)
            loss, metrics_last = ppo_loss(
                logits, vals, act_t, old_lp_t, adv_t, ret_t, ent_coef=ent_coef)
            loss = loss + 0.1 * bel_last
            self.opt_pol.zero_grad(); self.opt_val.zero_grad()
            loss.backward(); nn.utils.clip_grad_norm_(self.net.parameters(), PGRAD)
            self.opt_pol.step(); self.opt_val.step()
        self.total_updates += 1
        m = {'episode': self.episode, 'arch': self.ARCH, 'n_steps': n,
             'belief_loss': round(bel_last.item(), 4), **metrics_last}
        _log_stats(m, self); self.reset_episode(); return m

    def save(self, path=None):
        path = path or self.CKPT
        torch.save({
            'net':           self.net.state_dict(),
            'opt_pol':       self.opt_pol.state_dict(),
            'opt_val':       self.opt_val.state_dict(),
            'episode':       self.episode,
            'total_updates': self.total_updates,
        }, path)

    def load(self, path=None):
        path = path or self.CKPT
        c = torch.load(path, map_location=self.device)
        self.net.load_state_dict(c['net'])
        self.episode = c.get('episode', 0); self.total_updates = c.get('total_updates', self.total_updates)
        if 'opt_pol' in c:
            try: self.opt_pol.load_state_dict(c['opt_pol'])
            except Exception: pass
        if 'opt_val' in c:
            try: self.opt_val.load_state_dict(c['opt_val'])
            except Exception: pass
        print(f'[ObjBelief] loaded ep={self.episode}')


# ─────────────────────────────────────────────────────────
# ARCH 8: LATENT PLANNER (MPC/CEM)
# ─────────────────────────────────────────────────────────

WM_LAT = 128   # world model latent dim
CEM_N  = 32    # CEM candidates
CEM_K  = 8     # top-K for CEM refitting
CEM_H  = 3     # planning horizon
CEM_IT = 3     # CEM iterations


class _WorldModel(nn.Module):
    """Predicts next latent from (latent, action)."""
    def __init__(self):
        super().__init__()
        self.enc     = nn.Sequential(nn.Linear(OBS_DIM, 64), nn.ReLU(), nn.Linear(64, WM_LAT))
        self.act_emb = nn.Embedding(N_ACTIONS, ACT_EMB)
        self.trans   = nn.Sequential(nn.Linear(WM_LAT + ACT_EMB, 128), nn.ReLU(), nn.Linear(128, WM_LAT))
        self.rew_hat = nn.Linear(WM_LAT, 1)
        self.pol     = nn.Linear(WM_LAT, N_ACTIONS)
        self.val     = nn.Linear(WM_LAT, 1)
        nn.init.orthogonal_(self.pol.weight, 0.01)

    def encode(self, obs):    return self.enc(obs)
    def step(self, z, a_idx): return self.trans(torch.cat([z, self.act_emb(a_idx)], -1))
    def reward(self, z):      return self.rew_hat(z).squeeze(-1)


class Mk4LatentPlannerAgent:
    CKPT = CKPT_DIR / 'mk4_latent_planner.pt'
    ARCH = 'latent_planner_mpc_prior'

    def __init__(self, device='cpu'):
        self.device = torch.device(device)
        self.net = _WorldModel().to(self.device)
        pol_params = [p for n,p in self.net.named_parameters() if 'val' not in n]
        self.opt_pol = torch.optim.Adam(pol_params, LR_POLICY)
        self.opt_val = torch.optim.Adam(self.net.val.parameters(), LR_VALUE)
        self.opt_wm  = torch.optim.Adam(
            list(self.net.enc.parameters()) + list(self.net.trans.parameters()) +
            list(self.net.act_emb.parameters()) + list(self.net.rew_hat.parameters()), LR_AUX)
        self._obs, self._act, self._rew, self._lat = [], [], [], []
        self.episode = self.total_updates = 0
        _try_load(self, self.CKPT)

    def reset_episode(self):
        self._obs, self._act, self._rew, self._lat = [], [], [], []

    def _cem_plan(self, z0):
        """CEM: find best action for current latent z0."""
        # Uniform prior
        mu  = torch.zeros(N_ACTIONS, device=self.device)
        std = torch.ones(N_ACTIONS, device=self.device)
        best_a = 0
        for _ in range(CEM_IT):
            # Sample CEM_N action sequences of length CEM_H
            scores = torch.zeros(CEM_N, device=self.device)
            first_actions = torch.zeros(CEM_N, dtype=torch.long, device=self.device)
            for k in range(CEM_N):
                z = z0.clone()
                total_r = 0.0
                act_seq = torch.multinomial(F.softmax(mu, -1).expand(CEM_H, -1), 1).squeeze(-1)
                first_actions[k] = act_seq[0]
                for h in range(CEM_H):
                    a = act_seq[h:h+1]
                    z = self.net.step(z, a)
                    total_r = total_r + (GAMMA**h) * self.net.reward(z.unsqueeze(0)).item()
                scores[k] = total_r
            # Refit distribution from top-K
            topk_idx = scores.topk(CEM_K).indices
            best_a = int(first_actions[topk_idx[0]].item())
            best_acts = first_actions[topk_idx]
            one_hot = F.one_hot(best_acts, N_ACTIONS).float()
            mu  = one_hot.mean(0) * 10  # sharpen toward best
        return best_a

    def __call__(self, obs):
        self.net.eval()
        x = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        with torch.no_grad():
            z = self.net.encode(x)
            a_idx = self._cem_plan(z)
        self._obs.append(obs); self._act.append(a_idx)
        self._lat.append(z.squeeze(0).detach().cpu())
        return ACTIONS[a_idx]

    def record(self, r, done=False):
        self._rew.append(r)
        if done: self.learn()

    def learn(self):
        n = min(len(self._obs), len(self._act), len(self._rew))
        if n < 2: self.reset_episode(); return None
        self.net.train(); self.episode += 1
        ret = _discounted_returns(self._rew[:n]).to(self.device)
        obs_t = torch.tensor(self._obs[:n], dtype=torch.float32, device=self.device)
        act_t = torch.tensor(self._act[:n], dtype=torch.long, device=self.device)
        lat_t = self.net.encode(obs_t)

        # Policy loss (REINFORCE)
        logits = self.net.pol(lat_t); vals = self.net.val(lat_t).squeeze(-1)
        dist = Categorical(logits=logits); lp = dist.log_prob(act_t); ent = dist.entropy()
        adv = ret - vals.detach()
        pol_loss = -(lp*adv).mean() + 0.5*F.mse_loss(vals, ret) - ENT_COEF*ent.mean()

        # World model loss — predict next latent + reward
        if n > 1:
            z_pred   = self.net.step(lat_t[:-1], act_t[:-1])
            wm_loss  = F.mse_loss(z_pred, lat_t[1:].detach())
            rew_pred = self.net.reward(lat_t)
            rew_loss = F.mse_loss(rew_pred, ret)
            wm_total = wm_loss + 0.5 * rew_loss
        else:
            wm_total = torch.tensor(0.0)

        self.opt_pol.zero_grad(); self.opt_val.zero_grad()
        pol_loss.backward(retain_graph=True)
        self.opt_wm.zero_grad()
        wm_total.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), GRAD_CLIP)
        self.opt_pol.step(); self.opt_val.step(); self.opt_wm.step()
        self.total_updates += 1
        m = {'episode': self.episode, 'arch': self.ARCH,
             'wm_loss': round(wm_total.item(),4), 'n_steps': n}
        _log_stats(m, self); self.reset_episode(); return m

    def save(self, path=None):
        path = path or self.CKPT
        torch.save({
            'net':           self.net.state_dict(),
            'opt_pol':       self.opt_pol.state_dict(),
            'opt_val':       self.opt_val.state_dict(),
            'episode':       self.episode,
            'total_updates': self.total_updates,
        }, path)

    def load(self, path=None):
        path = path or self.CKPT
        c = torch.load(path, map_location=self.device)
        self.net.load_state_dict(c['net'])
        self.episode = c.get('episode', 0); self.total_updates = c.get('total_updates', self.total_updates)
        if 'opt_pol' in c:
            try: self.opt_pol.load_state_dict(c['opt_pol'])
            except Exception: pass
        if 'opt_val' in c:
            try: self.opt_val.load_state_dict(c['opt_val'])
            except Exception: pass
        print(f'[LatentPlanner] loaded ep={self.episode}')


# ─────────────────────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────────────────────

ARCH_REGISTRY = {
    'gru':             Mk4GruAgent,
    'cnn_rnn_reactive_baseline': Mk4GruAgent,
    'cont_rssm':       Mk4ContRssmAgent,
    'continuous_rssm_hier_ac': Mk4ContRssmAgent,
    'disc_rssm':       Mk4DiscRssmAgent,
    'discrete_rssm_hier_ac': Mk4DiscRssmAgent,
    'transformer':     Mk4TransformerAgent,
    'transformer_wm_hier_ac': Mk4TransformerAgent,
    'obj_belief':      Mk4ObjBeliefAgent,
    'mk4_object_belief_hier_wm': Mk4ObjBeliefAgent,
    'latent_planner':  Mk4LatentPlannerAgent,
    'latent_planner_mpc_prior': Mk4LatentPlannerAgent,
}


def build_arch_agent(agent_type: str, device: str = 'cpu'):
    cls = ARCH_REGISTRY.get(agent_type)
    if cls is None:
        raise ValueError(f'Unknown arch: {agent_type}. Options: {list(ARCH_REGISTRY)}')
    return cls(device=device)
