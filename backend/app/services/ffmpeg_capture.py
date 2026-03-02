"""
FFmpeg-based frame capture — async-native.

Uses asyncio.create_subprocess_exec so stdout reads never block the thread pool.
Frames are MJPEG from a pipe: each JPEG starts FF D8, ends FF D9.

Platform:
  Linux:  -f x11grab captures Xvfb virtual display
  macOS:  -f avfoundation captures primary screen (device index "2")
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def is_linux() -> bool:
    return IS_LINUX


class FFmpegCapture:
    """Captures emulator frames via FFmpeg at up to 60fps.

    Fully async: uses asyncio.create_subprocess_exec so reads
    go through the event loop's I/O multiplexer — zero thread blocking.
    """

    def __init__(
        self,
        display: str = ":99",       # Linux: Xvfb display
        screen_index: str = "2",    # macOS: avfoundation screen device index
        width: int = 320,
        height: int = 240,
        framerate: int = 60,
        quality: int = 5,           # ffmpeg -q:v (2=best, 31=worst for MJPEG)
    ) -> None:
        self.display = display
        self.screen_index = screen_index
        self.width = width
        self.height = height
        self.framerate = framerate
        self.quality = quality
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._running = False

    def _build_cmd(self) -> list[str]:
        if IS_MACOS:
            return [
                "ffmpeg",
                "-f", "avfoundation",
                "-capture_cursor", "0",
                "-video_size", f"{self.width}x{self.height}",
                "-framerate", str(self.framerate),
                "-i", self.screen_index,
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", str(self.quality),
                "-an",
                "pipe:1",
            ]
        else:
            return [
                "ffmpeg",
                "-f", "x11grab",
                "-video_size", f"{self.width}x{self.height}",
                "-framerate", str(self.framerate),
                "-i", self.display,
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", str(self.quality),
                "-an",
                "pipe:1",
            ]

    async def start(self, on_frame: Callable[[bytes], Awaitable[None]]) -> None:
        """Start capturing. Calls on_frame(jpeg_bytes) for each complete frame."""
        if self._running:
            return

        self._running = True
        cmd = self._build_cmd()
        logger.info(
            "Starting FFmpeg capture (%s): %s",
            "avfoundation" if IS_MACOS else "x11grab",
            " ".join(cmd),
        )

        # asyncio subprocess: pipe stderr so FFmpeg errors appear in journalctl
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # was DEVNULL — now captured for debugging
        )

        # Log FFmpeg stderr in background
        asyncio.create_task(
            self._log_ffmpeg_stderr(),
            name="ffmpeg-stderr-logger",
        )

        self._reader_task = asyncio.create_task(
            self._read_frames(on_frame),
            name="ffmpeg-reader",
        )

    async def _log_ffmpeg_stderr(self) -> None:
        """Drain FFmpeg stderr and log it so errors surface in journalctl."""
        if not self._process or not self._process.stderr:
            return
        try:
            async for line in self._process.stderr:
                txt = line.decode(errors="replace").rstrip()
                if txt:
                    logger.warning("FFmpeg: %s", txt)
        except Exception:
            pass

    async def _read_frames(
        self, on_frame: Callable[[bytes], Awaitable[None]]
    ) -> None:
        """Read MJPEG frames from FFmpeg stdout (async StreamReader)."""
        stdout = self._process.stdout  # type: ignore[union-attr]
        if stdout is None:
            return

        JPEG_START = b"\xff\xd8"
        JPEG_END = b"\xff\xd9"
        buf = bytearray()
        frames_received = 0
        last_log_time = asyncio.get_event_loop().time()

        try:
            while self._running:
                chunk = await asyncio.wait_for(stdout.read(65536), timeout=5.0)
                if not chunk:
                    logger.warning("FFmpeg stdout closed (frames_received=%d)", frames_received)
                    break

                buf.extend(chunk)

                # Extract all complete JPEG frames from the buffer
                while True:
                    start = buf.find(JPEG_START)
                    if start == -1:
                        buf.clear()
                        break

                    if start > 0:
                        del buf[:start]

                    end = buf.find(JPEG_END, 2)
                    if end == -1:
                        break  # Frame not yet complete

                    frame = bytes(buf[: end + 2])
                    del buf[: end + 2]

                    try:
                        await on_frame(frame)
                        frames_received += 1
                    except Exception:
                        pass

                    # Log frame rate every 5 seconds
                    now = asyncio.get_event_loop().time()
                    if now - last_log_time >= 5.0:
                        logger.info(
                            "FFmpeg capture: %d frames in last 5s (total=%d)",
                            frames_received, frames_received,
                        )
                        last_log_time = now

                    # Yield back to event loop between frames
                    await asyncio.sleep(0)

        except asyncio.TimeoutError:
            logger.error(
                "FFmpeg: no data received for 5s (frames_received=%d) — "
                "display may be blank or wrong display number",
                frames_received,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("FFmpeg frame reader error")
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop capture and terminate the FFmpeg process."""
        self._running = False

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
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

        logger.info("FFmpeg capture stopped")
