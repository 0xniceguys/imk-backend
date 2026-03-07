"""
device.py — Auto-detect best available PyTorch device.

Priority: CUDA (NVIDIA GPU) → MPS (Apple Silicon) → CPU.
All agents call auto_device() instead of hardcoding 'cpu'.
"""
from __future__ import annotations

import torch


def auto_device() -> str:
    """Return the best available device string for torch."""
    if torch.cuda.is_available():
        return 'cuda'
    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'
