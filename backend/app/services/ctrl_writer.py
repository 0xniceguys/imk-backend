"""
Controller writer — write N64 controller state to mmap files.

The custom input plugin (n64train-input) reads controller state from
memory-mapped files. Layout: 4 bytes = uint16 buttons + int8 x + int8 y.

This module writes to those files, matching the training code's
write_ctrl_worker() exactly.
"""

from __future__ import annotations

import mmap
import os
import struct

from app.services.actions import Button, ControllerState

# N64 hardware bitmask constants (matches vendor/n64train-input/plugin.c)
_BTN: dict[Button, int] = {
    Button.D_RIGHT: 1 << 0,
    Button.D_LEFT:  1 << 1,
    Button.D_DOWN:  1 << 2,
    Button.D_UP:    1 << 3,
    Button.START:   1 << 4,
    Button.Z:       1 << 5,
    Button.B:       1 << 6,
    Button.A:       1 << 7,
    Button.C_RIGHT: 1 << 8,
    Button.C_LEFT:  1 << 9,
    Button.C_DOWN:  1 << 10,
    Button.C_UP:    1 << 11,
    Button.R:       1 << 12,
    Button.L:       1 << 13,
}

CTRL_SIZE = 4


def _encode_axis(value: float) -> int:
    scaled = int(round(max(-1.0, min(1.0, value)) * 80))
    return max(-80, min(80, scaled))


def write_ctrl(state: ControllerState, path: str) -> None:
    """Write controller state to a mmap file for the input plugin to read.

    Matches training/src/n64train/training/worker.py:write_ctrl_worker exactly.
    """
    mask = 0
    for btn in state.pressed:
        mask |= _BTN.get(btn, 0)
    x = _encode_axis(state.analog_x)
    y = _encode_axis(state.analog_y)
    if not os.path.exists(path):
        with open(path, "w+b") as f:
            f.write(b"\x00" * CTRL_SIZE)
    with open(path, "r+b") as f:
        m = mmap.mmap(f.fileno(), CTRL_SIZE)
        m.seek(0)
        m.write(struct.pack("<Hbb", mask & 0xFFFF, x, y))
        m.flush()
        m.close()
