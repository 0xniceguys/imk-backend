"""
FFmpeg-based audio capture for live match audio streaming.

Captures from the PulseAudio null-sink monitor (where mupen64plus sends audio)
and writes HLS segments (.ts + .m3u8) to /tmp/hls/{match_id}/.

The HLS playlist is served by the backend so both the admin viewer
and the Flutter app (video_player) can play it.

Segments are 1s each (down from 2s) with 3 segments kept — approximately
2–3s of end-to-end latency, down from the previous 4–6s.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# PulseAudio source that monitors the null-sink output
PULSE_MONITOR_SOURCE = "auto_null.monitor"

# Where HLS segments are written per match
HLS_BASE_DIR = Path("/tmp/hls")

# HLS tuning — 1s segments for lower latency
HLS_SEGMENT_DURATION = 1   # seconds per .ts segment
HLS_LIST_SIZE = 3          # keep last N segments in the playlist


def hls_dir(match_id: str) -> Path:
    return HLS_BASE_DIR / match_id


def hls_playlist_path(match_id: str) -> Path:
    return hls_dir(match_id) / "stream.m3u8"


class FFmpegAudioCapture:
    """Captures emulator audio via PulseAudio → HLS segments.

    Usage::

        capture = FFmpegAudioCapture(match_id="abc123")
        await capture.start()
        # ... match runs ...
        await capture.stop()
    """

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self._dir = hls_dir(match_id)
        self._playlist = hls_playlist_path(match_id)
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._running = False

    def _build_cmd(self) -> list[str]:
        out_pattern = str(self._dir / "seg%05d.ts")
        playlist = str(self._playlist)
        return [
            "ffmpeg", "-y",
            # PulseAudio input — game audio arrives at 44100Hz from the null-sink
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # AAC audio — use strict baseline profile for max ExoPlayer compat
            "-c:a", "aac",
            "-profile:a", "aac_low",  # LC profile — universally supported
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            # MPEG-TS muxer flags to produce clean PES packets:
            #   resend_headers  — resend PAT/PMT at each segment start
            #   latm            — NOT used (causes PesReader confusion on Android)
            "-mpegts_flags", "resend_headers",
            "-muxrate", "0",         # CBR off — let muxer adapt to audio bitrate
            "-pcr_period", "20",     # PCR every 20ms — reduces ExoPlayer pipeline stalls
            # HLS output — 1s segments for lower latency
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_DURATION),
            "-hls_list_size", str(HLS_LIST_SIZE),
            "-hls_flags", "delete_segments+append_list+discont_start",
            "-hls_segment_type", "mpegts",   # explicit — avoids any fmp4 fallback
            "-hls_segment_filename", out_pattern,
            playlist,
        ]

    async def start(self) -> None:
        if self._running:
            return
        if not shutil.which("ffmpeg"):
            logger.warning("FFmpeg not found — audio capture disabled")
            return

        self._dir.mkdir(parents=True, exist_ok=True)
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
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._running = True
            self._stderr_task = asyncio.create_task(
                self._log_stderr(), name=f"audio-stderr-{self.match_id}"
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

        # Clean up HLS segment files
        try:
            if self._dir.exists():
                shutil.rmtree(self._dir, ignore_errors=True)
        except Exception:
            pass

        logger.info("Audio capture stopped for match %s", self.match_id)

    @property
    def playlist_ready(self) -> bool:
        """True once the HLS playlist file exists (first segment written)."""
        return self._playlist.exists()
