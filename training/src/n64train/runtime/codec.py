from __future__ import annotations

import base64
from typing import Any

from n64train.runtime.actions import Button, ControllerState, MacroAction
from n64train.runtime.memory import MemoryProbe
from n64train.runtime.types import (
    ActionPacket,
    DifficultySpec,
    EventLabel,
    MatchSetupSpec,
    ObservationBundle,
    ResetSpec,
    RewardTerms,
    ScenarioSpec,
    SpeedMode,
    StepResult,
    TimingKeys,
    TracedState,
)


def encode_controller_state(state: ControllerState) -> dict[str, Any]:
    return {
        "analog_x": state.analog_x,
        "analog_y": state.analog_y,
        "pressed": [button.value for button in sorted(state.pressed, key=lambda b: b.value)],
    }


def decode_controller_state(payload: dict[str, Any]) -> ControllerState:
    return ControllerState(
        analog_x=float(payload.get("analog_x", 0.0)),
        analog_y=float(payload.get("analog_y", 0.0)),
        pressed=frozenset(Button(str(value)) for value in payload.get("pressed", [])),
    )


def encode_action_packet(packet: ActionPacket) -> dict[str, Any]:
    return {
        "macro_action": packet.macro_action.value if packet.macro_action is not None else None,
        "micro_controller_state": encode_controller_state(packet.micro_controller_state),
        "repeat_frames": packet.repeat_frames,
    }


def decode_action_packet(payload: dict[str, Any]) -> ActionPacket:
    macro_raw = payload.get("macro_action")
    return ActionPacket(
        macro_action=MacroAction(str(macro_raw)) if macro_raw is not None else None,
        micro_controller_state=decode_controller_state(
            dict(payload.get("micro_controller_state", {}))
        ),
        repeat_frames=int(payload.get("repeat_frames", 1)),
    )


def encode_difficulty_spec(spec: DifficultySpec) -> dict[str, Any]:
    return {"use_max_cpu": spec.use_max_cpu, "cpu_level": spec.cpu_level}


def decode_difficulty_spec(payload: dict[str, Any]) -> DifficultySpec:
    return DifficultySpec(
        use_max_cpu=bool(payload.get("use_max_cpu", True)),
        cpu_level=payload.get("cpu_level"),
    )


def encode_match_setup_spec(spec: MatchSetupSpec) -> dict[str, Any]:
    return {
        "player_character_id": spec.player_character_id,
        "opponent_character_id": spec.opponent_character_id,
        "stage_id": spec.stage_id,
        "cpu_controls_opponent": spec.cpu_controls_opponent,
        "difficulty": encode_difficulty_spec(spec.difficulty),
        "notes": spec.notes,
    }


def decode_match_setup_spec(payload: dict[str, Any]) -> MatchSetupSpec:
    return MatchSetupSpec(
        player_character_id=payload.get("player_character_id"),
        opponent_character_id=payload.get("opponent_character_id"),
        stage_id=payload.get("stage_id"),
        cpu_controls_opponent=bool(payload.get("cpu_controls_opponent", True)),
        difficulty=decode_difficulty_spec(dict(payload.get("difficulty", {}))),
        notes=str(payload.get("notes", "")),
    )


def encode_reset_spec(spec: ResetSpec | None) -> dict[str, Any]:
    if spec is None:
        return {}
    return {
        "scenario_id": spec.scenario_id,
        "savestate_path": str(spec.savestate_path) if spec.savestate_path is not None else None,
        "slot": spec.slot,
        "episode_seed": spec.episode_seed,
    }


def decode_reset_spec(payload: dict[str, Any]) -> ResetSpec:
    from pathlib import Path

    path_raw = payload.get("savestate_path")
    return ResetSpec(
        scenario_id=payload.get("scenario_id"),
        savestate_path=Path(path_raw) if path_raw else None,
        slot=payload.get("slot"),
        episode_seed=payload.get("episode_seed"),
    )


def encode_timing_keys(keys: TimingKeys) -> dict[str, Any]:
    return {
        "run_id": keys.run_id,
        "episode_id": keys.episode_id,
        "emulator_frame_id": keys.emulator_frame_id,
        "action_frame_id": keys.action_frame_id,
        "scenario_id": keys.scenario_id,
    }


def decode_timing_keys(payload: dict[str, Any]) -> TimingKeys:
    return TimingKeys(
        run_id=str(payload["run_id"]),
        episode_id=str(payload["episode_id"]),
        emulator_frame_id=int(payload["emulator_frame_id"]),
        action_frame_id=int(payload["action_frame_id"]),
        scenario_id=payload.get("scenario_id"),
    )


def encode_traced_state(state: TracedState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "frame_id": state.frame_id,
        "p1_x": state.p1_x,
        "p2_x": state.p2_x,
        "p1_y": state.p1_y,
        "p2_y": state.p2_y,
        "p1_health": state.p1_health,
        "p2_health": state.p2_health,
        "timer": state.timer,
        "p1_facing": state.p1_facing,
        "p2_facing": state.p2_facing,
        "extras": state.extras,
    }


def decode_traced_state(payload: dict[str, Any] | None) -> TracedState | None:
    if payload is None:
        return None
    return TracedState(
        frame_id=int(payload["frame_id"]),
        p1_x=payload.get("p1_x"),
        p2_x=payload.get("p2_x"),
        p1_y=payload.get("p1_y"),
        p2_y=payload.get("p2_y"),
        p1_health=payload.get("p1_health"),
        p2_health=payload.get("p2_health"),
        timer=payload.get("timer"),
        p1_facing=payload.get("p1_facing"),
        p2_facing=payload.get("p2_facing"),
        extras=dict(payload.get("extras", {})),
    )


def encode_observation_bundle(obs: ObservationBundle) -> dict[str, Any]:
    frame_b64 = None
    if obs.frame_bytes is not None:
        frame_b64 = base64.b64encode(obs.frame_bytes).decode("ascii")
    return {
        "timing": encode_timing_keys(obs.timing),
        "traced_state": encode_traced_state(obs.traced_state),
        "frame_shape": list(obs.frame_shape) if obs.frame_shape is not None else None,
        "frame_bytes_b64": frame_b64,
        "privileged_features": dict(obs.privileged_features),
        "meta_context": dict(obs.meta_context),
    }


def decode_observation_bundle(payload: dict[str, Any]) -> ObservationBundle:
    frame_shape_raw = payload.get("frame_shape")
    frame_bytes_b64 = payload.get("frame_bytes_b64")
    frame_bytes = base64.b64decode(frame_bytes_b64) if frame_bytes_b64 is not None else None
    frame_shape = tuple(frame_shape_raw) if frame_shape_raw is not None else None
    return ObservationBundle(
        timing=decode_timing_keys(dict(payload["timing"])),
        traced_state=decode_traced_state(payload.get("traced_state")),
        frame_shape=frame_shape,  # type: ignore[arg-type]
        frame_bytes=frame_bytes,
        privileged_features=dict(payload.get("privileged_features", {})),
        meta_context=dict(payload.get("meta_context", {})),
    )


def encode_reward_terms(reward: RewardTerms) -> dict[str, Any]:
    return {
        "round_win": reward.round_win,
        "damage_dealt": reward.damage_dealt,
        "damage_taken": reward.damage_taken,
        "hit_confirm_bonus": reward.hit_confirm_bonus,
        "block_success_bonus": reward.block_success_bonus,
        "whiff_punished_penalty": reward.whiff_punished_penalty,
        "idle_timeout_penalty": reward.idle_timeout_penalty,
        "illegal_state_penalty": reward.illegal_state_penalty,
        "extras": dict(reward.extras),
    }


def decode_reward_terms(payload: dict[str, Any]) -> RewardTerms:
    return RewardTerms(
        round_win=float(payload.get("round_win", 0.0)),
        damage_dealt=float(payload.get("damage_dealt", 0.0)),
        damage_taken=float(payload.get("damage_taken", 0.0)),
        hit_confirm_bonus=float(payload.get("hit_confirm_bonus", 0.0)),
        block_success_bonus=float(payload.get("block_success_bonus", 0.0)),
        whiff_punished_penalty=float(payload.get("whiff_punished_penalty", 0.0)),
        idle_timeout_penalty=float(payload.get("idle_timeout_penalty", 0.0)),
        illegal_state_penalty=float(payload.get("illegal_state_penalty", 0.0)),
        extras={str(k): float(v) for k, v in dict(payload.get("extras", {})).items()},
    )


def encode_event_label(event: EventLabel) -> dict[str, Any]:
    return {
        "name": event.name,
        "present": event.present,
        "confidence": event.confidence,
        "payload": dict(event.payload),
    }


def decode_event_label(payload: dict[str, Any]) -> EventLabel:
    return EventLabel(
        name=str(payload["name"]),
        present=bool(payload.get("present", False)),
        confidence=float(payload.get("confidence", 1.0)),
        payload=dict(payload.get("payload", {})),
    )


def encode_step_result(result: StepResult) -> dict[str, Any]:
    return {
        "observation": encode_observation_bundle(result.observation),
        "reward_terms": encode_reward_terms(result.reward_terms),
        "events": [encode_event_label(event) for event in result.events],
        "done": result.done,
        "truncated": result.truncated,
        "info": dict(result.info),
    }


def decode_step_result(payload: dict[str, Any]) -> StepResult:
    return StepResult(
        observation=decode_observation_bundle(dict(payload["observation"])),
        reward_terms=decode_reward_terms(dict(payload.get("reward_terms", {}))),
        events=tuple(decode_event_label(dict(x)) for x in payload.get("events", [])),
        done=bool(payload.get("done", False)),
        truncated=bool(payload.get("truncated", False)),
        info=dict(payload.get("info", {})),
    )


def encode_scenario_spec(spec: ScenarioSpec) -> dict[str, Any]:
    return spec.to_dict()


def decode_scenario_spec(payload: dict[str, Any]) -> ScenarioSpec:
    return ScenarioSpec.from_dict(payload)


def encode_speed_mode(mode: SpeedMode) -> str:
    return mode.value


def decode_speed_mode(value: str) -> SpeedMode:
    return SpeedMode(str(value))


def encode_memory_probes(probes: list[MemoryProbe]) -> list[dict[str, Any]]:
    return [{"name": p.name, "address": p.address, "size": p.size} for p in probes]


def decode_memory_probes(payload: list[dict[str, Any]]) -> list[MemoryProbe]:
    return [MemoryProbe(name=str(p["name"]), address=int(p["address"]), size=int(p["size"])) for p in payload]
