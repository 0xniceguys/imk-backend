"""
Frame capture — reads screenshots from the emulator instance directory
and returns them as JPEG bytes for WebSocket delivery.

Fast path (JPEG input):
  mupen64plus is configured with ScreenShotFormat=2 → writes .jpg files.
  We just read the raw bytes and broadcast them — zero codec work.

Slow path (PNG input, legacy):
  mupen64plus writes .png files. We decode PNG and re-encode as JPEG.
  This is ~25ms per frame and is the old bottleneck.

Self-contained: no imports from the training package.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CapturedFrame:
    frame_bytes: bytes | None = None
    width: int = 0
    height: int = 0
    path: str | None = None
    stale: bool = True


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Extract width/height from PNG IHDR chunk (bytes 16-24)."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    return 0, 0


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Extract width/height from a JPEG SOF marker (best-effort)."""
    i = 0
    while i < len(data) - 3:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        # SOF markers: 0xC0–0xC3, 0xC5–0xC7, 0xC9–0xCB, 0xCD–0xCF
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 < len(data):
                h = int.from_bytes(data[i + 5: i + 7], "big")
                w = int.from_bytes(data[i + 7: i + 9], "big")
                return w, h
        if i + 3 < len(data):
            seg_len = int.from_bytes(data[i + 2: i + 4], "big")
            i += 2 + seg_len
        else:
            break
    return 0, 0


def _latest_file(directory: Path, *extensions: str) -> Path | None:
    """Return the most-recently-modified file with any of the given extensions."""
    candidates = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def capture_frame_jpeg(
    screenshot_dir: Path,
    quality: int = 70,
) -> CapturedFrame:
    """Read the latest screenshot and return JPEG bytes.

    Fast path: mupen64plus is configured to write JPEG directly.
    We just read the bytes — no Pillow, no re-encoding.

    Slow path: PNG screenshot exists. Decode with Pillow and re-encode.
    """
    if not screenshot_dir.exists():
        return CapturedFrame(stale=True)

    # Prefer JPEG (fast path) — mupen64plus writes .jpg when ScreenShotFormat=2
    latest = _latest_file(screenshot_dir, ".jpg", ".jpeg")
    if latest:
        data = latest.read_bytes()
        w, h = _jpeg_dimensions(data)
        return CapturedFrame(
            frame_bytes=data,
            width=w,
            height=h,
            path=str(latest),
            stale=False,
        )

    # Slow path: PNG fallback
    latest_png = _latest_file(screenshot_dir, ".png")
    if not latest_png:
        return CapturedFrame(stale=True)

    png_data = latest_png.read_bytes()
    w, h = _png_dimensions(png_data)

    try:
        from PIL import Image  # lazy import — only hit on slow path
        img = Image.open(io.BytesIO(png_data))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        jpeg_bytes = buf.getvalue()
    except Exception:
        # Return raw PNG if Pillow fails (better than nothing)
        return CapturedFrame(
            frame_bytes=png_data, width=w, height=h,
            path=str(latest_png), stale=False,
        )

    return CapturedFrame(
        frame_bytes=jpeg_bytes,
        width=w,
        height=h,
        path=str(latest_png),
        stale=False,
    )


# Legacy: kept for compatibility with any caller using capture_frame()
def capture_frame(screenshot_dir: Path) -> CapturedFrame:
    """Return raw frame bytes (PNG or JPEG) without any conversion."""
    if not screenshot_dir.exists():
        return CapturedFrame(stale=True)
    latest = _latest_file(screenshot_dir, ".jpg", ".jpeg", ".png")
    if not latest:
        return CapturedFrame(stale=True)
    data = latest.read_bytes()
    if latest.suffix.lower() in (".jpg", ".jpeg"):
        w, h = _jpeg_dimensions(data)
    else:
        w, h = _png_dimensions(data)
    return CapturedFrame(frame_bytes=data, width=w, height=h, path=str(latest), stale=False)
