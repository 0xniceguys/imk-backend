#!/usr/bin/env python3
"""
Export trained MK4 checkpoints (.pt) to ONNX.

Primary targets are the four production agents:
  - lstm
  - disc_rssm
  - transformer
  - obj_belief

Also supports mlp checkpoints.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

N64_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(N64_ROOT / "training" / "src"))

from n64train.experiments.mk4_agent import (  # noqa: E402
    CKPT_DIR,
    N_ACTIONS,
    OBS_DIM,
    Mk4LstmAgent,
    Mk4MlpAgent,
)
from n64train.experiments.mk4_architectures import (  # noqa: E402
    CAT_SZ,
    N_CATS,
    TRF_SEQ,
    Mk4DiscRssmAgent,
    Mk4ObjBeliefAgent,
    Mk4TransformerAgent,
)

AGENTS: dict[str, type] = {
    "mlp": Mk4MlpAgent,
    "lstm": Mk4LstmAgent,
    "disc_rssm": Mk4DiscRssmAgent,
    "transformer": Mk4TransformerAgent,
    "obj_belief": Mk4ObjBeliefAgent,
}


class _MlpOnnxWrapper(nn.Module):
    def __init__(self, net: nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        logits, _ = self.net(obs)
        return logits


class _LstmOnnxWrapper(nn.Module):
    """Stateful LSTM export with explicit hidden state IO."""

    def __init__(self, net: nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(
        self,
        obs: torch.Tensor,
        h_in: torch.Tensor,
        c_in: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits, _, (h_out, c_out) = self.net(obs, (h_in, c_in))
        return logits, h_out, c_out


class _DiscRssmOnnxWrapper(nn.Module):
    """Discrete RSSM export with deterministic prior sampling."""

    def __init__(self, net: nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(
        self,
        obs: torch.Tensor,
        h_in: torch.Tensor,
        prev_act: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        e = self.net.enc(obs)
        ae = self.net.act_emb(prev_act)
        h = self.net.det_gru(torch.cat([e, ae], dim=-1), h_in)

        pr_logits = self.net.prior(h).view(-1, N_CATS, CAT_SZ)
        z_idx = torch.argmax(pr_logits, dim=-1)
        z_onehot = F.one_hot(z_idx, num_classes=CAT_SZ).to(dtype=obs.dtype)
        z_flat = z_onehot.view(obs.size(0), -1)

        lat = torch.cat([h, z_flat], dim=-1)
        goal = self.net.manager_net(lat)
        logits = self.net.worker_pol(torch.cat([lat, goal], dim=-1))
        return logits, h


class _TransformerOnnxWrapper(nn.Module):
    def __init__(self, net: nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(self, obs_seq: torch.Tensor) -> torch.Tensor:
        ctx = self.net(obs_seq)
        goal = self.net.manager_net(ctx.unsqueeze(0))
        logits = self.net.worker_pol(torch.cat([ctx.unsqueeze(0), goal], dim=-1))
        return logits


class _ObjBeliefOnnxWrapper(nn.Module):
    """
    Stateless obj_belief export for current backend generic loader.

    We reset GRU hidden state to zeros each call, producing a pure obs->logits
    ONNX model compatible with OnnxAgent.
    """

    def __init__(self, net: nn.Module) -> None:
        super().__init__()
        self.net = net
        self.hidden_size = int(self.net.temporal_gru.hidden_size)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        h = torch.zeros(
            obs.size(0),
            self.hidden_size,
            dtype=obs.dtype,
            device=obs.device,
        )
        logits, _, _, _ = self.net(obs, h)
        return logits


def _export(
    model: nn.Module,
    inputs: tuple[torch.Tensor, ...],
    out_path: Path,
    *,
    input_names: list[str],
    output_names: list[str],
    dynamic_axes: dict[str, dict[int, str]] | None = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        inputs,
        str(out_path),
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        opset_version=18,
        dynamo=False,
    )


def export_agent(agent, arch: str, out_path: Path) -> None:
    net = agent.net.eval()

    if arch == "mlp":
        wrapper = _MlpOnnxWrapper(net).eval()
        dummy_obs = torch.zeros(1, OBS_DIM, dtype=torch.float32)
        _export(
            wrapper,
            (dummy_obs,),
            out_path,
            input_names=["obs"],
            output_names=["logits"],
            dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
        )
        print(f"  ✓ mlp        -> {out_path.name} (obs=1x{OBS_DIM})")
        return

    if arch == "lstm":
        hidden = int(net.hidden if hasattr(net, "hidden") else net.lstm.hidden_size)
        wrapper = _LstmOnnxWrapper(net).eval()
        dummy_obs = torch.zeros(1, OBS_DIM, dtype=torch.float32)
        dummy_h = torch.zeros(1, 1, hidden, dtype=torch.float32)
        dummy_c = torch.zeros(1, 1, hidden, dtype=torch.float32)
        _export(
            wrapper,
            (dummy_obs, dummy_h, dummy_c),
            out_path,
            input_names=["obs", "h_in", "c_in"],
            output_names=["logits", "h_out", "c_out"],
            dynamic_axes=None,
        )
        print(f"  ✓ lstm       -> {out_path.name} (obs=1x{OBS_DIM}, hidden={hidden})")
        return

    if arch == "disc_rssm":
        det_size = int(net.det_gru.hidden_size)
        wrapper = _DiscRssmOnnxWrapper(net).eval()
        dummy_obs = torch.zeros(1, OBS_DIM, dtype=torch.float32)
        dummy_h = torch.zeros(1, det_size, dtype=torch.float32)
        dummy_prev = torch.zeros(1, dtype=torch.long)
        _export(
            wrapper,
            (dummy_obs, dummy_h, dummy_prev),
            out_path,
            input_names=["obs", "h_in", "prev_act"],
            output_names=["logits", "h_out"],
            dynamic_axes={
                "obs": {0: "batch"},
                "h_in": {0: "batch"},
                "prev_act": {0: "batch"},
                "logits": {0: "batch"},
                "h_out": {0: "batch"},
            },
        )
        print(f"  ✓ disc_rssm  -> {out_path.name} (obs=1x{OBS_DIM}, det={det_size})")
        return

    if arch == "transformer":
        wrapper = _TransformerOnnxWrapper(net).eval()
        dummy_seq = torch.zeros(TRF_SEQ, OBS_DIM, dtype=torch.float32)
        _export(
            wrapper,
            (dummy_seq,),
            out_path,
            input_names=["obs_seq"],
            output_names=["logits"],
            dynamic_axes=None,
        )
        print(f"  ✓ transformer -> {out_path.name} (obs_seq={TRF_SEQ}x{OBS_DIM})")
        return

    if arch == "obj_belief":
        wrapper = _ObjBeliefOnnxWrapper(net).eval()
        dummy_obs = torch.zeros(1, OBS_DIM, dtype=torch.float32)
        _export(
            wrapper,
            (dummy_obs,),
            out_path,
            input_names=["obs"],
            output_names=["logits"],
            dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}},
        )
        print(f"  ✓ obj_belief -> {out_path.name} (obs=1x{OBS_DIM})")
        return

    raise ValueError(f"Unsupported agent for ONNX export: {arch}")


def _infer_arch_from_stem(stem: str) -> str | None:
    keys = ("disc_rssm", "obj_belief", "transformer", "lstm", "mlp")
    for key in keys:
        if key in stem:
            return key
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MK4 checkpoints to ONNX")
    parser.add_argument("--agent", default=None, choices=sorted(AGENTS.keys()))
    parser.add_argument("--ckpt", default=None, help="explicit .pt checkpoint path")
    parser.add_argument(
        "--ckpt-dir",
        default=None,
        dest="ckpt_dir",
        help=f"checkpoint directory (default: {CKPT_DIR})",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        dest="out_dir",
        help="output directory for ONNX files (default: ckpt-dir)",
    )
    args = parser.parse_args()

    ckpt_dir = Path(args.ckpt_dir) if args.ckpt_dir else CKPT_DIR
    out_dir = Path(args.out_dir) if args.out_dir else ckpt_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    to_export: list[tuple[str, Path]] = []

    if args.ckpt:
        pt = Path(args.ckpt)
        if not pt.exists():
            print(f"Checkpoint not found: {pt}")
            return 1
        arch = args.agent or _infer_arch_from_stem(pt.stem)
        if arch is None:
            print("Could not infer --agent from checkpoint name. Pass --agent explicitly.")
            return 1
        to_export.append((arch, pt))
    elif args.agent:
        found = sorted(ckpt_dir.glob(f"*{args.agent}*.pt"))
        if not found:
            print(f"No checkpoint found for agent '{args.agent}' in {ckpt_dir}")
            return 1
        to_export.extend((args.agent, pt) for pt in found)
    else:
        for pt in sorted(ckpt_dir.glob("*.pt")):
            arch = _infer_arch_from_stem(pt.stem)
            if arch is None:
                print(f"  ? skipping {pt.name} (unknown arch in filename)")
                continue
            to_export.append((arch, pt))

    if not to_export:
        print(f"No checkpoints discovered in {ckpt_dir}")
        return 1

    print(f"\nExporting {len(to_export)} checkpoint(s) from {ckpt_dir}\n")

    failures = 0
    for arch, pt_path in to_export:
        cls = AGENTS.get(arch)
        if cls is None:
            print(f"  ? no handler for {arch}; skipping {pt_path.name}")
            continue

        out_path = out_dir / f"{pt_path.stem}.onnx"
        print(f"Loading {pt_path.name} as {arch} ...")
        try:
            agent = cls(device="cpu")
            agent.load(pt_path)
            with torch.no_grad():
                export_agent(agent, arch, out_path)
        except Exception as exc:
            failures += 1
            print(f"  ✗ FAILED {pt_path.name}: {exc}")

    print(f"\nDone. ONNX files in: {out_dir}")
    if failures:
        print(f"Export finished with {failures} failure(s).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
