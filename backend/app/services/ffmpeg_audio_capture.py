"""
FFmpeg-based audio capture for live match audio streaming.

Captures from the PulseAudio null-sink monitor (where mupen64plus sends audio)
and pipes raw Opus-in-OGG chunks to a callback — one call per chunk (~20ms).

The caller (MatchRunner) passes each chunk to the WebSocket connection manager
so it is broadcast over the same WS connection as video frames, using a 0x01
message-type prefix byte. This eliminates HLS latency and keeps audio/video
naturally in sync.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# PulseAudio source that monitors the null-sink output
PULSE_MONITOR_SOURCE = "auto_null.monitor"

# Opus chunk size fed to the callback.
# FFmpeg writes OGG pages; we read in 4 KB chunks and pass them straight through.
# The client (browser Web Audio API / Flutter) reassembles OGG pages itself.
_READ_SIZE = 4096


class FFmpegAudioCapture:
    """Captures emulator audio via PulseAudio → Opus/OGG pipe → callback.

    Usage::

        capture = FFmpegAudioCapture(match_id="abc123")
        await capture.start(on_audio_chunk)
        # ... match runs ...
        await capture.stop()

    The ``on_audio_chunk`` callback receives raw bytes (OGG page chunks) that
    can be forwarded directly to WebSocket clients.
    """

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._running = False

    def _build_cmd(self) -> list[str]:
        return [
            "ffmpeg", "-y",
            # PulseAudio input
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # Opus codec in OGG container, piped to stdout
            "-c:a", "libopus",
            "-b:a", "64k",
            "-ar", "48000",
            "-ac", "2",
            "-f", "ogg",
            "pipe:1",
        ]

    async def start(
        self, on_audio_chunk: Callable[[bytes], Awaitable[None]]
    ) -> None:
        if self._running:
            return
        if not shutil.which("ffmpeg"):
            logger.warning("FFmpeg not found — audio capture disabled")
            return

        cmd = self._build_cmd()
        logger.info(
            "Starting audio capture for match %s: %s",
            self.match_id, " ".join(cmd),
        )

        try:
            env = dict(os.environ)
            uid = os.getuid()
            env.setdefault(
                "PULSE_SERVER",
                f"unix:/run/user/{uid}/pulse/native",
            )

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._running = True

            self._stderr_task = asyncio.create_task(
                self._log_stderr(), name=f"audio-stderr-{self.match_id}"
            )
            self._reader_task = asyncio.create_task(
                self._read_chunks(on_audio_chunk),
                name=f"audio-reader-{self.match_id}",
            )
            logger.info(
                "Audio capture started (pid=%s) for match %s",
                self._process.pid, self.match_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to start audio capture for match %s: %s",
                self.match_id, exc,
            )

    async def _read_chunks(
        self, on_audio_chunk: Callable[[bytes], Awaitable[None]]
    ) -> None:
        """Read OGG/Opus chunks from FFmpeg stdout and fire the callback."""
        stdout = self._process.stdout  # type: ignore[union-attr]
        if stdout is None:
            return

        chunks_sent = 0
        try:
            while self._running:
                chunk = await asyncio.wait_for(
                    stdout.read(_READ_SIZE), timeout=5.0
                )
                if not chunk:
                    logger.warning(
                        "Audio FFmpeg stdout closed (chunks_sent=%d)", chunks_sent
                    )
                    break
                try:
                    await on_audio_chunk(chunk)
                    chunks_sent += 1
                except Exception:
                    pass
                # Yield back to event loop
                await asyncio.sleep(0)

        except asyncio.TimeoutError:
            logger.error(
                "Audio FFmpeg: no data for 5s (match=%s) — PulseAudio may be unavailable",
                self.match_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Audio chunk reader error (match=%s)", self.match_id)
        finally:
            self._running = False

    async def _log_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        try:
            async for line in self._process.stderr:
                txt = line.decode(errors="replace").rstrip()
                if txt and self._running:
                    logger.warning("FFmpegAudio[%s]: %s", self.match_id, txt)
        except Exception:
            pass

    async def stop(self) -> None:
        self._running = False

        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._reader_task = None
        self._stderr_task = None

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        logger.info("Audio capture stopped for match %s", self.match_id)
