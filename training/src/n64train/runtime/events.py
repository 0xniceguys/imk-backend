from __future__ import annotations

from dataclasses import dataclass

from n64train.runtime.types import EventLabel, TracedState


class EventExtractor:
    def extract(
        self,
        prev_state: TracedState | None,
        next_state: TracedState | None,
    ) -> tuple[EventLabel, ...]:
        raise NotImplementedError


@dataclass
class SimpleCombatEventExtractor(EventExtractor):
    """
    Emits a minimal event stream from traced state deltas.

    This is intentionally simple until action-state and hit/block flags are traced.
    """

    def extract(
        self,
        prev_state: TracedState | None,
        next_state: TracedState | None,
    ) -> tuple[EventLabel, ...]:
        if prev_state is None or next_state is None:
            return ()
        events: list[EventLabel] = []
        if (
            prev_state.p1_health is not None
            and next_state.p1_health is not None
            and next_state.p1_health < prev_state.p1_health
        ):
            events.append(
                EventLabel(
                    name="p1_took_damage",
                    present=True,
                    payload={"delta": prev_state.p1_health - next_state.p1_health},
                )
            )
        if (
            prev_state.p2_health is not None
            and next_state.p2_health is not None
            and next_state.p2_health < prev_state.p2_health
        ):
            events.append(
                EventLabel(
                    name="p2_took_damage",
                    present=True,
                    payload={"delta": prev_state.p2_health - next_state.p2_health},
                )
            )
        if (
            prev_state.timer is not None
            and next_state.timer is not None
            and next_state.timer < prev_state.timer
        ):
            events.append(
                EventLabel(
                    name="timer_decrement",
                    present=True,
                    payload={"delta": prev_state.timer - next_state.timer},
                )
            )
        return tuple(events)
