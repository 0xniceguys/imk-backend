"""Image file validation for fighter uploads."""

import logging
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from app.exceptions import ValidationError

logger = logging.getLogger(__name__)

# Supported image formats
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Size limits
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_DIMENSIONS = (2048, 2048)  # 2048x2048 pixels


def validate_image_file(file: BinaryIO, filename: str) -> dict:
    """Validate an uploaded image file.

    Args:
        file: File-like object (from UploadFile.file)
        filename: Original filename

    Returns:
        dict with metadata:
            - format: Image format (JPEG, PNG, WEBP)
            - width: Image width in pixels
            - height: Image height in pixels
            - file_size_bytes: File size

    Raises:
        ValidationError: If the file is invalid
    """
    # Check extension
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Invalid file extension: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # Get file size
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning

    if file_size > MAX_FILE_SIZE:
        raise ValidationError(
            f"File too large: {file_size / 1024 / 1024:.1f} MB (max {MAX_FILE_SIZE / 1024 / 1024:.0f} MB)"
        )

    if file_size == 0:
        raise ValidationError("File is empty")

    try:
        # Open and validate image
        img = Image.open(file)
        img.verify()  # Verify it's a valid image

        # Reopen for format checking (verify() invalidates the image object)
        file.seek(0)
        img = Image.open(file)

        # Check format
        if img.format not in ALLOWED_FORMATS:
            raise ValidationError(
                f"Invalid image format: {img.format}. Allowed: {', '.join(ALLOWED_FORMATS)}"
            )

        # Check dimensions
        width, height = img.size
        if width > MAX_DIMENSIONS[0] or height > MAX_DIMENSIONS[1]:
            raise ValidationError(
                f"Image too large: {width}x{height}. Max: {MAX_DIMENSIONS[0]}x{MAX_DIMENSIONS[1]}"
            )

        if width < 100 or height < 100:
            raise ValidationError(
                f"Image too small: {width}x{height}. Minimum: 100x100"
            )

        metadata = {
            "format": img.format,
            "width": width,
            "height": height,
            "file_size_bytes": file_size,
        }

        logger.info(
            f"✓ Image validation passed: {filename}",
            extra={
                "format": img.format,
                "dimensions": f"{width}x{height}",
                "file_size_kb": round(file_size / 1024, 1),
            },
        )

        return metadata

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Image validation error: {e}", exc_info=True)
        raise ValidationError(f"Invalid image file: {e}")
