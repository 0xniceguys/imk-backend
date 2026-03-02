from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ObservationMode(str, Enum):
    FRAME = "frame"
    RAM = "ram"
    FRAME_AND_RAM = "frame+ram"


@dataclass(frozen=True)
class ObservationSpec:
    mode: ObservationMode = ObservationMode.FRAME
    frame_width: int = 320
    frame_height: int = 240
    grayscale: bool = False
    ram_bytes: int = 0
