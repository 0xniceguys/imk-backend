"""
LiveKit Publisher — Python-native video/audio publisher for live match streaming.

Architecture (no ingress service needed):
  x11grab + PulseAudio → FFmpeg (raw frames) → Python → livekit.rtc → LiveKit Server
                                                                            ↓
                                                              Flutter livekit_client

Two FFmpeg processes:
  1. Video: x11grab → rawvideo YUV I420 → stdout pipe
  2. Audio: PulseAudio → raw PCM s16le 48kHz stereo → stdout pipe

Python reads raw frames, wraps them in livekit.rtc.VideoFrame / AudioFrame,
and pushes to VideoSource / AudioSource. The Room publishes these as live
WebRTC tracks. Flutter subscribes using the official livekit_client Dart SDK.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct

from livekit import api, rtc

logger = logging.getLogger(__name__)

# Video capture settings
XVFB_DISPLAY         = ":99"
PULSE_MONITOR_SOURCE = "auto_null.monitor"
CAPTURE_WIDTH        = 640
CAPTURE_HEIGHT       = 480
CAPTURE_FPS          = 24
OUTPUT_WIDTH         = 480
OUTPUT_HEIGHT        = 368

# Audio settings — must match LiveKit (Opus needs 48kHz)
AUDIO_SAMPLE_RATE    = 48000
AUDIO_CHANNELS       = 2
# WebRTC standard audio chunk: 10ms at 48kHz = 480 samples per channel
AUDIO_SAMPLES_PER_CHUNK = 480
AUDIO_BYTES_PER_CHUNK   = AUDIO_SAMPLES_PER_CHUNK * AUDIO_CHANNELS * 2  # s16le = 2 bytes

# YUV I420 frame size: W*H for Y, W*H/4 for each Cb/Cr
VIDEO_FRAME_BYTES = int(OUTPUT_WIDTH * OUTPUT_HEIGHT * 3 / 2)


class LiveKitPublisher:
    """
    Publishes live match video + audio to a LiveKit room.

    Usage::

        pub = LiveKitPublisher(
            match_id="abc123",
            livekit_url="ws://localhost:7880",
            api_key="imk_key",
            api_secret="imk_secret",
        )
        await pub.start()
        # match runs ...
        await pub.stop()
    """

    def __init__(
        self,
        match_id: str,
        livekit_url: str = "ws://localhost:7880",
        api_key: str    = "imk_key",
        api_secret: str = "imk_secret_change_in_production",
    ) -> None:
        self.match_id    = match_id
        self.livekit_url = livekit_url
        self.api_key     = api_key
        self.api_secret  = api_secret

        self._room:   rtc.Room | None = None
        self._video_proc:  asyncio.subprocess.Process | None = None
        self._audio_proc:  asyncio.subprocess.Process | None = None
        self._video_task:  asyncio.Task | None = None
        self._audio_task:  asyncio.Task | None = None
        self._running = False

    # ── Public API ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # 1. Connect to LiveKit room as publisher
        token = self._make_publisher_token()
        self._room = rtc.Room()
        await self._room.connect(self.livekit_url, token)
        logger.info("[LiveKit] Connected to room=%s", self.match_id)

        # 2. Create video source + track
        video_source = rtc.VideoSource(width=OUTPUT_WIDTH, height=OUTPUT_HEIGHT)
        audio_source = rtc.AudioSource(
            sample_rate=AUDIO_SAMPLE_RATE,
            num_channels=AUDIO_CHANNELS,
        )

        video_track = rtc.LocalVideoTrack.create_video_track("screen", video_source)
        audio_track = rtc.LocalAudioTrack.create_audio_track("mic", audio_source)

        video_opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        audio_opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)

        await self._room.local_participant.publish_track(video_track, video_opts)
        await self._room.local_participant.publish_track(audio_track, audio_opts)
        logger.info("[LiveKit] Tracks published for match=%s", self.match_id)

        # 3. Start FFmpeg and frame-pump tasks
        env = dict(os.environ)
        uid = os.getuid()
        env.setdefault("DISPLAY", XVFB_DISPLAY)
        env.setdefault("PULSE_SERVER", f"unix:/run/user/{uid}/pulse/native")

        video_cmd = self._video_cmd()
        audio_cmd = self._audio_cmd()
        logger.info("[LiveKit] Video cmd: %s", " ".join(video_cmd))
        logger.info("[LiveKit] Audio cmd: %s", " ".join(audio_cmd))
        logger.info("[LiveKit] DISPLAY=%s PULSE_SERVER=%s", env.get("DISPLAY"), env.get("PULSE_SERVER"))

        self._video_proc = await asyncio.create_subprocess_exec(
            *video_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._audio_proc = await asyncio.create_subprocess_exec(
            *audio_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._video_task = asyncio.create_task(
            self._pump_video(video_source),
            name=f"lk-video-{self.match_id}",
        )
        self._audio_task = asyncio.create_task(
            self._pump_audio(audio_source),
            name=f"lk-audio-{self.match_id}",
        )
        # Drain stderr so we can see FFmpeg errors
        asyncio.create_task(self._drain_stderr(self._video_proc, "video"), name=f"lk-vstderr-{self.match_id}")
        asyncio.create_task(self._drain_stderr(self._audio_proc, "audio"), name=f"lk-astderr-{self.match_id}")
        logger.info("[LiveKit] Frame pumps started for match=%s", self.match_id)

    async def stop(self) -> None:
        self._running = False
        for task in (self._video_task, self._audio_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for proc in (self._video_proc, self._audio_proc):
            if proc:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=4)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        if self._room:
            try:
                await self._room.disconnect()
            except Exception:
                pass
            self._room = None
        logger.info("[LiveKit] Publisher stopped for match=%s", self.match_id)

    def make_subscriber_token(self, participant_id: str) -> str:
        """Generate a JWT for a Flutter viewer to join this match's room."""
        token = api.AccessToken(self.api_key, self.api_secret)
        token.with_identity(participant_id).with_name(participant_id)
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=self.match_id,
            can_subscribe=True,
            can_publish=False,
            can_publish_data=False,
        ))
        return token.to_jwt()

    # ── Internals ────────────────────────────────────────────────────────────

    async def _drain_stderr(self, proc: asyncio.subprocess.Process, label: str) -> None:
        """Log FFmpeg stderr for debugging."""
        if not proc or not proc.stderr:
            return
        try:
            async for line in proc.stderr:
                txt = line.decode(errors="replace").rstrip()
                if txt:
                    logger.warning("[LiveKit|FFmpeg-%s] %s | match=%s", label, txt, self.match_id)
        except Exception:
            pass

    def _make_publisher_token(self) -> str:
        token = api.AccessToken(self.api_key, self.api_secret)
        token.with_identity(f"ffmpeg-{self.match_id}").with_name("FFmpeg Publisher")
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=self.match_id,
            can_publish=True,
            room_create=True,
        ))
        return token.to_jwt()

    def _video_cmd(self) -> list[str]:
        """FFmpeg: x11grab → YUV I420 raw frames on stdout."""
        return [
            "ffmpeg", "-y",
            "-thread_queue_size", "4096",
            "-f", "x11grab",
            "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
            "-framerate", str(CAPTURE_FPS),
            "-i", XVFB_DISPLAY,
            "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},setpts=PTS-STARTPTS",
            "-fps_mode", "cfr",
            "-r", str(CAPTURE_FPS),
            "-f", "rawvideo",
            "-pix_fmt", "yuv420p",   # I420 — smallest raw format (1.5 bytes/px)
            "-an", "pipe:1",
        ]

    def _audio_cmd(self) -> list[str]:
        """FFmpeg: PulseAudio → raw PCM s16le 48kHz stereo on stdout."""
        return [
            "ffmpeg", "-y",
            "-thread_queue_size", "4096",
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            "-vn",
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", str(AUDIO_CHANNELS),
            "-f", "s16le",
            "pipe:1",
        ]

    async def _pump_video(self, source: rtc.VideoSource) -> None:
        """Read raw YUV I420 frames from FFmpeg stdout and push to VideoSource."""
        proc = self._video_proc
        if not proc or not proc.stdout:
            return
        try:
            while self._running:
                data = await proc.stdout.readexactly(VIDEO_FRAME_BYTES)
                frame = rtc.VideoFrame(
                    width=OUTPUT_WIDTH,
                    height=OUTPUT_HEIGHT,
                    type=rtc.VideoBufferType.I420,
                    data=bytearray(data),
                )
                source.capture_frame(frame)
        except asyncio.IncompleteReadError:
            logger.warning("[LiveKit] Video FFmpeg EOF for match=%s", self.match_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[LiveKit] Video pump error match=%s: %s", self.match_id, exc)

    async def _pump_audio(self, source: rtc.AudioSource) -> None:
        """Read raw PCM s16le from FFmpeg stdout and push to AudioSource."""
        proc = self._audio_proc
        if not proc or not proc.stdout:
            return
        try:
            while self._running:
                data = await proc.stdout.readexactly(AUDIO_BYTES_PER_CHUNK)
                frame = rtc.AudioFrame(
                    data=bytearray(data),
                    sample_rate=AUDIO_SAMPLE_RATE,
                    num_channels=AUDIO_CHANNELS,
                    samples_per_channel=AUDIO_SAMPLES_PER_CHUNK,
                )
                await source.capture_frame(frame)
        except asyncio.IncompleteReadError:
            logger.warning("[LiveKit] Audio FFmpeg EOF for match=%s", self.match_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[LiveKit] Audio pump error match=%s: %s", self.match_id, exc)
