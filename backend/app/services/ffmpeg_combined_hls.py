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
import time
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
OUTPUT_HEIGHT = 368   # multiple of 16 → hardware decoder uses this anyway; set explicitly
                      # to avoid the codec padding step (360 → 368) inside ExoPlayer.

# HLS tuning — 1-second segments with a wide live window for mobile resilience.
# 30 segments = 30-second buffer. This prevents BehindLiveWindowException on
# devices that decode slowly and fall behind the live edge.
HLS_SEGMENT_DURATION = 0.5   # 0.5s segments — smaller chunks means faster recovery
                             # from any ExoPlayer buffering event (less data to discard)
HLS_LIST_SIZE = 60           # 60 × 0.5s = 30s live window (same wall-clock buffer as before)
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
            "-thread_queue_size", "4096",
            # DO NOT use -use_wallclock_as_timestamps here: it introduces
            # microsecond-level wall-clock jitter that misaligns video PTS
            # with audio PTS at the muxer level, producing false 0x000001
            # sequences in PES packets (Unexpected start code prefix errors).
            # x11grab's native counter-based PTS is perfectly monotonic.
            "-f", "x11grab",
            "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
            "-framerate", str(CAPTURE_FPS),
            "-i", XVFB_DISPLAY,
            # ── Audio input: PulseAudio null-sink monitor ───────────────────
            "-thread_queue_size", "4096",
            # DO NOT use -use_wallclock_as_timestamps here: pulse native PTS
            # is sample-count-based (perfectly regular at 1024/44100 ≈ 23.2ms)
            # which is far more stable than wall clock readings.
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # Explicit mapping and timestamp normalization:
            # - reset both streams to a common zero origin
            # - continuously correct tiny audio clock drift vs video timeline
            "-map", "0:v:0",
            "-map", "1:a:0",
            # Single -vf: scale to output resolution and reset PTS origin.
            # (Two -vf flags in one command is ambiguous — combined here.)
            "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},setpts=PTS-STARTPTS",
            # async=200: gentle clock drift correction.
            "-af", "aresample=async=200:min_hard_comp=0.100:first_pts=0",
            # ── Video encoding: H.264 ultrafast / zero-latency ──────────────
            "-fps_mode", "cfr",
            "-r", str(CAPTURE_FPS),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-level", "3.1",
            # Keyframe every segment (CAPTURE_FPS × HLS_SEGMENT_DURATION frames).
            # This ensures every segment starts with an IDR frame, which is
            # required for correct HLS seeking and independent_segments.
            "-g", str(int(CAPTURE_FPS * HLS_SEGMENT_DURATION)),
            "-keyint_min", str(int(CAPTURE_FPS * HLS_SEGMENT_DURATION)),
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
            "-max_interleave_delta", "500000000",  # 500ms — enough room for bursty video/audio interleave
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
            "-hls_flags", "append_list+independent_segments+temp_file",
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
        """Drain FFmpeg stderr, parsing and surfacing diagnostics in real time.

        Categories logged at WARNING (always visible):
          • PES / start-code corruption   → likely muxer interleave issue
          • Input queue overflow          → x11grab can't keep up
          • Encoding errors               → codec/bitrate problem

        Categories logged at INFO (visible at normal log level):
          • Frame drops / duplicates      → emulator render stutter
          • Audio sync corrections        → aresample doing heavy work
          • Segment written               → confirms HLS output is flowing

        Everything else → DEBUG (suppressed in production).
        """
        if not self._process or not self._process.stderr:
            return

        import re
        _progress_re = re.compile(
            r"frame=\s*(\d+).*?fps=\s*([\d.]+).*?speed=\s*([\d.]+)x"
        )
        _dup_drop_re = re.compile(r"dup=\s*(\d+).*?drop=\s*(\d+)")

        # Throttle: emit an FPS/drop summary at most once per N progress lines.
        _progress_count = 0
        _PROGRESS_REPORT_EVERY = 10   # log summary every ~10 progress updates

        try:
            async for line in self._process.stderr:
                if not self._running:
                    break
                txt = line.decode(errors="replace").rstrip()
                if not txt:
                    continue

                lower = txt.lower()

                # ── High-priority warnings ────────────────────────────────────
                if any(kw in lower for kw in (
                    "start code", "unexpected", "pes header", "corrupt",
                    "invalid data", "invalid nal",
                )):
                    logger.warning(
                        "[FFmpeg|PES] %s | match=%s", txt, self.match_id
                    )

                elif any(kw in lower for kw in (
                    "queue overflow", "buffer queue", "overrun",
                    "thread message queue blocker",
                )):
                    logger.warning(
                        "[FFmpeg|QUEUE] %s | match=%s", txt, self.match_id
                    )

                elif "error" in lower or "fatal" in lower:
                    logger.warning(
                        "[FFmpeg|ERROR] %s | match=%s", txt, self.match_id
                    )

                # ── Frame drop / duplicate detection ─────────────────────────
                elif "dropping" in lower or "dup=" in lower or "drop=" in lower:
                    dd = _dup_drop_re.search(txt)
                    if dd:
                        dups  = int(dd.group(1))
                        drops = int(dd.group(2))
                        if drops > 0 or dups > 5:          # only log when non-trivial
                            logger.info(
                                "[FFmpeg|FRAMES] dup=%d drop=%d | match=%s | %s",
                                dups, drops, self.match_id, txt,
                            )
                    else:
                        logger.info(
                            "[FFmpeg|FRAMES] %s | match=%s", txt, self.match_id
                        )

                # ── Audio sync / aresample ────────────────────────────────────
                elif any(kw in lower for kw in (
                    "aresample", "async", "audio drift", "pts discontinuity",
                    "non monotonous", "dts", "out of order",
                )):
                    logger.info(
                        "[FFmpeg|AV-SYNC] %s | match=%s", txt, self.match_id
                    )

                # ── Progress line: periodic FPS / drop summary ────────────────
                elif "frame=" in lower and "fps=" in lower:
                    _progress_count += 1
                    if _progress_count % _PROGRESS_REPORT_EVERY == 0:
                        m = _progress_re.search(txt)
                        dd = _dup_drop_re.search(txt)
                        if m:
                            frame = m.group(1)
                            fps   = m.group(2)
                            speed = m.group(3)
                            dups  = int(dd.group(1)) if dd else 0
                            drops = int(dd.group(2)) if dd else 0
                            logger.info(
                                "[FFmpeg|PROGRESS] frame=%s fps=%s speed=%sx "
                                "dup=%d drop=%d | match=%s",
                                frame, fps, speed, dups, drops, self.match_id,
                            )

                # ── Segment written ───────────────────────────────────────────
                elif ".ts" in txt and ("open" in lower or "mux" in lower or "seg" in lower):
                    logger.debug(
                        "[FFmpeg|SEG] %s | match=%s", txt, self.match_id
                    )

                # ── Everything else: debug only ───────────────────────────────
                else:
                    logger.debug(
                        "[FFmpeg|DBG] %s | match=%s", txt, self.match_id
                    )

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

    def health_snapshot(self) -> dict:
        """Return a diagnostic snapshot of HLS capture health.

        Checks: FFmpeg process alive, latest segment age, segment count, playlist exists.
        """
        info: dict = {
            "running": self._running,
            "pid": self._process.pid if self._process else None,
            "process_alive": False,
            "playlist_exists": self._playlist.exists(),
            "segment_count": 0,
            "newest_segment_age_s": None,
            "newest_segment": None,
        }

        # Process liveness
        if self._process and self._process.returncode is None:
            info["process_alive"] = True
        elif self._process:
            info["process_exit_code"] = self._process.returncode

        # Segment freshness
        segs = self._segment_paths_from_playlist()
        info["segment_count"] = len(segs)
        if segs:
            try:
                newest = max(segs, key=lambda p: p.stat().st_mtime if p.exists() else 0)
                if newest.exists():
                    age = time.time() - newest.stat().st_mtime
                    info["newest_segment_age_s"] = round(age, 1)
                    info["newest_segment"] = newest.name
            except Exception:
                pass

        return info
