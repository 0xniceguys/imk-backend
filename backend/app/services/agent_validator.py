"""Agent checkpoint validation for uploaded ONNX files."""

import logging
from pathlib import Path

import onnx
from google.protobuf.message import DecodeError
from onnx import checker

from app.exceptions import ValidationError

logger = logging.getLogger(__name__)


def validate_onnx_checkpoint(file_path: Path, architecture: str) -> dict:
    """Validate an ONNX checkpoint file.

    Args:
        file_path: Path to the .onnx file
        architecture: Expected architecture (lstm, transformer, disc_rssm, obj_belief)

    Returns:
        dict with metadata:
            - file_size_bytes: int
            - input_shapes: dict
            - output_shapes: dict
            - opset_version: int

    Raises:
        ValidationError: If the file is invalid
    """
    if not file_path.exists():
        raise ValidationError(f"File not found: {file_path}")

    if not file_path.suffix == ".onnx":
        raise ValidationError("File must have .onnx extension")

    file_size = file_path.stat().st_size

    # Check max file size (100 MB)
    max_size = 100 * 1024 * 1024
    if file_size > max_size:
        raise ValidationError(
            f"File too large: {file_size / 1024 / 1024:.1f} MB (max {max_size / 1024 / 1024:.0f} MB)"
        )

    try:
        # Load ONNX model
        model = onnx.load(str(file_path))

        # Validate the model structure
        checker.check_model(model)

        # Extract metadata
        input_shapes = {}
        for inp in model.graph.input:
            shape = [dim.dim_value if dim.dim_value > 0 else "dynamic" for dim in inp.type.tensor_type.shape.dim]
            input_shapes[inp.name] = shape

        output_shapes = {}
        for out in model.graph.output:
            shape = [dim.dim_value if dim.dim_value > 0 else "dynamic" for dim in out.type.tensor_type.shape.dim]
            output_shapes[out.name] = shape

        opset_version = model.opset_import[0].version if model.opset_import else 0

        metadata = {
            "file_size_bytes": file_size,
            "input_shapes": input_shapes,
            "output_shapes": output_shapes,
            "opset_version": opset_version,
        }

        logger.info(
            f"✓ ONNX validation passed: {file_path.name}",
            extra={
                "architecture": architecture,
                "file_size_mb": round(file_size / 1024 / 1024, 2),
                "inputs": list(input_shapes.keys()),
                "outputs": list(output_shapes.keys()),
            },
        )

        return metadata

    except (onnx.checker.ValidationError, DecodeError) as e:
        raise ValidationError(f"Invalid ONNX model: {e}")
    except Exception as e:
        logger.error(f"ONNX validation error: {e}", exc_info=True)
        raise ValidationError(f"Failed to validate ONNX file: {e}")
