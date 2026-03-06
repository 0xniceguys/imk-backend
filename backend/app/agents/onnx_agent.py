"""
ONNX inference agent — loads any exported .onnx policy model.

No PyTorch dependency. Uses onnxruntime (~30MB) for inference.
The ONNX model is expected to take a float32 observation tensor and
output action logits (from which we sample).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort

from app.agents.base import FighterAgent
from app.agents.observation import FrameStack, RAW_OBS_DIM, build_obs
from app.services.actions import ActionPacket, MacroAction
from app.services.game_state import FightState

logger = logging.getLogger(__name__)

ACTIONS = list(MacroAction)


def _shape_dim(shape: list[object], idx: int, default: int) -> int:
    """Extract an integer dim from ONNX shape metadata with fallback."""
    if idx < 0 or idx >= len(shape):
        return default
    value = shape[idx]
    return value if isinstance(value, int) and value > 0 else default


class OnnxAgent(FighterAgent):
    """Generic ONNX-based agent.

    Loads a .onnx model that expects:
      Input:  "obs" — float32 tensor of shape (1, obs_dim)
      Output: "logits" — float32 tensor of shape (1, n_actions)

    For MLP models, obs_dim = 28 (7 raw × 4 stacked frames).
    For sequence models (LSTM), the ONNX export should flatten the
    recurrence into the graph (stateful ONNX is handled separately).

    Args:
        model_path: Path to the .onnx file.
        use_frame_stack: If True, stack 4 frames (for MLP). If False, use raw obs.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        use_frame_stack: bool = True,
        n_stack_frames: int = 4,
    ) -> None:
        self.model_path = str(model_path)
        self.use_frame_stack = use_frame_stack

        # Load ONNX model
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])

        # Inspect model inputs/outputs
        self._input_name = self.session.get_inputs()[0].name
        self._input_shape = self.session.get_inputs()[0].shape
        self._output_name = self.session.get_outputs()[0].name

        # Frame stacking for MLP-style models
        if use_frame_stack:
            self.frame_stack = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=n_stack_frames)
        else:
            self.frame_stack = None

        logger.info(
            "ONNX agent loaded: %s (input=%s shape=%s, output=%s, frame_stack=%s)",
            model_path, self._input_name, self._input_shape,
            self._output_name, use_frame_stack,
        )

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        raw_obs = build_obs(state, player=player)

        if self.frame_stack is not None:
            obs = self.frame_stack.push(raw_obs)
        else:
            obs = raw_obs

        # Run inference
        obs_array = np.array([obs], dtype=np.float32)
        outputs = self.session.run(None, {self._input_name: obs_array})
        logits = outputs[0][0]  # shape: (n_actions,)

        # Sample from the policy distribution (softmax → multinomial)
        probs = _softmax(logits)
        action_idx = np.random.choice(len(probs), p=probs)

        return ActionPacket(
            macro_action=ACTIONS[action_idx],
            repeat_frames=1,
            player=player,
        )

    def reset(self) -> None:
        if self.frame_stack is not None:
            self.frame_stack.reset()


class OnnxLstmAgent(FighterAgent):
    """ONNX-based LSTM agent with hidden state management.

    The ONNX model expects 3 inputs:
      "obs"  — float32 (1, obs_dim)
      "h_in" — float32 (1, 1, hidden_size)
      "c_in" — float32 (1, 1, hidden_size)

    And returns 3 outputs:
      "logits" — float32 (1, n_actions)
      "h_out"  — float32 (1, 1, hidden_size)
      "c_out"  — float32 (1, 1, hidden_size)
    """

    def __init__(self, model_path: str | Path, *, hidden_size: int | None = None) -> None:
        self.model_path = str(model_path)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])

        # Get input/output names
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        self._obs_name = inputs[0].name
        self._h_name = inputs[1].name
        self._c_name = inputs[2].name
        self._obs_dim = _shape_dim(inputs[0].shape, 1, RAW_OBS_DIM)
        inferred_hidden = _shape_dim(inputs[1].shape, 2, 128)
        self.hidden_size = hidden_size or inferred_hidden

        self._h: np.ndarray | None = None
        self._c: np.ndarray | None = None

        logger.info(
            "ONNX LSTM agent loaded: %s (obs_dim=%s, hidden=%d, outputs=%d)",
            model_path, self._obs_dim, self.hidden_size, len(outputs),
        )

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        if self._h is None:
            self._h = np.zeros((1, 1, self.hidden_size), dtype=np.float32)
            self._c = np.zeros((1, 1, self.hidden_size), dtype=np.float32)

        raw_obs = build_obs(state, player=player)
        # Pad to match expected obs_dim
        if len(raw_obs) < self._obs_dim:
            raw_obs = raw_obs + [0.0] * (self._obs_dim - len(raw_obs))

        obs_array = np.array([raw_obs], dtype=np.float32)
        outputs = self.session.run(None, {
            self._obs_name: obs_array,
            self._h_name: self._h,
            self._c_name: self._c,
        })

        logits = outputs[0][0]
        self._h = outputs[1]
        self._c = outputs[2]

        probs = _softmax(logits)
        action_idx = np.random.choice(len(probs), p=probs)

        return ActionPacket(
            macro_action=ACTIONS[action_idx],
            repeat_frames=1,
            player=player,
        )

    def reset(self) -> None:
        self._h = None
        self._c = None


class OnnxDiscRssmAgent(FighterAgent):
    """ONNX-based Discrete RSSM agent with GRU hidden state.

    The ONNX model expects 3 inputs:
      "obs"      — float32 (1, obs_dim)
      "h_in"     — float32 (1, det_size)
      "prev_act" — int64 (1,)

    And returns 2 outputs:
      "logits" — float32 (1, n_actions)
      "h_out"  — float32 (1, det_size)
    """

    def __init__(self, model_path: str | Path, *, det_size: int | None = None) -> None:
        self.model_path = str(model_path)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])

        inputs = self.session.get_inputs()
        self._obs_name = inputs[0].name
        self._h_name = inputs[1].name
        self._act_name = inputs[2].name
        inferred_det = _shape_dim(inputs[1].shape, 1, 128)
        self.det_size = det_size or inferred_det

        self._h: np.ndarray | None = None
        self._prev_act: int = 0
        self.frame_stack = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)

        logger.info("ONNX DiscRSSM agent loaded: %s (det=%d)", model_path, self.det_size)

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        if self._h is None:
            self._h = np.zeros((1, self.det_size), dtype=np.float32)

        obs = self.frame_stack.push(build_obs(state, player=player))
        obs_array = np.array([obs], dtype=np.float32)
        h_array = self._h
        act_array = np.array([self._prev_act], dtype=np.int64)

        outputs = self.session.run(None, {
            self._obs_name: obs_array,
            self._h_name: h_array,
            self._act_name: act_array,
        })

        logits = outputs[0][0]
        self._h = outputs[1]

        probs = _softmax(logits)
        action_idx = int(np.random.choice(len(probs), p=probs))
        self._prev_act = action_idx

        return ActionPacket(
            macro_action=ACTIONS[action_idx],
            repeat_frames=1,
            player=player,
        )

    def reset(self) -> None:
        self._h = None
        self._prev_act = 0
        self.frame_stack.reset()


class OnnxTransformerAgent(FighterAgent):
    """ONNX-based Transformer agent with context window.

    The ONNX model expects 1 input:
      "obs_seq" — float32 (seq_len, obs_dim)  — fixed size context window

    And returns 1 output:
      "logits" — float32 (1, n_actions)
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        seq_len: int = 16,
    ) -> None:
        self.model_path = str(model_path)
        self.seq_len = seq_len

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])

        self._input_name = self.session.get_inputs()[0].name
        self._buf: list[list[float]] = []
        self.frame_stack = FrameStack(obs_dim=RAW_OBS_DIM, n_frames=4)

        logger.info("ONNX Transformer agent loaded: %s (seq=%d)", model_path, seq_len)

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        obs = self.frame_stack.push(build_obs(state, player=player))
        self._buf.append(obs)
        if len(self._buf) > self.seq_len:
            self._buf.pop(0)

        # Pad with zeros if fewer than seq_len frames
        pad_count = self.seq_len - len(self._buf)
        padded = [[0.0] * len(obs)] * pad_count + self._buf
        obs_seq = np.array(padded, dtype=np.float32)

        outputs = self.session.run(None, {self._input_name: obs_seq})
        logits = outputs[0][0]

        probs = _softmax(logits)
        action_idx = int(np.random.choice(len(probs), p=probs))

        return ActionPacket(
            macro_action=ACTIONS[action_idx],
            repeat_frames=1,
            player=player,
        )

    def reset(self) -> None:
        self._buf = []
        self.frame_stack.reset()


def _softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    e = np.exp(x - np.max(x))
    return e / e.sum()
