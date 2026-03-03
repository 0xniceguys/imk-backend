"""Unit tests for agent validator."""

import io
from pathlib import Path

import onnx
import pytest

from app.exceptions import ValidationError
from app.services.agent_validator import validate_onnx_checkpoint


def test_validate_onnx_checkpoint_file_not_found():
    """Test validation fails for non-existent file."""
    with pytest.raises(ValidationError, match="File not found"):
        validate_onnx_checkpoint(Path("/nonexistent.onnx"), "lstm")


def test_validate_onnx_checkpoint_wrong_extension(tmp_path):
    """Test validation fails for wrong file extension."""
    file_path = tmp_path / "model.txt"
    file_path.write_text("fake model")

    with pytest.raises(ValidationError, match="must have .onnx extension"):
        validate_onnx_checkpoint(file_path, "lstm")


def test_validate_onnx_checkpoint_too_large(tmp_path):
    """Test validation fails for files exceeding max size."""
    file_path = tmp_path / "large.onnx"
    # Create a file larger than 100 MB
    with open(file_path, "wb") as f:
        f.write(b"0" * (101 * 1024 * 1024))

    with pytest.raises(ValidationError, match="File too large"):
        validate_onnx_checkpoint(file_path, "lstm")


def test_validate_onnx_checkpoint_invalid_onnx(tmp_path):
    """Test validation fails for invalid ONNX file."""
    file_path = tmp_path / "invalid.onnx"
    file_path.write_bytes(b"not an onnx file")

    with pytest.raises(ValidationError, match="Invalid ONNX model"):
        validate_onnx_checkpoint(file_path, "lstm")


def test_validate_onnx_checkpoint_valid(tmp_path):
    """Test validation succeeds for valid ONNX file."""
    # Create a minimal valid ONNX model
    input_tensor = onnx.helper.make_tensor_value_info("input", onnx.TensorProto.FLOAT, [1, 10])
    output_tensor = onnx.helper.make_tensor_value_info("output", onnx.TensorProto.FLOAT, [1, 5])

    # Create a simple identity node
    node = onnx.helper.make_node(
        "Identity",
        inputs=["input"],
        outputs=["output"],
    )

    graph = onnx.helper.make_graph(
        [node],
        "test_model",
        [input_tensor],
        [output_tensor],
    )

    model = onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid("", 13)])

    file_path = tmp_path / "valid.onnx"
    onnx.save(model, str(file_path))

    # Validate
    metadata = validate_onnx_checkpoint(file_path, "lstm")

    assert metadata["file_size_bytes"] > 0
    assert "input_shapes" in metadata
    assert "output_shapes" in metadata
    assert metadata["opset_version"] == 13
