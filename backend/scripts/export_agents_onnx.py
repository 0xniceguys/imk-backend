#!/usr/bin/env python3
"""Export trained PyTorch agents to ONNX for backend inference.

Usage:
    cd backend && python scripts/export_agents_onnx.py

Reads .pt checkpoints from training/data/checkpoints/
Writes .onnx files to backend/app/agents/checkpoints/
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add training/src to path for model architectures
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "training" / "src"))

import torch
import torch.nn as nn

TRAINING_CKPT_DIR = REPO_ROOT / "training" / "training" / "data" / "checkpoints"
BACKEND_CKPT_DIR = REPO_ROOT / "backend" / "app" / "agents" / "checkpoints"

# Import model architectures from training
from n64train.experiments.mk4_architectures import (
    _ObjBeliefNet,
    _DiscRssmNet,
    _TransformerWMNet,
    OBS_DIM,
    N_ACTIONS,
    DET,
    DISC_Z,
    N_CATS,
    CAT_SZ,
    TRF_SEQ,
    TRF_D,
    GOAL,
)


# ── obj_belief: simple forward, frame-stacked input ──

class ObjBeliefPolicy(nn.Module):
    """Wraps _ObjBeliefNet to output only logits (no value/belief)."""

    def __init__(self, net: _ObjBeliefNet):
        super().__init__()
        self.net = net

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        logits, _value, _belief = self.net(obs)
        return logits


def export_obj_belief():
    ckpt_path = TRAINING_CKPT_DIR / "mk4_obj_belief.pt"
    if not ckpt_path.exists():
        print(f"SKIP obj_belief: {ckpt_path} not found")
        return

    c = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    net = _ObjBeliefNet()
    net.load_state_dict(c["net"])
    net.eval()

    wrapper = ObjBeliefPolicy(net)
    wrapper.eval()

    dummy = torch.randn(1, OBS_DIM)
    out_path = BACKEND_CKPT_DIR / "obj_belief.onnx"

    torch.onnx.export(
        wrapper,
        dummy,
        str(out_path),
        input_names=["obs"],
        output_names=["logits"],
        dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
    )
    print(f"OK obj_belief → {out_path} (input: obs(1,28) → logits(1,{N_ACTIONS}))")


# ── disc_rssm: GRU-based, needs hidden state I/O ──

class DiscRssmPolicy(nn.Module):
    """Wraps _DiscRssmNet for inference: obs + h_in + prev_act → logits + h_out."""

    def __init__(self, net: _DiscRssmNet):
        super().__init__()
        self.net = net

    def forward(
        self,
        obs: torch.Tensor,
        h_in: torch.Tensor,
        prev_act: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        # Encode: deterministic GRU step + posterior sampling
        e = self.net.enc(obs)
        ae = self.net.act_emb(prev_act)
        h = self.net.det_gru(torch.cat([e, ae], -1), h_in)

        # Use prior for inference (no posterior available without ground truth)
        pr_logits = self.net.prior(h).view(-1, N_CATS, CAT_SZ)
        z = torch.softmax(pr_logits, dim=-1)
        # Hard argmax for deterministic inference
        z_hard = torch.zeros_like(z)
        z_hard.scatter_(-1, z.argmax(-1, keepdim=True), 1.0)
        z_flat = z_hard.view(-1, DISC_Z)

        lat = torch.cat([h, z_flat], -1)
        goal = self.net.manager_net(lat)
        logits = self.net.worker_pol(torch.cat([lat, goal], -1))

        return logits, h


def export_disc_rssm():
    ckpt_path = TRAINING_CKPT_DIR / "mk4_disc_rssm.pt"
    if not ckpt_path.exists():
        print(f"SKIP disc_rssm: {ckpt_path} not found")
        return

    c = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    net = _DiscRssmNet()
    net.load_state_dict(c["net"])
    net.eval()

    wrapper = DiscRssmPolicy(net)
    wrapper.eval()

    dummy_obs = torch.randn(1, OBS_DIM)
    dummy_h = torch.zeros(1, DET)
    dummy_act = torch.zeros(1, dtype=torch.long)
    out_path = BACKEND_CKPT_DIR / "disc_rssm.onnx"

    torch.onnx.export(
        wrapper,
        (dummy_obs, dummy_h, dummy_act),
        str(out_path),
        input_names=["obs", "h_in", "prev_act"],
        output_names=["logits", "h_out"],
        opset_version=17,
    )
    print(f"OK disc_rssm → {out_path} (input: obs(1,28)+h(1,128)+act(1) → logits(1,{N_ACTIONS})+h(1,128))")


# ── transformer: fixed-length context window ──

class TransformerPolicy(nn.Module):
    """Wraps _TransformerWMNet for inference: context_window → logits."""

    def __init__(self, net: _TransformerWMNet):
        super().__init__()
        self.net = net

    def forward(self, obs_seq: torch.Tensor) -> torch.Tensor:
        # obs_seq: (TRF_SEQ, OBS_DIM) — padded context window
        ctx = self.net(obs_seq)  # (TRF_D,)
        goal = self.net.manager_net(ctx)
        logits = self.net.worker_pol(torch.cat([ctx, goal], -1))
        return logits.unsqueeze(0)  # (1, N_ACTIONS)


def export_transformer():
    ckpt_path = TRAINING_CKPT_DIR / "mk4_transformer.pt"
    if not ckpt_path.exists():
        print(f"SKIP transformer: {ckpt_path} not found")
        return

    c = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    net = _TransformerWMNet()
    net.load_state_dict(c["net"])
    net.eval()

    wrapper = TransformerPolicy(net)
    wrapper.eval()

    dummy = torch.randn(TRF_SEQ, OBS_DIM)
    out_path = BACKEND_CKPT_DIR / "transformer.onnx"

    torch.onnx.export(
        wrapper,
        dummy,
        str(out_path),
        input_names=["obs_seq"],
        output_names=["logits"],
        opset_version=17,
    )
    print(f"OK transformer → {out_path} (input: obs_seq({TRF_SEQ},{OBS_DIM}) → logits(1,{N_ACTIONS}))")


if __name__ == "__main__":
    BACKEND_CKPT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Training checkpoints: {TRAINING_CKPT_DIR}")
    print(f"Output directory:     {BACKEND_CKPT_DIR}")
    print()

    export_obj_belief()
    export_disc_rssm()
    export_transformer()
