"""
FFmpeg WebRTC capture — replaces HLS capture when settings.use_webrtc is True.

Instead of writing HLS segments to disk, this sends:
  - H.264 RTP  →  mediasoup PlainTransport video port
  - Opus RTP   →  mediasoup PlainTransport audio port

mediasoup then relays to Flutter subscribers via WebRtcTransport.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

import httpx

logger = logging.getLogger(__name__)

# Match ffmpeg_combined_hls settings
XVFB_DISPLAY        = ":99"
PULSE_MONITOR_SOURCE = "auto_null.monitor"
CAPTURE_WIDTH  = 640
CAPTURE_HEIGHT = 480
CAPTURE_FPS    = 24
OUTPUT_WIDTH   = 480
OUTPUT_HEIGHT  = 368

# RTP payload types — must match mediasoup server.js producer definitions
VIDEO_PT = 102   # H.264
AUDIO_PT = 111   # Opus


class FFmpegWebrtc:
    """
    One FFmpeg process: x11grab + PulseAudio → H.264 RTP + Opus RTP → mediasoup.

    Usage::

        cap = FFmpegWebrtc(match_id="abc123", mediasoup_url="http://127.0.0.1:3000")
        rtp_params = await cap.start()   # returns {"videoPort": X, "audioPort": Y}
        # ... match runs ...
        await cap.stop()
    """

    def __init__(self, match_id: str, mediasoup_url: str = "http://127.0.0.1:3000") -> None:
        self.match_id      = match_id
        self.mediasoup_url = mediasoup_url
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._running = False
        self._video_port: int = 0
        self._audio_port: int = 0

    async def start(self) -> dict:
        """
        1. Ask mediasoup to create a room and allocate RTP ports.
        2. Start FFmpeg sending RTP to those ports.
        Returns {"videoPort": X, "audioPort": Y}.
        """
        if self._running:
            return {"videoPort": self._video_port, "audioPort": self._audio_port}

        # Step 1 — create mediasoup room
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.mediasoup_url}/rooms/{self.match_id}")
            resp.raise_for_status()
            params = resp.json()

        self._video_port = params["videoPort"]
        self._audio_port = params["audioPort"]
        logger.info(
            "[WebRTC] Room created: match=%s videoPort=%d audioPort=%d",
            self.match_id, self._video_port, self._audio_port,
        )

        # Step 2 — build FFmpeg RTP command
        cmd = self._build_cmd(self._video_port, self._audio_port)

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
            self._drain_stderr(), name=f"webrtc-stderr-{self.match_id}"
        )
        logger.info(
            "[WebRTC] FFmpeg started (pid=%s) for match=%s",
            self._process.pid, self.match_id,
        )
        return {"videoPort": self._video_port, "audioPort": self._audio_port}

    def _build_cmd(self, video_port: int, audio_port: int) -> list[str]:
        """
        FFmpeg: x11grab + PulseAudio → RTP UDP to mediasoup.

        Two separate RTP outputs:
          - Video: H.264 → udp://127.0.0.1:<video_port>
          - Audio: Opus  → udp://127.0.0.1:<audio_port>
        """
        keyframes = int(CAPTURE_FPS * 1)  # keyframe every 1s for WebRTC
        return [
            "ffmpeg", "-y",
            "-fflags", "+genpts",
            # ── Video input ─────────────────────────────────────────────────
            "-thread_queue_size", "4096",
            "-f", "x11grab",
            "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
            "-framerate", str(CAPTURE_FPS),
            "-i", XVFB_DISPLAY,
            # ── Audio input ─────────────────────────────────────────────────
            "-thread_queue_size", "4096",
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # ── Video encoding ───────────────────────────────────────────────
            "-map", "0:v:0",
            "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},setpts=PTS-STARTPTS",
            "-fps_mode", "cfr",
            "-r", str(CAPTURE_FPS),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-g", str(keyframes),
            "-keyint_min", str(keyframes),
            "-sc_threshold", "0",
            "-b:v", "400k",
            "-maxrate", "500k",
            "-bufsize", "800k",
            "-pix_fmt", "yuv420p",
            # ── Audio encoding (Opus — required for WebRTC) ──────────────────
            "-map", "1:a:0",
            "-af", "aresample=async=200:min_hard_comp=0.100:first_pts=0",
            "-c:a", "libopus",
            "-b:a", "64k",
            "-ar", "48000",   # Opus always uses 48 kHz
            "-ac", "2",
            "-application", "lowdelay",   # minimise Opus internal buffering
            # ── RTP outputs ──────────────────────────────────────────────────
            # Video RTP stream
            "-an",    # no audio in video output
            "-f", "rtp",
            f"rtp://127.0.0.1:{video_port}?pkt_size=1200&ssrc=1111&payload_type={VIDEO_PT}",
            # Audio RTP stream (separate output)
            # Re-read inputs for second output
        ]
        # NOTE: two separate -f rtp outputs requires tee muxer or two outputs.
        # Built correctly below.

    def _build_cmd(self, video_port: int, audio_port: int) -> list[str]:
        """Correct two-output RTP command using FFmpeg output mapping."""
        keyframes = int(CAPTURE_FPS * 1)
        return [
            "ffmpeg", "-y",
            "-fflags", "+genpts",
            # ── Inputs ──────────────────────────────────────────────────────
            "-thread_queue_size", "4096",
            "-f", "x11grab",
            "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
            "-framerate", str(CAPTURE_FPS),
            "-i", XVFB_DISPLAY,
            "-thread_queue_size", "4096",
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            # ── Video output → video RTP port ────────────────────────────────
            "-map", "0:v:0",
            "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},setpts=PTS-STARTPTS",
            "-fps_mode", "cfr",
            "-r", str(CAPTURE_FPS),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-g", str(keyframes),
            "-keyint_min", str(keyframes),
            "-sc_threshold", "0",
            "-b:v", "400k",
            "-maxrate", "500k",
            "-bufsize", "800k",
            "-pix_fmt", "yuv420p",
            "-an",
            "-f", "rtp",
            f"rtp://127.0.0.1:{video_port}?pkt_size=1200&ssrc=1111&payload_type={VIDEO_PT}",
            # ── Audio output → audio RTP port ────────────────────────────────
            "-map", "1:a:0",
            "-af", "aresample=async=200:min_hard_comp=0.100:first_pts=0",
            "-c:a", "libopus",
            "-b:a", "64k",
            "-ar", "48000",
            "-ac", "2",
            "-application", "lowdelay",
            "-vn",
            "-f", "rtp",
            f"rtp://127.0.0.1:{audio_port}?pkt_size=1200&ssrc=2222&payload_type={AUDIO_PT}",
        ]

    async def _drain_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        try:
            async for line in self._process.stderr:
                if not self._running:
                    break
                txt = line.decode(errors="replace").rstrip()
                if not txt:
                    continue
                lower = txt.lower()
                if any(k in lower for k in ("error", "fatal", "queue overflow")):
                    logger.warning("[WebRTC|FFmpeg] %s | match=%s", txt, self.match_id)
                elif "frame=" in lower and "fps=" in lower:
                    pass   # suppress noisy progress lines
                else:
                    logger.debug("[WebRTC|FFmpeg] %s | match=%s", txt, self.match_id)
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

        # Tell mediasoup to tear down the room
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.delete(f"{self.mediasoup_url}/rooms/{self.match_id}")
            logger.info("[WebRTC] Room closed: match=%s", self.match_id)
        except Exception as exc:
            logger.warning("[WebRTC] Failed to close room %s: %s", self.match_id, exc)
