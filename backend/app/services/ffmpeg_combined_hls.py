"""
Combined H.264+AAC HLS capture for live match streaming.

One FFmpeg process captures:
  - Video: x11grab from Xvfb display :99 @ 640x480 30fps  →  libx264 ultrafast
  - Audio: PulseAudio null-sink monitor (game audio)       →  AAC-LC 128k

Output: HLS segments at /tmp/hls/{match_id}/ with 1-second segments.
Flutter's VideoPlayer (ExoPlayer on Android) plays the m3u8 directly —
video and audio are always in sync because they're in the same container.

Mobile robustness notes:
- Keep a wider live segment window to tolerate network jitter.
- Avoid aggressive segment deletion while live to reduce transient 404s.
- Mark stream "ready" only once real .ts media files exist and are non-empty.
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
#
# Capture at 480x360 (scaled from 640x480 in FFmpeg with -vf scale=) to produce
# smaller segments for faster mobile startup. Quality is intentionally traded for
# lower bitrate and faster segment download time.
CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
CAPTURE_FPS = 24  # 24fps — lower than 30 to reduce mobile decoder pipeline pressure

# Output resolution — scale down in encoder to shrink segment size ~60%
OUTPUT_WIDTH = 480
OUTPUT_HEIGHT = 360

# HLS tuning — 1-second segments with a wide live window for mobile resilience.
# 30 segments = 30-second buffer. This prevents BehindLiveWindowException on
# devices that decode slowly and fall behind the live edge.
HLS_SEGMENT_DURATION = 1
HLS_LIST_SIZE = 30
READY_MIN_SEGMENTS = 1   # mark ready after first segment — don't wait for two
READY_MIN_BYTES = 188  # One MPEG-TS packet

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
            # Generate PTS when an input packet lacks timestamps.
            "-fflags", "+genpts",
            # ── Video input: Xvfb virtual display ──────────────────────────
            "-thread_queue_size", "2048",
            "-use_wallclock_as_timestamps", "1",
            "-f", "x11grab",
            "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
            "-framerate", str(CAPTURE_FPS),
            "-i", XVFB_DISPLAY,
            # ── Audio input: PulseAudio null-sink monitor ───────────────────
            "-thread_queue_size", "2048",
            "-use_wallclock_as_timestamps", "1",
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # Explicit mapping and timestamp normalization:
            # - reset both streams to a common zero origin
            # - continuously correct tiny audio clock drift vs video timeline
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", "setpts=PTS-STARTPTS",
            "-af", "aresample=async=1000:min_hard_comp=0.100:first_pts=0",
            # ── Video encoding: H.264 ultrafast / zero-latency ──────────────
            "-fps_mode", "cfr",
            "-r", str(CAPTURE_FPS),      # stable output cadence for HLS
            "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},setpts=PTS-STARTPTS",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",   # widest device compat (no B-frames)
            "-level", "3.1",
            "-g", str(CAPTURE_FPS),     # keyframe every 1s = one per segment
            "-keyint_min", str(CAPTURE_FPS),
            "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{HLS_SEGMENT_DURATION})",
            "-b:v", "400k",
            "-maxrate", "500k",
            "-bufsize", "800k",
            "-pix_fmt", "yuv420p",       # required by baseline profile
            # ── Audio encoding: AAC-LC ──────────────────────────────────────
            "-c:a", "aac",
            "-profile:a", "aac_low",    # LC profile — universally supported
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            # ── MPEG-TS muxer settings  ─────────────────────────────────────
            "-mpegts_flags", "resend_headers",  # PAT/PMT at segment start
            "-max_interleave_delta", "1000000",
            "-muxpreload", "0",
            "-muxdelay", "0",
            "-avoid_negative_ts", "make_zero",
            "-muxrate", "0",
            "-pcr_period", "20",
            # ── HLS output ──────────────────────────────────────────────────
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_DURATION),
            "-hls_list_size", str(HLS_LIST_SIZE),
            # Keep recent segments on disk while live. This avoids clients
            # briefly requesting a just-rotated segment and receiving JSON 404
            # payloads that confuse some mobile decoders.
            "-hls_flags", "append_list+discont_start+independent_segments+temp_file",
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

        # Always start from a clean directory to avoid stale playlist/segments
        # when a previous capture crashed or was interrupted.
        try:
            if self._dir.exists():
                shutil.rmtree(self._dir, ignore_errors=True)
        except Exception:
            pass
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

    def _segment_paths_from_playlist(self) -> list[Path]:
        try:
            if not self._playlist.exists():
                return []
            lines = self._playlist.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return []
        paths: list[Path] = []
        for line in lines:
            seg = line.strip()
            if not seg or seg.startswith("#") or not seg.endswith(".ts"):
                continue
            paths.append(self._dir / seg)
        return paths

    def ready_for_playback(self, min_segments: int = READY_MIN_SEGMENTS) -> bool:
        """True once the playlist and enough non-empty media segments exist."""
        segs = self._segment_paths_from_playlist()
        if len(segs) < min_segments:
            return False
        for seg in segs[-min_segments:]:
            try:
                if not seg.exists() or seg.stat().st_size < READY_MIN_BYTES:
                    return False
            except Exception:
                return False
        return True

    @property
    def playlist_ready(self) -> bool:
        """Backward-compatible readiness check."""
        return self.ready_for_playback(min_segments=1)
