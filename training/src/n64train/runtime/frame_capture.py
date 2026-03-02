from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from n64train.paths import PATHS


@dataclass(frozen=True)
class FrameCaptureResult:
    frame_bytes: bytes | None
    frame_shape: tuple[int, int, int] | None
    source: str
    path: str | None = None
    stale: bool = False


class FrameCapture:
    def capture(self) -> FrameCaptureResult:
        raise NotImplementedError


@dataclass
class ScreenshotPollFrameCapture(FrameCapture):
    instance_id: str
    expected_channels: int = 3

    def _screenshot_dir(self) -> Path:
        return PATHS.local_m64p_instances_root / self.instance_id / "data" / "screenshots"

    def _png_shape(self, payload: bytes) -> tuple[int, int, int] | None:
        # Minimal PNG IHDR parser: width/height stored at bytes 16..24.
        if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        width, height = struct.unpack(">II", payload[16:24])
        return (height, width, self.expected_channels)

    def capture(self) -> FrameCaptureResult:
        shot_dir = self._screenshot_dir()
        if not shot_dir.exists():
            return FrameCaptureResult(None, None, source="screenshot_poll", stale=True)
        shots = [p for p in shot_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
        if not shots:
            return FrameCaptureResult(None, None, source="screenshot_poll", stale=True)
        latest = max(shots, key=lambda p: p.stat().st_mtime)
        payload = latest.read_bytes()
        return FrameCaptureResult(
            frame_bytes=payload,
            frame_shape=self._png_shape(payload),
            source="screenshot_poll",
            path=str(latest),
            stale=False,
        )
