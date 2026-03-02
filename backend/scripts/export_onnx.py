#!/usr/bin/env python3
"""
Export trained PyTorch agents to ONNX format for the backend.

Run from repo root:
    python backend/scripts/export_onnx.py

Reads checkpoints from training/data/checkpoints/
Writes .onnx files to backend/app/agents/checkpoints/

Requires PyTorch (one-time export, not needed at runtime).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add training src to path so we can import the model architectures
REPO_ROOT = Path(__file__).resolve().parents[2]
TRAINING_SRC = REPO_ROOT / "training" / "src"
sys.path.insert(0, str(TRAINING_SRC))

import torch

CKPT_DIR = REPO_ROOT / "training" / "data" / "checkpoints"
OUTPUT_DIR = REPO_ROOT / "backend" / "app" / "agents" / "checkpoints"


def export_mlp() -> None:
    """Export MLP policy to ONNX."""
    from n64train.experiments.mk4_agent import Mk4PolicyNet

    ckpt_path = CKPT_DIR / "mk4_policy.pt"
    if not ckpt_path.exists():
        print(f"[skip] MLP checkpoint not found: {ckpt_path}")
        return

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    net = Mk4PolicyNet(obs_dim=28)
    net.load_state_dict(ckpt["net"])
    net.eval()

    # MLP forward returns (logits, value) — we only need logits
    # Wrap to return just logits
    class MlpLogitsOnly(torch.nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, obs):
            logits, _ = self.net(obs)
            return logits

    wrapper = MlpLogitsOnly(net)
    dummy_input = torch.randn(1, 28)

    out_path = OUTPUT_DIR / "mlp.onnx"
    torch.onnx.export(
        wrapper,
        dummy_input,
        str(out_path),
        input_names=["obs"],
        output_names=["logits"],
        dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
    )
    ep = ckpt.get("episode", "?")
    print(f"[ok] MLP exported: {out_path} (episode={ep})")


def export_lstm() -> None:
    """Export LSTM policy to ONNX (single-step, no hidden state export)."""
    from n64train.experiments.mk4_agent import Mk4LstmNet

    ckpt_path = CKPT_DIR / "mk4_lstm_policy.pt"
    if not ckpt_path.exists():
        print(f"[skip] LSTM checkpoint not found: {ckpt_path}")
        return

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    net = Mk4LstmNet(obs_dim=28)
    net.load_state_dict(ckpt["net"])
    net.eval()

    # For ONNX: flatten single-step LSTM into a stateless model
    # Input: obs (1, 28), h (1, 1, 128), c (1, 1, 128)
    # Output: logits (1, n_actions), h_out (1, 1, 128), c_out (1, 1, 128)
    class LstmSingleStep(torch.nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, obs, h, c):
            logits, _, (h_out, c_out) = self.net(obs, (h, c))
            return logits, h_out, c_out

    wrapper = LstmSingleStep(net)
    dummy_obs = torch.randn(1, 28)
    dummy_h = torch.zeros(1, 1, 128)
    dummy_c = torch.zeros(1, 1, 128)

    out_path = OUTPUT_DIR / "lstm.onnx"
    torch.onnx.export(
        wrapper,
        (dummy_obs, dummy_h, dummy_c),
        str(out_path),
        input_names=["obs", "h_in", "c_in"],
        output_names=["logits", "h_out", "c_out"],
        dynamic_axes={
            "obs": {0: "batch"},
            "logits": {0: "batch"},
        },
        opset_version=17,
    )
    ep = ckpt.get("episode", "?")
    print(f"[ok] LSTM exported: {out_path} (episode={ep})")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Exporting to: {OUTPUT_DIR}")
    print(f"Checkpoints from: {CKPT_DIR}")
    print()
    export_mlp()
    export_lstm()
    print("\nDone. Place additional .onnx files in the checkpoints dir to enable more agents.")


if __name__ == "__main__":
    main()
