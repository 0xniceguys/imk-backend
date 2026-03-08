"""
Match Runner — orchestrates a single live match.

Architecture (decoupled loops):
  Loop A (agent brain, ~10 Hz):
    Read game state from RAM → agent decides → write controller mmap
  Loop B (frame delivery, 60 Hz):
    FFmpeg captures Xvfb display → MJPEG pipe → JPEG frames → WebSocket

The emulator always runs freely at native speed. No frame-stepping.
mupen64plus config sets ScreenWidth/Height so the window opens at the
correct size on headless Xvfb — otherwise x11grab captures a black screen.

Self-contained: ZERO imports from the training package.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.agents import create_agent
from app.agents.base import FighterAgent
from app.services.actions import (
    ControllerState,
    encode_controller_state,
    resolve_action,
)
from app.services.anti_camping import AntiCampingGuard
from app.services.bridge import EmulatorBridge
from app.services.ctrl_writer import write_ctrl
from app.services.emulator import EmulatorSession, LaunchOptions
from app.services.ffmpeg_capture import ffmpeg_available, is_linux
from app.services.ffmpeg_combined_hls import FFmpegCombinedHls
from app.services.ram_debug import RamDebugRecorder
from app.services.game_state import FightState, is_round_over, p1_won, read_fight_state
from app.ws.connection_manager import manager as ws_manager

logger = logging.getLogger(__name__)
KO_CONFIRM_FRAMES = 5


def apply_round_end_policy(
    state: FightState,
    *,
    sample_flags: list[str],
    round_done: bool,
    round_over_reason: str | None,
    ko_streaks: dict[str, int],
    ko_confirm_frames: int = KO_CONFIRM_FRAMES,
) -> tuple[bool, bool, str | None, dict[str, int]]:
    next_streaks = {"p1_ko": 0, "p2_ko": 0, "double_ko": 0}
    invalid_ko_sample = any(flag in {"p1_health_out_of_range", "p2_health_out_of_range", "fallback_state"} for flag in sample_flags)

    if round_over_reason in next_streaks and not invalid_ko_sample:
        next_streaks[round_over_reason] = int(ko_streaks.get(round_over_reason, 0)) + 1
        confirmed = next_streaks[round_over_reason] >= ko_confirm_frames
        return confirmed, p1_won(state) if confirmed else False, round_over_reason if confirmed else None, next_streaks

    if round_done:
        return True, p1_won(state), round_over_reason, next_streaks

    return False, False, None, next_streaks

class RunnerState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    ROUND_OVER = "round_over"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


class StreamingState(str, Enum):
    """HLS streaming lifecycle state."""
    NOT_STARTED = "not_started"
    INITIALIZING = "initializing"
    READY = "ready"
    PLAYING = "playing"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class GameSnapshot:
    """Latest game state read from RAM."""
    p1_health: int = 160
    p2_health: int = 160
    timer: int = 99
    p1_x: float = 0.0
    p2_x: float = 0.0
    frame_id: int = 0
    round_over: bool = False
    p1_won: bool = False
    current_round: int = 1
    rounds_won_p1: int = 0
    rounds_won_p2: int = 0
    best_of: int = 3
    timestamp: float = field(default_factory=time.time)
    # Combat signals (from training update 2026-03-04)
    p1_action: float = 0.0
    p2_action: float = 0.0
    p1_y_vel: float = 0.0
    p1_hitstun: float = 0.0
    p2_hitstun: float = 0.0
    p1_airborne: float = 0.0
    p2_airborne: float = 0.0
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "game_state",
            "p1_health": self.p1_health,
            "p2_health": self.p2_health,
            "p1_health_pct": round(self.p1_health / 160, 4),
            "p2_health_pct": round(self.p2_health / 160, 4),
            "timer": self.timer,
            "p1_x": self.p1_x,
            "p2_x": self.p2_x,
            "frame_id": self.frame_id,
            "round_over": self.round_over,
            "p1_won": self.p1_won,
            "current_round": self.current_round,
            "rounds_won_p1": self.rounds_won_p1,
            "rounds_won_p2": self.rounds_won_p2,
            "best_of": self.best_of,
            "timestamp": self.timestamp,
            # Combat signals
            "p1_action": self.p1_action,
            "p2_action": self.p2_action,
            "p1_y_vel": self.p1_y_vel,
            "p1_hitstun": self.p1_hitstun,
            "p2_hitstun": self.p2_hitstun,
            "p1_airborne": self.p1_airborne,
            "p2_airborne": self.p2_airborne,
            "debug": self.debug,
        }


@dataclass
class ManualOverrideState:
    """Per-player manual controller override used by the admin viewer."""
    enabled: bool = False
    controller_state: ControllerState = field(default_factory=ControllerState)
    updated_at: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        payload = encode_controller_state(self.controller_state)
        payload.update({
            "enabled": self.enabled,
            "updated_at": self.updated_at,
        })
        return payload


class MatchRunner:
    """
    Manages the lifecycle of a single emulator match.

    Supports multi-round (best-of-N) matches. After each round KO,
    reloads the savestate and continues until a player wins enough rounds.

    The emulator runs freely at native speed on all platforms.
    FFmpeg captures the display at 30fps (x11grab on Linux, avfoundation on macOS).
    The agent brain runs independently at ~10Hz reading RAM and writing inputs.
    """

    def __init__(
        self,
        match_id: str,
        savestate_path: str,
        instance_id: str | None = None,
        p1_agent: FighterAgent | None = None,
        p2_agent: FighterAgent | None = None,
        agent_interval: int = 3,
        best_of: int = 3,
    ) -> None:
        self.match_id = match_id
        self.savestate_path = savestate_path
        self.instance_id = instance_id or f"match-{uuid.uuid4().hex[:8]}"
        self.state = RunnerState.IDLE
        self.streaming_state = StreamingState.NOT_STARTED
        self.latest_snapshot: GameSnapshot = GameSnapshot(best_of=best_of)
        self.latest_frame: bytes | None = None

        # Agents — default to random for both P1 and P2
        self.p1_agent: FighterAgent = p1_agent or create_agent("random")
        self.p2_agent: FighterAgent = p2_agent or create_agent("random")
        self.agent_interval = agent_interval

        # Multi-round state
        self.best_of = best_of
        self.current_round = 1
        self.rounds_won_p1 = 0
        self.rounds_won_p2 = 0

        self._session: EmulatorSession | None = None
        self._bridge: EmulatorBridge | None = None
        self._ctrl_p1_path: str | None = None
        self._ctrl_p2_path: str | None = None
        self._agent_loop_task: asyncio.Task | None = None
        # Combined HLS capture: one FFmpeg for video + audio
        self._hls_capture = FFmpegCombinedHls(match_id=self.match_id)
        self._ram_debug = RamDebugRecorder(match_id=self.match_id, instance_id=self.instance_id)
        self._manual_overrides: dict[int, ManualOverrideState] = {
            1: ManualOverrideState(),
            2: ManualOverrideState(),
        }
        self._bridge_lock = asyncio.Lock()
        self._round_context_reset_requested = False
        # Game state broadcast throttle: cap at 5Hz to avoid spamming clients
        self._last_state_broadcast: float = 0.0
        # Streaming state monitoring
        self._stream_monitor_task: asyncio.Task | None = None
        # Anti-camping: injects real N64 D-pad inputs when fighters are stuck
        self._anti_camping = AntiCampingGuard()

    @property
    def rounds_to_win(self) -> int:
        return (self.best_of // 2) + 1

    def manual_control_payload(self) -> dict[str, Any]:
        return {
            "p1": self._manual_overrides[1].to_payload(),
            "p2": self._manual_overrides[2].to_payload(),
        }

    def _ctrl_path_for_player(self, player: int) -> str | None:
        if player == 1:
            return self._ctrl_p1_path
        if player == 2:
            return self._ctrl_p2_path
        raise ValueError("player must be 1 or 2")

    async def _write_player_state(self, player: int, controller_state: ControllerState) -> None:
        ctrl_path = self._ctrl_path_for_player(player)
        if not ctrl_path:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, write_ctrl, controller_state.clipped(), ctrl_path)

    async def set_manual_mode(self, player: int, enabled: bool) -> dict[str, Any]:
        override = self._manual_overrides[player]
        override.enabled = bool(enabled)
        override.updated_at = time.time()
        if not override.enabled:
            override.controller_state = ControllerState()
        await self._write_player_state(
            player,
            override.controller_state if override.enabled else ControllerState(),
        )
        self._ram_debug.record_event(
            "manual_mode_updated",
            player=player,
            enabled=override.enabled,
        )
        return self.manual_control_payload()

    async def set_manual_controller_state(
        self,
        player: int,
        controller_state: ControllerState,
        *,
        enable: bool = True,
    ) -> dict[str, Any]:
        override = self._manual_overrides[player]
        override.enabled = bool(enable)
        override.controller_state = controller_state.clipped()
        override.updated_at = time.time()
        await self._write_player_state(player, override.controller_state)
        self._ram_debug.record_event(
            "manual_controller_updated",
            player=player,
            enabled=override.enabled,
            controller_state=override.to_payload(),
        )
        return self.manual_control_payload()

    async def release_manual_controls(
        self,
        player: int | None = None,
        *,
        disable: bool = False,
    ) -> dict[str, Any]:
        players = (player,) if player in (1, 2) else (1, 2)
        for current in players:
            override = self._manual_overrides[current]
            override.controller_state = ControllerState()
            if disable:
                override.enabled = False
            override.updated_at = time.time()
            await self._write_player_state(current, ControllerState())
        self._ram_debug.record_event(
            "manual_controls_released",
            player=player,
            disable=disable,
        )
        return self.manual_control_payload()

    async def debug_load_savestate(self, savestate_path: str | None = None) -> dict[str, Any]:
        if not self._bridge:
            raise RuntimeError("Bridge not connected")
        if savestate_path:
            self.savestate_path = savestate_path
        async with self._bridge_lock:
            await self._load_savestate()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._bridge.debugger_command, "run")
        self._round_context_reset_requested = True
        self._ram_debug.record_event(
            "manual_savestate_reload",
            savestate_path=self.savestate_path,
        )
        return {
            "ok": True,
            "savestate_path": self.savestate_path,
            "manual_control": self.manual_control_payload(),
        }

    def _control_source_label(self, player: int, macro_action: Any) -> str:
        if self._manual_overrides[player].enabled:
            return "MANUAL"
        return str(macro_action)

    def _build_snapshot_debug(
        self,
        state: FightState,
        *,
        sample_flags: list[str],
        raw_round_done: bool,
        raw_round_over_reason: str | None,
        round_done: bool,
        round_over_reason: str | None,
        ko_streaks: dict[str, int],
    ) -> dict[str, Any]:
        debug_info = state.debug_info or {}
        direct_probe = debug_info.get("direct_probe")
        contract_payload = debug_info.get("contract_payload")

        def _compact_probe_field(name: str) -> dict[str, Any] | None:
            if not isinstance(direct_probe, dict):
                return None
            field = direct_probe.get(name)
            if not isinstance(field, dict):
                return None
            return {
                "value": field.get("value"),
                "error": field.get("error"),
            }

        compact_contract: dict[str, Any] | None = None
        if isinstance(contract_payload, dict):
            compact_contract = {
                "frame_id": contract_payload.get("frame_id"),
                "p1_health": contract_payload.get("p1_health"),
                "p2_health": contract_payload.get("p2_health"),
                "timer": contract_payload.get("timer"),
                "timer_raw": contract_payload.get("timer_raw"),
                "p1_health_word": contract_payload.get("p1_health_word"),
                "p2_health_word": contract_payload.get("p2_health_word"),
                "p1_x": contract_payload.get("p1_x"),
                "p2_x": contract_payload.get("p2_x"),
            }

        return {
            "state_source": debug_info.get("state_source"),
            "selected_frame_id": debug_info.get("selected_frame_id"),
            "timer_decode_source": debug_info.get("timer_decode_source"),
            "contract_core_trusted": debug_info.get("contract_core_trusted"),
            "logic_trusted": not sample_flags,
            "sample_flags": list(sample_flags),
            "source_map": dict(debug_info.get("source_map", {})) if isinstance(debug_info.get("source_map"), dict) else {},
            "p1_health_word_used": debug_info.get("p1_health_word_used"),
            "p2_health_word_used": debug_info.get("p2_health_word_used"),
            "raw_round_done": raw_round_done,
            "raw_round_over_reason": raw_round_over_reason,
            "round_done": round_done,
            "round_over_reason": round_over_reason,
            "ko_streaks": dict(ko_streaks),
            "ram_debug_log_path": str(self._ram_debug.file_path),
            "manual_control": self.manual_control_payload(),
            "direct_probe": {
                "p1_health_word": _compact_probe_field("p1_health_word"),
                "p2_health_word": _compact_probe_field("p2_health_word"),
                "p1_health_hud": _compact_probe_field("p1_health_hud"),
                "p2_health_hud": _compact_probe_field("p2_health_hud"),
                "timer_raw": _compact_probe_field("timer_raw"),
                "timer_word_u32": _compact_probe_field("timer_word_u32"),
                "p1_x_word": _compact_probe_field("p1_x_word"),
                "p2_x_word": _compact_probe_field("p2_x_word"),
            },
            "contract_payload": compact_contract,
        }

    async def start(self) -> None:
        """Launch emulator, connect bridge, load savestate, start loops."""
        if self.state not in (RunnerState.IDLE, RunnerState.STOPPED, RunnerState.ERROR):
            raise RuntimeError(f"Cannot start runner in state {self.state}")

        if not ffmpeg_available():
            raise RuntimeError(
                "FFmpeg is required for frame capture but was not found. "
                "Install it: brew install ffmpeg (macOS) or sudo apt install ffmpeg (Linux)"
            )

        self.state = RunnerState.STARTING
        logger.info("Starting match runner %s (instance=%s)", self.match_id, self.instance_id)
        logger.info("RAM debug trace: %s", self._ram_debug.file_path)
        self._ram_debug.record_event(
            "runner_starting",
            savestate_path=self.savestate_path,
            best_of=self.best_of,
            rounds_to_win=self.rounds_to_win,
            agent_interval=self.agent_interval,
        )

        try:
            await self._launch_emulator()
            await self._connect_bridge()

            # Warm-up: send 'run' and wait before stateload.
            # The video plugin (rice) does not fully initialize its OpenGL context
            # until the emulator has started running at least once. Sending stateload
            # while mupen is still at the initial (dbg) prompt triggers plugin callbacks
            # before GL is ready, causing a crash (EIO on the PTY).
            loop = asyncio.get_running_loop()
            try:
                logger.info("Emulator warm-up: starting run before stateload (instance=%s)", self.instance_id)
                await loop.run_in_executor(None, self._bridge.debugger_command, "run")
                await asyncio.sleep(1.5)
            except Exception as warm_exc:
                logger.warning("Warm-up run failed (will still attempt stateload): %s", warm_exc)

            await self._load_savestate()

            # Reset agents
            self.p1_agent.reset()
            self.p2_agent.reset()

            self.state = RunnerState.RUNNING

            # Broadcast global event that match is going live
            await ws_manager.broadcast_global_event({
                "type": "match_status_changed",
                "match_id": self.match_id,
                "status": "live",
                "timestamp": time.time(),
            })

            # Clear any stale Redis cache from previous runs of this match
            try:
                from app.services.redis_client import clear_match_cache
                await clear_match_cache(self.match_id)
            except Exception:
                pass

            # Let emulator run freely, FFmpeg captures display at 30fps
            await self._start_free_running()

            self._agent_loop_task = asyncio.create_task(self._match_loop())
            logger.info(
                "Match runner %s is LIVE (P1=%s, P2=%s, best_of=%d)",
                self.match_id,
                self.p1_agent.__class__.__name__,
                self.p2_agent.__class__.__name__,
                self.best_of,
            )
        except Exception:
            self.state = RunnerState.ERROR
            logger.exception("Failed to start match runner %s", self.match_id)
            raise

    async def _start_free_running(self) -> None:
        """Let the emulator run freely and start combined H.264+AAC HLS capture.

        Video and audio are both captured by a single FFmpeg process and muxed
        into HLS segments. Flutter's VideoPlayer plays stream.m3u8 directly,
        giving perfect A/V sync with no extra pipeline complexity.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._bridge.debugger_command, "run")
        logger.info("Emulator set to free-running mode")

        # Give the video plugin time to render the first frame before FFmpeg starts
        await asyncio.sleep(1.5)

        # Update streaming state and notify clients
        self.streaming_state = StreamingState.INITIALIZING
        await ws_manager.broadcast_json(self.match_id, {
            "type": "streaming_state",
            "state": self.streaming_state.value,
        })

        await self._hls_capture.start()
        logger.info("Combined HLS capture started for match %s", self.match_id)

        # Start monitoring for HLS playlist ready
        self._stream_monitor_task = asyncio.create_task(
            self._monitor_hls_ready(), name=f"hls-monitor-{self.match_id}"
        )

    async def _monitor_hls_ready(self) -> None:
        """Poll until HLS playlist is ready, then notify clients."""
        max_wait = 20  # seconds
        poll_interval = 0.5
        elapsed = 0.0

        while elapsed < max_wait:
            if self._hls_capture.ready_for_playback():
                self.streaming_state = StreamingState.READY
                logger.info("HLS stream ready for match %s", self.match_id)
                await ws_manager.broadcast_json(self.match_id, {
                    "type": "streaming_state",
                    "state": self.streaming_state.value,
                    "hls_url": f"/stream/{self.match_id}/stream.m3u8",
                })
                return

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout — stream failed to initialize
        self.streaming_state = StreamingState.ERROR
        logger.error("HLS stream failed to initialize for match %s", self.match_id)
        await ws_manager.broadcast_json(self.match_id, {
            "type": "streaming_state",
            "state": self.streaming_state.value,
            "error": "Stream initialization timeout",
        })


    async def stop(self) -> None:
        """Stop the match, kill emulator."""
        logger.info("Stopping match runner %s", self.match_id)
        self.state = RunnerState.STOPPED
        self.streaming_state = StreamingState.STOPPED

        # Stop HLS monitor
        if self._stream_monitor_task and not self._stream_monitor_task.done():
            self._stream_monitor_task.cancel()
            try:
                await self._stream_monitor_task
            except asyncio.CancelledError:
                pass

        # Stop combined HLS capture (video + audio)
        await self._hls_capture.stop()

        # Cancel background task
        if self._agent_loop_task and not self._agent_loop_task.done():
            self._agent_loop_task.cancel()
            try:
                await self._agent_loop_task
            except asyncio.CancelledError:
                pass

        # Close bridge
        if self._bridge:
            try:
                self._bridge.close()
            except Exception:
                pass
            self._bridge = None

        # Stop emulator
        if self._session:
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None

        # Notify viewers
        stopped_payload = {
            "type": "match_ended",
            "match_id": self.match_id,
        }
        self._ram_debug.record_event("runner_stopped", state=self.state.value)
        await ws_manager.broadcast_json(self.match_id, stopped_payload)
        # Cache so reconnecting clients know the match is over
        try:
            from app.services.redis_client import cache_match_ended
            await cache_match_ended(self.match_id, stopped_payload)
        except Exception:
            pass

    async def _launch_emulator(self) -> None:
        """Launch the bridge server (which manages mupen64plus internally)."""
        opts = LaunchOptions(
            instance_id=self.instance_id,
            resolution="640x480",
        )
        self._session = EmulatorSession(opts)
        self._ctrl_p1_path = self._session.ctrl_p1_path
        self._ctrl_p2_path = self._session.ctrl_p2_path

        loop = asyncio.get_running_loop()

        # Start bridge server process
        await loop.run_in_executor(None, self._session.start)

        # Wait for the bridge socket to appear (up to 45s)
        ready = await loop.run_in_executor(None, self._session.wait_for_socket, 45.0)
        if not ready:
            raise RuntimeError(
                f"Bridge server failed to start (instance={self.instance_id})"
            )

        logger.info("Bridge server ready (instance=%s)", self.instance_id)

    async def _connect_bridge(self) -> None:
        """Connect to the bridge server's Unix socket with retry."""
        if not self._session:
            raise RuntimeError("Emulator session not started")

        socket_path = self._session.socket_path
        logger.info("Connecting to bridge at %s", socket_path)

        loop = asyncio.get_running_loop()
        bridge = EmulatorBridge(socket_path)

        # Retry connect + hello — emulator may need time to boot
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                await loop.run_in_executor(None, bridge.connect)
                resp = await loop.run_in_executor(None, bridge.hello)
                break
            except Exception as e:
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Failed to connect to bridge after {max_retries} attempts: {e}"
                    ) from e
                logger.warning(
                    "Bridge connect attempt %d/%d failed: %s — retrying in %ds",
                    attempt, max_retries, e, attempt * 5,
                )
                bridge.close()
                bridge = EmulatorBridge(socket_path)
                await asyncio.sleep(attempt * 5)

        self._bridge = bridge

        caps = resp.payload.get("capabilities", {})
        logger.info(
            "Bridge connected (set_inputs=%s, frame_step=%s, debugger=%s)",
            caps.get("set_inputs", False),
            caps.get("frame_step", False),
            caps.get("debugger_command", False),
        )
        self._ram_debug.record_event(
            "bridge_connected",
            capabilities=dict(caps),
            socket_path=socket_path,
        )

    async def _load_savestate(self) -> None:
        """Load the configured savestate into the emulator."""
        if not self._bridge:
            raise RuntimeError("Bridge not connected")

        loop = asyncio.get_running_loop()

        # Ensure emulator is paused before loading (safe even if already paused)
        try:
            await loop.run_in_executor(
                None, self._bridge.debugger_command, "pause"
            )
        except Exception:
            pass  # May already be paused
        await asyncio.sleep(0.3)

        # Load via bridge LOAD_SAVESTATE protocol (checks for M64P_STATELOAD_OK)
        result = await loop.run_in_executor(None, self._bridge.load_savestate, self.savestate_path)
        logger.info("Savestate loaded: %s → %s", self.savestate_path, result)
        self._ram_debug.record_event(
            "savestate_loaded",
            savestate_path=self.savestate_path,
            bridge_response=str(result),
            round=self.current_round,
        )

        # Brief settle time
        await asyncio.sleep(1.0)

    async def _match_loop(self) -> None:
        """Outer match loop: runs rounds until a player wins enough."""
        try:
            while self.state == RunnerState.RUNNING:
                logger.info(
                    "Round %d/%d starting (P1=%d, P2=%d)",
                    self.current_round, self.best_of,
                    self.rounds_won_p1, self.rounds_won_p2,
                )
                self._ram_debug.record_event(
                    "round_started",
                    round=self.current_round,
                    rounds_won_p1=self.rounds_won_p1,
                    rounds_won_p2=self.rounds_won_p2,
                )

                p1_won_round = await self._round_loop()

                if self.state != RunnerState.ROUND_OVER:
                    break  # Stopped or error

                # Update round scores
                if p1_won_round:
                    self.rounds_won_p1 += 1
                else:
                    self.rounds_won_p2 += 1

                match_over = (
                    self.rounds_won_p1 >= self.rounds_to_win
                    or self.rounds_won_p2 >= self.rounds_to_win
                )

                # Broadcast round_end with scores
                await ws_manager.broadcast_json(self.match_id, {
                    "type": "round_end",
                    "p1_won": p1_won_round,
                    "current_round": self.current_round,
                    "rounds_won_p1": self.rounds_won_p1,
                    "rounds_won_p2": self.rounds_won_p2,
                    "best_of": self.best_of,
                    "match_over": match_over,
                    "p1_health": self.latest_snapshot.p1_health,
                    "p2_health": self.latest_snapshot.p2_health,
                    "frame_id": self.latest_snapshot.frame_id,
                })
                self._ram_debug.record_event(
                    "round_ended",
                    round=self.current_round,
                    p1_won=p1_won_round,
                    rounds_won_p1=self.rounds_won_p1,
                    rounds_won_p2=self.rounds_won_p2,
                    p1_health=self.latest_snapshot.p1_health,
                    p2_health=self.latest_snapshot.p2_health,
                    timer=self.latest_snapshot.timer,
                )

                if match_over:
                    winner_player = 1 if self.rounds_won_p1 >= self.rounds_to_win else 2
                    logger.info(
                        "Match over! P%d wins (%d-%d)",
                        winner_player, self.rounds_won_p1, self.rounds_won_p2,
                    )
                    self.state = RunnerState.COMPLETED

                    ended_payload = {
                        "type": "match_ended",
                        "match_id": self.match_id,
                        "winner_player": winner_player,
                        "rounds_won_p1": self.rounds_won_p1,
                        "rounds_won_p2": self.rounds_won_p2,
                    }
                    self._ram_debug.record_event(
                        "match_completed",
                        winner_player=winner_player,
                        rounds_won_p1=self.rounds_won_p1,
                        rounds_won_p2=self.rounds_won_p2,
                    )
                    await ws_manager.broadcast_json(self.match_id, ended_payload)

                    # Cache terminal event in Redis so cold-start rejoins get it
                    try:
                        from app.services.redis_client import cache_match_ended
                        await cache_match_ended(self.match_id, ended_payload)
                    except Exception:
                        pass

                    await self._auto_settle(winner_player)
                    break

                # More rounds to play — reload savestate.
                self.current_round += 1
                logger.info("Between rounds — reloading savestate for round %d", self.current_round)

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._bridge.debugger_command, "pause")

                # Between rounds: stop HLS capture so there's no stale stream
                # through the round transition. It will be restarted by
                # _start_free_running after the savestate reload.
                await self._hls_capture.stop()

                await asyncio.sleep(2.0)  # KO animation visible

                await self._load_savestate()
                self.p1_agent.reset()
                self.p2_agent.reset()
                self._anti_camping.reset()  # clear position history for new round
                self.state = RunnerState.RUNNING

                await self._start_free_running()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in match loop")
            self.state = RunnerState.ERROR
            # ✅ FIX: Update DB to reflect runner failure
            await self._mark_match_errored()

        # Cleanup emulator
        await self._cleanup_emulator()

    async def _round_loop(self) -> bool:
        """Single round: agent brain loop at ~10Hz.

        The emulator runs freely at native speed.
        FFmpeg captures frames independently at 30fps.
        This loop ONLY reads RAM and writes agent inputs.

        Returns True if P1 won the round, False if P2 won.
        """
        if not self._bridge or not self._ctrl_p1_path or not self._ctrl_p2_path:
            return False
        loop = asyncio.get_running_loop()
        ctrl_p1 = self._ctrl_p1_path
        ctrl_p2 = self._ctrl_p2_path

        neutral = ControllerState()
        write_ctrl(neutral, ctrl_p1)
        write_ctrl(neutral, ctrl_p2)

        step_count = 0
        winner_p1 = False
        consecutive_errors = 0
        previous_state: FightState | None = None
        ko_streaks = {"p1_ko": 0, "p2_ko": 0, "double_ko": 0}

        step_count = 0
        winner_p1 = False
        consecutive_errors = 0
        agent_interval_sec = 1.0 / 10.0  # 100ms between reads

        # Grace period: skip round-over detection for first N steps.
        # Use 30 steps (~3s) to allow health addresses to stabilise after
        # savestate load.
        ROUND_OVER_GRACE_STEPS = 30

        while self.state == RunnerState.RUNNING:
            step_start = time.monotonic()
            try:
                if self._round_context_reset_requested:
                    previous_state = None
                    ko_streaks = {"p1_ko": 0, "p2_ko": 0, "double_ko": 0}
                    step_count = 0
                    self._round_context_reset_requested = False


                # 1. Read game state from RAM (free-running — parse fix handles correct values)
                async with self._bridge_lock:
                    loop = asyncio.get_running_loop()
                    state = await loop.run_in_executor(
                        None, read_fight_state, self._bridge, step_count, previous_state
                    )
                previous_state = state

                # 3. Agent decisions, unless a player is under manual control.
                p1_action = None if self._manual_overrides[1].enabled else self.p1_agent.choose_action(state, player=1)
                p2_action = None if self._manual_overrides[2].enabled else self.p2_agent.choose_action(state, player=2)

                # 3a. Anti-camping override — fires ONLY when far apart + not moving.
                #     Uses real N64 D-pad inputs (D_RIGHT/D_LEFT), no macros.
                #     check() is a no-op when characters are already at close range.
                p1_camp, p2_camp = self._anti_camping.check(state.p1_x, state.p2_x)

                # 4. Write controller states to mmap
                if self._manual_overrides[1].enabled:
                    p1_controller = self._manual_overrides[1].controller_state.clipped()
                elif p1_camp is not None:
                    p1_controller = p1_camp
                else:
                    p1_controller = resolve_action(p1_action).micro_controller_state
                if self._manual_overrides[2].enabled:
                    p2_controller = self._manual_overrides[2].controller_state.clipped()
                elif p2_camp is not None:
                    p2_controller = p2_camp
                else:
                    p2_controller = resolve_action(p2_action).micro_controller_state
                write_ctrl(p1_controller, ctrl_p1)
                write_ctrl(p2_controller, ctrl_p2)

                step_count += 1

                # Debug log every 30 steps (~3s)
                if step_count % 30 == 0:
                    logger.info(
                        "R%d Brain step %d: P1=%s P2=%s | HP %d-%d T=%d",
                        self.current_round, step_count,
                        self._control_source_label(1, getattr(p1_action, "macro_action", None)),
                        self._control_source_label(2, getattr(p2_action, "macro_action", None)),
                        state.p1_health, state.p2_health, state.timer,
                    )

                # 5. Check round status (grace period set above)
                if step_count > ROUND_OVER_GRACE_STEPS:
                    raw_round_done = is_round_over(state)
                else:
                    raw_round_done = False

                sample_flags = self._sample_flags(state)
                raw_round_over_reason = self._round_over_reason(state, raw_round_done)
                round_done, winner_p1, round_over_reason, ko_streaks = apply_round_end_policy(
                    state,
                    sample_flags=sample_flags,
                    round_done=raw_round_done,
                    round_over_reason=raw_round_over_reason,
                    ko_streaks=ko_streaks,
                )

                # 5. Update snapshot and broadcast game state
                self.latest_snapshot = GameSnapshot(
                    p1_health=state.p1_health,
                    p2_health=state.p2_health,
                    timer=state.timer,
                    p1_x=state.p1_x,
                    p2_x=state.p2_x,
                    frame_id=step_count,
                    round_over=round_done,
                    p1_won=winner_p1,
                    current_round=self.current_round,
                    rounds_won_p1=self.rounds_won_p1,
                    rounds_won_p2=self.rounds_won_p2,
                    best_of=self.best_of,
                    # Combat signals from training update
                    p1_action=state.p1_action,
                    p2_action=state.p2_action,
                    p1_y_vel=state.p1_y_vel,
                    p1_hitstun=state.p1_hitstun,
                    p2_hitstun=state.p2_hitstun,
                    p1_airborne=state.p1_airborne,
                    p2_airborne=state.p2_airborne,
                    debug=self._build_snapshot_debug(
                        state,
                        sample_flags=sample_flags,
                        raw_round_done=raw_round_done,
                        raw_round_over_reason=raw_round_over_reason,
                        round_done=round_done,
                        round_over_reason=round_over_reason,
                        ko_streaks=ko_streaks,
                    ),
                )
                self._ram_debug.record(
                    {
                        "kind": "sample",
                        "round": self.current_round,
                        "step": step_count,
                        "agent_interval_sec": agent_interval_sec,
                        "state_source": state.debug_info.get("state_source"),
                        "frame_id": state.frame_id,
                        "decoded": {
                            "p1_health": state.p1_health,
                            "p2_health": state.p2_health,
                            "timer": state.timer,
                            "p1_x": state.p1_x,
                            "p2_x": state.p2_x,
                            "p1_airborne": state.p1_airborne,
                            "p2_airborne": state.p2_airborne,
                            "p1_y_vel": state.p1_y_vel,
                        },
                        "actions": {
                            "p1": self._control_source_label(1, getattr(p1_action, "macro_action", None)),
                            "p2": self._control_source_label(2, getattr(p2_action, "macro_action", None)),
                        },
                        "round_done": round_done,
                        "winner_p1": winner_p1,
                        "round_over_reason": round_over_reason,
                        "raw_round_done": raw_round_done,
                        "raw_round_over_reason": raw_round_over_reason,
                        "ko_streaks": dict(ko_streaks),
                        "ko_confirm_frames": KO_CONFIRM_FRAMES,
                        "sample_flags": sample_flags,
                        "logic_trusted": not sample_flags,
                        "debug_info": state.debug_info,
                    }
                )

                # Throttle game state broadcast to 5Hz — clients only need
                # ~3-5 updates/sec for smooth health bar UI.
                _now = time.monotonic()
                if _now - self._last_state_broadcast >= 0.2:
                    await ws_manager.broadcast_json(
                        self.match_id,
                        self.latest_snapshot.to_dict(),
                    )
                    self._last_state_broadcast = _now

                # Cache latest state in Redis for late joiners
                try:
                    from app.services.redis_client import cache_game_state
                    await cache_game_state(self.match_id, self.latest_snapshot.to_dict())
                except Exception:
                    pass

                if round_done:
                    self.state = RunnerState.ROUND_OVER
                    logger.info(
                        "Round %d over! P1 %s (hp=%d vs %d)",
                        self.current_round,
                        "WON" if winner_p1 else "LOST",
                        state.p1_health, state.p2_health,
                    )
                    break

                consecutive_errors = 0

                elapsed = time.monotonic() - step_start
                sleep_time = agent_interval_sec - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in agent brain loop")
                consecutive_errors += 1
                if consecutive_errors >= 10:
                    logger.error("Agent brain: %d consecutive errors — aborting", consecutive_errors)
                    self.state = RunnerState.ERROR
                    # ✅ FIX: Update DB to reflect runner failure
                    await self._mark_match_errored()
                    break
                await asyncio.sleep(0.5)

        write_ctrl(neutral, ctrl_p1)
        write_ctrl(neutral, ctrl_p2)
        return winner_p1

    async def _auto_settle(self, winner_player: int) -> None:
        """Auto-settle the match after the final round."""
        try:
            from app.services.settlement import settle_match
            await settle_match(self.match_id, winner_player)
            logger.info("Successfully settled match %s with winner player %d", self.match_id, winner_player)
        except Exception as e:
            logger.exception("Auto-settle failed for match %s: %s", self.match_id, str(e))
            # Even if settlement fails, we should still mark the match as completed
            # to prevent it from being stuck in LIVE status
            await self._mark_match_completed_fallback(winner_player)

    async def _mark_match_completed_fallback(self, winner_player: int) -> None:
        """Fallback to mark match as completed in DB when settlement fails."""
        try:
            from datetime import datetime, timezone
            from uuid import UUID
            from app.db.engine import async_session
            from app.db.models import Match, MatchStatus, StreamStatus
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            async with async_session() as db:
                result = await db.execute(
                    select(Match)
                    .where(Match.id == UUID(self.match_id))
                    .options(selectinload(Match.stream))
                )
                match = result.scalar_one_or_none()
                if not match:
                    logger.error("Fallback: match %s not found", self.match_id)
                    return

                if match.status != MatchStatus.LIVE:
                    logger.warning("Fallback: match %s not LIVE (status=%s), skipping",
                                 self.match_id, match.status.value)
                    return

                winner_id = match.fighter1_id if winner_player == 1 else match.fighter2_id
                match.status = MatchStatus.COMPLETED
                match.winner_id = winner_id
                match.completed_at = datetime.now(timezone.utc)
                if match.stream:
                    match.stream.status = StreamStatus.STOPPED

                await db.commit()
                logger.warning("Fallback: Marked match %s as COMPLETED (winner=%s) without full settlement",
                             self.match_id, winner_id)
        except Exception:
            logger.exception("Fallback failed to mark match %s as completed", self.match_id)

    async def _mark_match_errored(self) -> None:
        """Cancel the match contract-first when runner fails."""
        try:
            from app.db.models import StreamStatus
            from app.services.match_cancel import cancel_match_by_id_contract_first

            result = await cancel_match_by_id_contract_first(
                self.match_id,
                stream_status=StreamStatus.ERROR,
                reason="runner_failure",
            )
            if result is not None:
                logger.info(
                    "Marked match %s as CANCELLED due to runner failure (on_chain_tx=%s)",
                    self.match_id,
                    result.on_chain_tx,
                )
        except Exception:
            logger.exception("Failed to mark match %s as errored in DB", self.match_id)

    async def _cleanup_emulator(self) -> None:
        """Clean up emulator resources after match ends."""
        # Stop combined HLS capture if still running
        await self._hls_capture.stop()
        if self._bridge:
            try:
                self._bridge.close()
            except Exception:
                pass
            self._bridge = None
        if self._session:
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None
        # Remove from global registry
        _active_runners.pop(self.match_id, None)

    def _sample_flags(self, state: FightState) -> list[str]:
        flags: list[str] = []
        if state.timer < 0 or state.timer > 99:
            flags.append("timer_out_of_range")
        if state.timer == 0 and state.p1_health > 0 and state.p2_health > 0:
            flags.append("timer_zero_while_both_alive")
        if state.p1_health < 0 or state.p1_health > 160:
            flags.append("p1_health_out_of_range")
        if state.p2_health < 0 or state.p2_health > 160:
            flags.append("p2_health_out_of_range")
        if state.debug_info.get("state_source") == "fallback":
            flags.append("fallback_state")
        return flags

    def _round_over_reason(self, state: FightState, round_done: bool) -> str | None:
        if not round_done:
            return None
        if state.p1_health <= 0 and state.p2_health > 0:
            return "p1_ko"
        if state.p2_health <= 0 and state.p1_health > 0:
            return "p2_ko"
        if state.timer == 0:
            return "timer_zero"
        if state.p1_health <= 0 and state.p2_health <= 0:
            return "double_ko"
        return "unknown"


# ── Global registry of active match runners ──

_active_runners: dict[str, MatchRunner] = {}


def get_runner(match_id: str) -> MatchRunner | None:
    return _active_runners.get(match_id)


def get_all_runners() -> dict[str, MatchRunner]:
    return dict(_active_runners)


async def start_match(
    match_id: str,
    savestate_path: str,
    p1_agent_id: str = "random",
    p2_agent_id: str = "random",
    p1_checkpoint_path: str | None = None,
    p2_checkpoint_path: str | None = None,
    p1_architecture: str | None = None,
    p2_architecture: str | None = None,
    best_of: int = 3,
) -> MatchRunner:
    """Create and start a match runner. Returns the runner.

    Args:
        match_id: Unique identifier for the match
        savestate_path: Path to the MK4 savestate file
        p1_agent_id: Agent identifier for player 1 (e.g., "random", "custom_my_agent")
        p2_agent_id: Agent identifier for player 2
        p1_checkpoint_path: Path to ONNX checkpoint for P1 custom agent
        p2_checkpoint_path: Path to ONNX checkpoint for P2 custom agent
        p1_architecture: Architecture type for P1 (e.g., "lstm", "disc_rssm") - required for custom agents
        p2_architecture: Architecture type for P2 (e.g., "lstm", "disc_rssm") - required for custom agents
        best_of: Number of rounds (best of N)
    """
    if match_id in _active_runners:
        raise RuntimeError(f"Match {match_id} already has an active runner")

    # ✅ FIX: Pass checkpoint paths AND architecture to create_agent for custom uploaded agents
    p1_agent = create_agent(p1_agent_id, checkpoint_path=p1_checkpoint_path, architecture=p1_architecture)
    p2_agent = create_agent(p2_agent_id, checkpoint_path=p2_checkpoint_path, architecture=p2_architecture)

    runner = MatchRunner(
        match_id=match_id,
        savestate_path=savestate_path,
        p1_agent=p1_agent,
        p2_agent=p2_agent,
        best_of=best_of,
    )

    # ✅ CRITICAL FIX: Only add to registry AFTER successful start
    # Previously: added before start(), so startup failures left zombie runners
    try:
        await runner.start()
        _active_runners[match_id] = runner  # Only register if startup succeeded
        return runner
    except Exception:
        # Startup failed - ensure runner is NOT in registry
        _active_runners.pop(match_id, None)
        # Clean up any partial resources
        await runner._cleanup_emulator()
        raise


async def stop_match(match_id: str) -> None:
    """Stop a running match."""
    runner = _active_runners.pop(match_id, None)
    if runner:
        await runner.stop()
