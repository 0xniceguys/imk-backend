"""
smoke_test_gpu.py — Verify the full training import chain and agent construction.

Tests:
  1. auto_device() detection
  2. All 8 agent imports + instantiation (no checkpoint required)
  3. Single forward pass for each agent
  4. learner.py import chain (llm_coach, ppo_learner)
  5. paths.py repo root detection

Run from repo root:
  training/.venv/bin/python training/scripts/smoke_test_gpu.py
"""
import sys
import traceback
from pathlib import Path

# Add src to path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / 'training/src'))
sys.path.insert(0, str(ROOT / 'training/scripts'))

PASS = '[PASS]'
FAIL = '[FAIL]'
results = []

def check(name, fn):
    try:
        result = fn()
        msg = f'{PASS} {name}'
        if result is not None:
            msg += f'  →  {result}'
        print(msg)
        results.append((name, True, None))
    except Exception as e:
        print(f'{FAIL} {name}')
        traceback.print_exc()
        results.append((name, False, str(e)))

# ── 1. Device detection ────────────────────────────────────────────────────────
print('\n── Device ─────────────────────────────────────────────────────')

def test_device():
    from n64train.device import auto_device
    import torch
    dev = auto_device()
    cuda_ok = torch.cuda.is_available()
    name = torch.cuda.get_device_name(0) if cuda_ok else 'cpu'
    return f'auto_device()={dev!r}  cuda={cuda_ok}  ({name})'

check('auto_device + CUDA detection', test_device)

# ── 2. paths.py ────────────────────────────────────────────────────────────────
print('\n── Paths ──────────────────────────────────────────────────────')

def test_paths():
    from n64train.paths import PATHS
    return f'repo_root={PATHS.repo_root}'

check('paths.py repo root detection', test_paths)

# ── 3. ppo_learner imports ─────────────────────────────────────────────────────
print('\n── PPO Learner ────────────────────────────────────────────────')

def test_ppo_learner():
    from n64train.training.ppo_learner import (
        gae_advantages, ppo_loss, entropy_schedule, anneal_lr,
        PPO_EPOCHS, GRAD_CLIP, LR,
    )
    return f'LR={LR}  PPO_EPOCHS={PPO_EPOCHS}  GRAD_CLIP={GRAD_CLIP}'

check('ppo_learner imports', test_ppo_learner)

# ── 4. llm_coach imports ───────────────────────────────────────────────────────
print('\n── LLM Coach ──────────────────────────────────────────────────')

def test_llm_coach():
    from n64train.training.llm_coach import (
        FighterCoach, RoundStats, MacroStats,
        MACRO_REVIEW_EVERY, FIGHTER_DESCRIPTIONS,
    )
    return f'MACRO_REVIEW_EVERY={MACRO_REVIEW_EVERY}  agents={list(FIGHTER_DESCRIPTIONS.keys())}'

check('llm_coach imports', test_llm_coach)

# ── 5. Agent imports ───────────────────────────────────────────────────────────
print('\n── Agent Imports ──────────────────────────────────────────────')

def test_mk4_agent_import():
    from n64train.experiments.mk4_agent import Mk4MlpAgent, Mk4LstmAgent, OBS_DIM, N_ACTIONS, RAW_OBS_DIM
    return f'OBS_DIM={OBS_DIM}  N_ACTIONS={N_ACTIONS}  RAW_OBS_DIM={RAW_OBS_DIM}'

check('mk4_agent.py imports', test_mk4_agent_import)

def test_mk4_arch_import():
    from n64train.experiments.mk4_architectures import (
        Mk4GruAgent, Mk4ContRssmAgent, Mk4DiscRssmAgent,
        Mk4TransformerAgent, Mk4ObjBeliefAgent, Mk4LatentPlannerAgent,
        build_arch_agent,
    )
    return 'all 6 arch agents imported'

check('mk4_architectures.py imports', test_mk4_arch_import)

# ── 6. Agent instantiation + forward pass ─────────────────────────────────────
print('\n── Agent Forward Passes ───────────────────────────────────────')

import torch

def _fake_obs(n=56):
    return [0.0] * n

def test_mlp():
    from n64train.experiments.mk4_agent import Mk4MlpAgent, OBS_DIM
    a = Mk4MlpAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_lstm():
    from n64train.experiments.mk4_agent import Mk4LstmAgent, OBS_DIM
    a = Mk4LstmAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_gru():
    from n64train.experiments.mk4_architectures import Mk4GruAgent, OBS_DIM
    a = Mk4GruAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_cont_rssm():
    from n64train.experiments.mk4_architectures import Mk4ContRssmAgent, OBS_DIM
    a = Mk4ContRssmAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_disc_rssm():
    from n64train.experiments.mk4_architectures import Mk4DiscRssmAgent, OBS_DIM
    a = Mk4DiscRssmAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_transformer():
    from n64train.experiments.mk4_architectures import Mk4TransformerAgent, OBS_DIM
    a = Mk4TransformerAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_obj_belief():
    from n64train.experiments.mk4_architectures import Mk4ObjBeliefAgent, OBS_DIM
    a = Mk4ObjBeliefAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

def test_latent_planner():
    from n64train.experiments.mk4_architectures import Mk4LatentPlannerAgent, OBS_DIM
    a = Mk4LatentPlannerAgent()
    obs = _fake_obs(OBS_DIM)
    action = a(obs)
    return f'device={a.device}  action={action.name}'

check('Mk4MlpAgent         __call__', test_mlp)
check('Mk4LstmAgent        __call__', test_lstm)
check('Mk4GruAgent         __call__', test_gru)
check('Mk4ContRssmAgent    __call__', test_cont_rssm)
check('Mk4DiscRssmAgent    __call__', test_disc_rssm)
check('Mk4TransformerAgent __call__', test_transformer)
check('Mk4ObjBeliefAgent   __call__', test_obj_belief)
check('Mk4LatentPlannerAgent __call__', test_latent_planner)

# ── 7. Mini learn() cycle ──────────────────────────────────────────────────────
print('\n── Mini learn() cycle (MLP + LSTM) ────────────────────────────')

def test_mlp_learn():
    from n64train.experiments.mk4_agent import Mk4MlpAgent, OBS_DIM
    a = Mk4MlpAgent()
    obs = _fake_obs(OBS_DIM)
    for i in range(5):
        a(obs)
        a.record(float(i), done=(i == 4))
    return f'episode={a.episode}  updates={a.total_updates}'

def test_lstm_learn():
    from n64train.experiments.mk4_agent import Mk4LstmAgent, OBS_DIM
    a = Mk4LstmAgent()
    obs = _fake_obs(OBS_DIM)
    a.reset_episode()
    for i in range(5):
        a(obs)
        a.record(float(i), done=(i == 4))
    return f'episode={a.episode}  updates={a.total_updates}'

check('Mk4MlpAgent  learn() 5 steps', test_mlp_learn)
check('Mk4LstmAgent learn() 5 steps', test_lstm_learn)

# ── 8. build_arch_agent registry ──────────────────────────────────────────────
print('\n── build_arch_agent registry ──────────────────────────────────')

def test_registry():
    from n64train.experiments.mk4_architectures import build_arch_agent, ARCH_REGISTRY
    keys = list(ARCH_REGISTRY.keys())
    # Spot-check one arch from registry
    a = build_arch_agent('gru')
    return f'{len(keys)} keys  gru device={a.device}'

check('build_arch_agent registry', test_registry)

# ── Summary ────────────────────────────────────────────────────────────────────
print('\n── Summary ────────────────────────────────────────────────────')
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f'{passed}/{len(results)} passed')
if failed:
    print('\nFailed:')
    for name, ok, err in results:
        if not ok:
            print(f'  {FAIL} {name}: {err}')
    sys.exit(1)
else:
    print('All checks passed — training flow is ready.')
