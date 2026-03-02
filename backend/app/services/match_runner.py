"""
Match Runner — orchestrates a single live match.

Architecture (decoupled loops):
  Loop A (agent brain, ~10 Hz):
    Read game state from RAM → agent decides → write controller mmap
  Loop B (frame delivery, 60 Hz):
    FFmpeg captures display → MJPEG pipe → JPEG frames → WebSocket broadcast

Platform-aware:
  - Linux (EC2):  FFmpeg x11grab captures Xvfb virtual display
  - macOS (dev):  FFmpeg avfoundation captures the emulator window

The emulator always runs freely at native speed. No frame-stepping.

Self-contained: ZERO imports from the training package.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from app.agents import create_agent
from app.agents.base import FighterAgent
from app.services.actions import (
    ControllerState,
    resolve_action,
)
from app.services.bridge import EmulatorBridge
from app.services.ctrl_writer import write_ctrl
from app.services.emulator import EmulatorSession, LaunchOptions
from app.services.ffmpeg_capture import FFmpegCapture, ffmpeg_available, is_linux
from app.services.game_state import (
    FightState,
    is_round_over,
    p1_won,
    read_fight_state,
)
from app.ws.connection_manager import manager as ws_manager

logger = logging.getLogger(__name__)

class RunnerState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    ROUND_OVER = "round_over"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


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
        }


class MatchRunner:
    """
    Manages the lifecycle of a single emulator match.

    Supports multi-round (best-of-N) matches. After each round KO,
    reloads the savestate and continues until a player wins enough rounds.

    The emulator runs freely at native speed on all platforms.
    FFmpeg captures the display at 60fps (x11grab on Linux, avfoundation on macOS).
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
        self._frame_capture: FFmpegCapture | None = None

    @property
    def rounds_to_win(self) -> int:
        return (self.best_of // 2) + 1

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

        try:
            await self._launch_emulator()
            await self._connect_bridge()
            await self._load_savestate()

            # Reset agents
            self.p1_agent.reset()
            self.p2_agent.reset()

            self.state = RunnerState.RUNNING

            # Let emulator run freely, FFmpeg captures display at 60fps
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
        """Let the emulator run freely and start FFmpeg frame capture.

        Linux:  captures Xvfb display via x11grab
        macOS:  captures primary screen via avfoundation (device index "2")
        """
        loop = asyncio.get_running_loop()
        # Tell emulator to run (no longer paused/frame-stepping)
        await loop.run_in_executor(
            None, self._bridge.debugger_command, "run"
        )
        logger.info("Emulator set to free-running mode")

        # Pick the right FFmpeg input based on platform
        if is_linux():
            display = self._session.display if self._session else ":99"
            self._frame_capture = FFmpegCapture(
                display=display,
                width=320,
                height=240,
                framerate=60,
                quality=5,
            )
            logger.info("FFmpeg capture: x11grab on display %s", display)
        else:
            # macOS: avfoundation screen capture
            # Screen index 2 = "Capture screen 0" (primary display)
            self._frame_capture = FFmpegCapture(
                screen_index="2",
                width=320,
                height=240,
                framerate=60,
                quality=5,
            )
            logger.info("FFmpeg capture: avfoundation screen 0 (macOS)")

        await self._frame_capture.start(self._on_ffmpeg_frame)
        logger.info("FFmpeg capture started at 60fps")

    async def _on_ffmpeg_frame(self, jpeg_bytes: bytes) -> None:
        """Callback: FFmpeg delivered a JPEG frame — broadcast it."""
        self.latest_frame = jpeg_bytes
        await ws_manager.broadcast_bytes(self.match_id, jpeg_bytes)

    async def stop(self) -> None:
        """Stop the match, kill emulator."""
        logger.info("Stopping match runner %s", self.match_id)
        self.state = RunnerState.STOPPED

        # Stop FFmpeg capture
        if self._frame_capture:
            await self._frame_capture.stop()
            self._frame_capture = None

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
        await ws_manager.broadcast_json(self.match_id, {
            "type": "match_ended",
            "match_id": self.match_id,
        })

    async def _launch_emulator(self) -> None:
        """Launch the bridge server (which manages mupen64plus internally)."""
        opts = LaunchOptions(
            instance_id=self.instance_id,
            resolution="320x240",
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
        result = await loop.run_in_executor(
            None,
            self._bridge.load_savestate,
            self.savestate_path,
        )
        logger.info("Savestate loaded: %s → %s", self.savestate_path, result)

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

                if match_over:
                    winner_player = 1 if self.rounds_won_p1 >= self.rounds_to_win else 2
                    logger.info(
                        "Match over! P%d wins (%d-%d)",
                        winner_player, self.rounds_won_p1, self.rounds_won_p2,
                    )
                    self.state = RunnerState.COMPLETED

                    await ws_manager.broadcast_json(self.match_id, {
                        "type": "match_ended",
                        "match_id": self.match_id,
                        "winner_player": winner_player,
                        "rounds_won_p1": self.rounds_won_p1,
                        "rounds_won_p2": self.rounds_won_p2,
                    })

                    await self._auto_settle(winner_player)
                    break

                # More rounds to play — wait for KO animation, reload savestate
                self.current_round += 1
                logger.info("Between rounds — reloading savestate for round %d", self.current_round)

                # Pause emulator + stop FFmpeg for savestate reload
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, self._bridge.debugger_command, "pause"
                )
                if self._frame_capture:
                    await self._frame_capture.stop()
                    self._frame_capture = None

                await asyncio.sleep(3.0)  # KO animation + pause

                await self._load_savestate()
                self.p1_agent.reset()
                self.p2_agent.reset()
                self.state = RunnerState.RUNNING

                # Resume free-running + FFmpeg
                await self._start_free_running()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in match loop")
            self.state = RunnerState.ERROR

        # Cleanup emulator
        await self._cleanup_emulator()

    async def _round_loop(self) -> bool:
        """Single round: agent brain loop at ~10Hz.

        The emulator runs freely at native speed.
        FFmpeg captures frames independently at 60fps.
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

        # Agent brain runs at ~10Hz — reads RAM, decides, writes inputs
        agent_interval_sec = 1.0 / 10.0  # 100ms between decisions

        while self.state == RunnerState.RUNNING:
            step_start = time.monotonic()
            try:
                # 1. Read game state from RAM
                state: FightState = await loop.run_in_executor(
                    None, read_fight_state, self._bridge, step_count
                )

                # 2. Agent decisions
                p1_action = self.p1_agent.choose_action(state, player=1)
                p2_action = self.p2_agent.choose_action(state, player=2)

                # 3. Write controller states to mmap
                p1_resolved = resolve_action(p1_action)
                p2_resolved = resolve_action(p2_action)
                write_ctrl(p1_resolved.micro_controller_state, ctrl_p1)
                write_ctrl(p2_resolved.micro_controller_state, ctrl_p2)

                step_count += 1

                # Debug log every 30 steps (~3s)
                if step_count % 30 == 0:
                    logger.info(
                        "R%d Brain step %d: P1=%s P2=%s | HP %d-%d T=%d",
                        self.current_round, step_count,
                        p1_resolved.macro_action, p2_resolved.macro_action,
                        state.p1_health, state.p2_health, state.timer,
                    )

                # 4. Check round status
                round_done = is_round_over(state)
                winner_p1 = p1_won(state) if round_done else False

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
                )

                await ws_manager.broadcast_json(
                    self.match_id,
                    self.latest_snapshot.to_dict(),
                )

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

                # Pace the agent brain at ~10Hz
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
        except Exception:
            logger.exception("Auto-settle failed for match %s", self.match_id)

    async def _cleanup_emulator(self) -> None:
        """Clean up emulator resources after match ends."""
        if self._frame_capture:
            await self._frame_capture.stop()
            self._frame_capture = None
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
    best_of: int = 3,
) -> MatchRunner:
    """Create and start a match runner. Returns the runner."""
    if match_id in _active_runners:
        raise RuntimeError(f"Match {match_id} already has an active runner")

    # Guard: only one concurrent match until per-instance mmap propagation is fixed.
    if _active_runners:
        raise RuntimeError(
            "Only one concurrent match is supported until per-instance mmap paths are "
            "propagated to the mupen64plus grandchild process. Stop the running match first."
        )

    p1_agent = create_agent(p1_agent_id)
    p2_agent = create_agent(p2_agent_id)

    runner = MatchRunner(
        match_id=match_id,
        savestate_path=savestate_path,
        p1_agent=p1_agent,
        p2_agent=p2_agent,
        best_of=best_of,
    )
    _active_runners[match_id] = runner
    await runner.start()
    return runner


async def stop_match(match_id: str) -> None:
    """Stop a running match."""
    runner = _active_runners.pop(match_id, None)
    if runner:
        await runner.stop()
