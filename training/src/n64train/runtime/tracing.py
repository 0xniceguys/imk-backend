from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from n64train.runtime.types import TracedState


class TraceProvider(Protocol):
    def read(self, frame_id: int) -> TracedState: ...


@dataclass
class NullTraceProvider:
    """
    Placeholder tracer used until RAM reverse engineering is implemented.

    It returns a frame-aligned TracedState with no semantic values yet.
    """

    def read(self, frame_id: int) -> TracedState:
        return TracedState(frame_id=frame_id)
