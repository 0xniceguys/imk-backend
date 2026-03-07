"""
Combined H.264+AAC HLS capture for live match streaming.

One FFmpeg process captures:
  - Video: x11grab from Xvfb display :99 @ 320x240 30fps  →  libx264 ultrafast
  - Audio: PulseAudio null-sink monitor (game audio)       →  AAC-LC 128k

Output: HLS segments at /tmp/hls/{match_id}/ with 1-second segments.
Flutter's VideoPlayer (ExoPlayer on Android) plays the m3u8 directly —
video and audio are always in sync because they're in the same container.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# PulseAudio source for game audio output
PULSE_MONITOR_SOURCE = "auto_null.monitor"

# Xvfb display the emulator renders to
XVFB_DISPLAY = ":99"

# Video capture dimensions (must match Xvfb and emulator resolution)
CAPTURE_WIDTH = 320
CAPTURE_HEIGHT = 240
CAPTURE_FPS = 30

# HLS tuning — 1-second segments for ~2–3s end-to-end latency
HLS_SEGMENT_DURATION = 1
HLS_LIST_SIZE = 3

# Where HLS output is written per match
_HLS_BASE = Path("/tmp/hls")


def hls_dir(match_id: str) -> Path:
    return _HLS_BASE / match_id


def hls_playlist_path(match_id: str) -> Path:
    return hls_dir(match_id) / "stream.m3u8"


class FFmpegCombinedHls:
    """Single FFmpeg process: x11grab + PulseAudio → H.264+AAC HLS.

    Usage::

        cap = FFmpegCombinedHls(match_id="abc123")
        await cap.start()
        # ... match runs ...
        await cap.stop()
    """

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self._dir = hls_dir(match_id)
        self._playlist = hls_playlist_path(match_id)
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._running = False

    def _build_cmd(self) -> list[str]:
        playlist = str(self._playlist)
        seg_pattern = str(self._dir / "seg%05d.ts")
        return [
            "ffmpeg", "-y",
            # ── Video input: Xvfb virtual display ──────────────────────────
            "-f", "x11grab",
            "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
            "-framerate", str(CAPTURE_FPS),
            "-i", XVFB_DISPLAY,
            # ── Audio input: PulseAudio null-sink monitor ───────────────────
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # ── Video encoding: H.264 ultrafast / zero-latency ──────────────
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",   # widest device compat (no B-frames)
            "-level", "3.1",
            "-g", str(CAPTURE_FPS),     # keyframe every 1s = one per segment
            "-b:v", "1000k",
            "-pix_fmt", "yuv420p",       # required by baseline profile
            # ── Audio encoding: AAC-LC ──────────────────────────────────────
            "-c:a", "aac",
            "-profile:a", "aac_low",    # LC profile — universally supported
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            # ── MPEG-TS muxer settings  ─────────────────────────────────────
            "-mpegts_flags", "resend_headers",  # PAT/PMT at segment start
            "-muxrate", "0",
            "-pcr_period", "20",
            # ── HLS output ──────────────────────────────────────────────────
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_DURATION),
            "-hls_list_size", str(HLS_LIST_SIZE),
            "-hls_flags", "delete_segments+append_list+discont_start",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", seg_pattern,
            playlist,
        ]

    async def start(self) -> None:
        if self._running:
            return
        if not shutil.which("ffmpeg"):
            logger.warning("FFmpeg not found — HLS capture disabled")
            return

        self._dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_cmd()
        logger.info("Starting combined HLS capture for match %s", self.match_id)

        try:
            env = dict(os.environ)
            uid = os.getuid()
            env.setdefault("PULSE_SERVER", f"unix:/run/user/{uid}/pulse/native")
            env.setdefault("DISPLAY", XVFB_DISPLAY)

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._running = True
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(), name=f"hls-stderr-{self.match_id}"
            )
            logger.info(
                "Combined HLS capture started (pid=%s) for match %s",
                self._process.pid, self.match_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to start combined HLS capture for match %s: %s",
                self.match_id, exc,
            )

    async def _drain_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        try:
            async for line in self._process.stderr:
                txt = line.decode(errors="replace").rstrip()
                if txt and self._running:
                    logger.debug("FFmpegHLS[%s]: %s", self.match_id, txt)
        except Exception:
            pass

    async def stop(self) -> None:
        self._running = False

        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
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

        # Remove HLS segment files for this match
        try:
            if self._dir.exists():
                shutil.rmtree(self._dir, ignore_errors=True)
        except Exception:
            pass

        logger.info("Combined HLS capture stopped for match %s", self.match_id)

    @property
    def playlist_ready(self) -> bool:
        """True once the first HLS segment has been written."""
        return self._playlist.exists()
