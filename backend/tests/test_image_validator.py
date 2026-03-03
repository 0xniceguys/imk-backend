"""Unit tests for image validator."""

import io

import pytest
from PIL import Image

from app.exceptions import ValidationError
from app.services.image_validator import validate_image_file


def create_test_image(width=200, height=200, format="JPEG"):
    """Create a test image in memory."""
    img = Image.new("RGB", (width, height), color="red")
    buffer = io.BytesIO()
    img.save(buffer, format=format)
    buffer.seek(0)
    return buffer


def test_validate_image_invalid_extension():
    """Test validation fails for invalid extension."""
    buffer = io.BytesIO(b"fake image")

    with pytest.raises(ValidationError, match="Invalid file extension"):
        validate_image_file(buffer, "image.txt")


def test_validate_image_too_large():
    """Test validation fails for files exceeding max size."""
    # Create buffer larger than 5 MB
    buffer = io.BytesIO(b"0" * (6 * 1024 * 1024))

    with pytest.raises(ValidationError, match="File too large"):
        validate_image_file(buffer, "large.jpg")


def test_validate_image_empty_file():
    """Test validation fails for empty file."""
    buffer = io.BytesIO(b"")

    with pytest.raises(ValidationError, match="File is empty"):
        validate_image_file(buffer, "empty.jpg")


def test_validate_image_invalid_content():
    """Test validation fails for invalid image content."""
    buffer = io.BytesIO(b"not an image")

    with pytest.raises(ValidationError, match="Invalid image file"):
        validate_image_file(buffer, "invalid.jpg")


def test_validate_image_too_small():
    """Test validation fails for images below minimum size."""
    buffer = create_test_image(width=50, height=50)

    with pytest.raises(ValidationError, match="Image too small"):
        validate_image_file(buffer, "small.jpg")


def test_validate_image_too_large_dimensions():
    """Test validation fails for images exceeding max dimensions."""
    buffer = create_test_image(width=3000, height=3000)

    with pytest.raises(ValidationError, match="Image too large"):
        validate_image_file(buffer, "huge.jpg")


def test_validate_image_valid_jpeg():
    """Test validation succeeds for valid JPEG."""
    buffer = create_test_image(width=500, height=500, format="JPEG")

    metadata = validate_image_file(buffer, "test.jpg")

    assert metadata["format"] == "JPEG"
    assert metadata["width"] == 500
    assert metadata["height"] == 500
    assert metadata["file_size_bytes"] > 0


def test_validate_image_valid_png():
    """Test validation succeeds for valid PNG."""
    buffer = create_test_image(width=400, height=400, format="PNG")

    metadata = validate_image_file(buffer, "test.png")

    assert metadata["format"] == "PNG"
    assert metadata["width"] == 400
    assert metadata["height"] == 400


def test_validate_image_valid_webp():
    """Test validation succeeds for valid WebP."""
    buffer = create_test_image(width=600, height=600, format="WEBP")

    metadata = validate_image_file(buffer, "test.webp")

    assert metadata["format"] == "WEBP"
    assert metadata["width"] == 600
    assert metadata["height"] == 600
