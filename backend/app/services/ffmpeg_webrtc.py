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

Jitter mitigation:
  - Clock-based frame pacing: delivers video frames at steady intervals
    regardless of FFmpeg read timing.
  - Producer/consumer buffering: FFmpeg reads are decoupled from frame
    delivery using an asyncio.Queue, absorbing read-timing variance.
  - Low-latency FFmpeg flags: -fflags nobuffer, minimal probesize.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

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

# Frame pacing
FRAME_INTERVAL = 1.0 / CAPTURE_FPS  # ~41.67ms at 24fps
AUDIO_CHUNK_INTERVAL = AUDIO_SAMPLES_PER_CHUNK / AUDIO_SAMPLE_RATE  # 10ms

# Buffer sizes (asyncio.Queue maxsize)
VIDEO_QUEUE_SIZE = 3   # ~125ms buffer at 24fps — small to keep latency low
AUDIO_QUEUE_SIZE = 10  # ~100ms buffer at 10ms chunks


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
        self._video_reader_task:  asyncio.Task | None = None
        self._video_pacer_task:   asyncio.Task | None = None
        self._audio_reader_task:  asyncio.Task | None = None
        self._audio_pacer_task:   asyncio.Task | None = None
        self._running = False

        # Producer/consumer queues for decoupling FFmpeg reads from delivery
        self._video_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=VIDEO_QUEUE_SIZE)
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=AUDIO_QUEUE_SIZE)

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

        # Video: producer reads from FFmpeg, consumer paces delivery
        self._video_reader_task = asyncio.create_task(
            self._read_video_frames(),
            name=f"lk-vread-{self.match_id}",
        )
        self._video_pacer_task = asyncio.create_task(
            self._pace_video(video_source),
            name=f"lk-vpace-{self.match_id}",
        )

        # Audio: producer reads from FFmpeg, consumer paces delivery
        self._audio_reader_task = asyncio.create_task(
            self._read_audio_chunks(),
            name=f"lk-aread-{self.match_id}",
        )
        self._audio_pacer_task = asyncio.create_task(
            self._pace_audio(audio_source),
            name=f"lk-apace-{self.match_id}",
        )

        # Drain stderr so we can see FFmpeg errors
        asyncio.create_task(self._drain_stderr(self._video_proc, "video"), name=f"lk-vstderr-{self.match_id}")
        asyncio.create_task(self._drain_stderr(self._audio_proc, "audio"), name=f"lk-astderr-{self.match_id}")
        logger.info("[LiveKit] Frame pumps started for match=%s (paced @ %dfps, buf=%d/%d)",
                     self.match_id, CAPTURE_FPS, VIDEO_QUEUE_SIZE, AUDIO_QUEUE_SIZE)

    async def stop(self) -> None:
        """Stop publisher with proper cleanup to prevent memory leaks."""
        self._running = False

        # Cancel all tasks
        for task in (self._video_reader_task, self._video_pacer_task,
                     self._audio_reader_task, self._audio_pacer_task):
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    logger.warning("[LiveKit] Error canceling task: %s", e)

        # Terminate FFmpeg processes with timeout
        for proc_name, proc in [("video", self._video_proc), ("audio", self._audio_proc)]:
            if proc:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("[LiveKit] %s FFmpeg didn't terminate gracefully, killing", proc_name)
                    try:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except Exception as e:
                        logger.error("[LiveKit] Failed to kill %s FFmpeg: %s", proc_name, e)
                except Exception as e:
                    logger.error("[LiveKit] Error stopping %s FFmpeg: %s", proc_name, e)

        # Disconnect from LiveKit room with timeout
        if self._room:
            try:
                await asyncio.wait_for(self._room.disconnect(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("[LiveKit] Room disconnect timed out for match=%s", self.match_id)
            except Exception as e:
                logger.error("[LiveKit] Error disconnecting room: %s", e)
            finally:
                self._room = None

        # Clear references to prevent memory leaks
        self._video_proc = None
        self._audio_proc = None
        self._video_reader_task = None
        self._video_pacer_task = None
        self._audio_reader_task = None
        self._audio_pacer_task = None

        # Drain queues
        for q in (self._video_queue, self._audio_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

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
        """FFmpeg: x11grab → YUV I420 raw frames on stdout (low-latency)."""
        return [
            "ffmpeg", "-y",
            # Low-latency input flags
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
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
        """FFmpeg: PulseAudio → raw PCM s16le 48kHz stereo on stdout (low-latency)."""
        return [
            "ffmpeg", "-y",
            # Low-latency input flags
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-thread_queue_size", "4096",
            "-f", "pulse",
            "-i", PULSE_MONITOR_SOURCE,
            "-vn",
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", str(AUDIO_CHANNELS),
            "-f", "s16le",
            "pipe:1",
        ]

    # ── Video producer/consumer ──────────────────────────────────────────────

    async def _read_video_frames(self) -> None:
        """Producer: read raw YUV frames from FFmpeg and enqueue them."""
        proc = self._video_proc
        if not proc or not proc.stdout:
            return
        try:
            while self._running:
                data = await proc.stdout.readexactly(VIDEO_FRAME_BYTES)
                try:
                    # Non-blocking put — if queue is full, drop oldest frame
                    # to keep latency low (prefer freshness over completeness)
                    if self._video_queue.full():
                        try:
                            self._video_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    self._video_queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass  # Should not happen after the drain above
        except asyncio.IncompleteReadError:
            logger.warning("[LiveKit] Video FFmpeg EOF for match=%s", self.match_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[LiveKit] Video reader error match=%s: %s", self.match_id, exc)
        finally:
            # Signal consumer to stop
            try:
                self._video_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def _pace_video(self, source: rtc.VideoSource) -> None:
        """Consumer: deliver video frames at a steady clock-paced rate."""
        next_frame_time = time.monotonic()
        frames_sent = 0
        t_start = time.monotonic()

        try:
            while self._running:
                # Wait until it's time to send the next frame
                now = time.monotonic()
                sleep_duration = next_frame_time - now
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)

                # Get the latest frame (prefer newest if multiple queued)
                frame_data: bytes | None = None
                try:
                    frame_data = self._video_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # No frame available — wait briefly for one
                    try:
                        frame_data = await asyncio.wait_for(
                            self._video_queue.get(), timeout=FRAME_INTERVAL * 2
                        )
                    except asyncio.TimeoutError:
                        # Still no frame; skip this tick and try next
                        next_frame_time += FRAME_INTERVAL
                        continue

                if frame_data is None:
                    break  # EOF signal from producer

                # Drain any extra queued frames (keep only latest for lowest latency)
                while not self._video_queue.empty():
                    try:
                        newer = self._video_queue.get_nowait()
                        if newer is None:
                            frame_data = None
                            break
                        frame_data = newer
                    except asyncio.QueueEmpty:
                        break

                if frame_data is None:
                    break

                frame = rtc.VideoFrame(
                    width=OUTPUT_WIDTH,
                    height=OUTPUT_HEIGHT,
                    type=rtc.VideoBufferType.I420,
                    data=bytearray(frame_data),
                )
                source.capture_frame(frame)

                frames_sent += 1
                # Advance clock — anchor to absolute time to prevent drift
                next_frame_time = t_start + (frames_sent * FRAME_INTERVAL)

                # Log stats every 5 seconds
                if frames_sent % (CAPTURE_FPS * 5) == 0:
                    elapsed = time.monotonic() - t_start
                    actual_fps = frames_sent / elapsed if elapsed > 0 else 0
                    logger.debug(
                        "[LiveKit] Video stats: %d frames, %.1f actual fps (target %d) match=%s",
                        frames_sent, actual_fps, CAPTURE_FPS, self.match_id,
                    )

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[LiveKit] Video pacer error match=%s: %s", self.match_id, exc)

    # ── Audio producer/consumer ──────────────────────────────────────────────

    async def _read_audio_chunks(self) -> None:
        """Producer: read raw PCM chunks from FFmpeg and enqueue them."""
        proc = self._audio_proc
        if not proc or not proc.stdout:
            return
        try:
            while self._running:
                data = await proc.stdout.readexactly(AUDIO_BYTES_PER_CHUNK)
                try:
                    if self._audio_queue.full():
                        try:
                            self._audio_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    self._audio_queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass
        except asyncio.IncompleteReadError:
            logger.warning("[LiveKit] Audio FFmpeg EOF for match=%s", self.match_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[LiveKit] Audio reader error match=%s: %s", self.match_id, exc)
        finally:
            try:
                self._audio_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def _pace_audio(self, source: rtc.AudioSource) -> None:
        """Consumer: deliver audio chunks at a steady clock-paced rate."""
        next_chunk_time = time.monotonic()
        chunks_sent = 0
        t_start = time.monotonic()

        try:
            while self._running:
                now = time.monotonic()
                sleep_duration = next_chunk_time - now
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)

                chunk_data: bytes | None = None
                try:
                    chunk_data = self._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        chunk_data = await asyncio.wait_for(
                            self._audio_queue.get(), timeout=AUDIO_CHUNK_INTERVAL * 3
                        )
                    except asyncio.TimeoutError:
                        next_chunk_time += AUDIO_CHUNK_INTERVAL
                        continue

                if chunk_data is None:
                    break

                frame = rtc.AudioFrame(
                    data=bytearray(chunk_data),
                    sample_rate=AUDIO_SAMPLE_RATE,
                    num_channels=AUDIO_CHANNELS,
                    samples_per_channel=AUDIO_SAMPLES_PER_CHUNK,
                )
                await source.capture_frame(frame)

                chunks_sent += 1
                next_chunk_time = t_start + (chunks_sent * AUDIO_CHUNK_INTERVAL)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[LiveKit] Audio pacer error match=%s: %s", self.match_id, exc)
