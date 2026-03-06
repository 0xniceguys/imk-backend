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


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _resolve_shape_dim(shape: list[object], index: int, default: int) -> int:
    if not shape:
        return default
    if index < 0:
        index = len(shape) + index
    if index < 0 or index >= len(shape):
        return default
    resolved = _positive_int(shape[index])
    return resolved if resolved is not None else default


def _materialize_shape(shape: list[object], *, last_dim_default: int) -> tuple[int, ...]:
    if not shape:
        return (1, last_dim_default)

    out: list[int] = []
    last_idx = len(shape) - 1
    for idx, dim in enumerate(shape):
        resolved = _positive_int(dim)
        if resolved is not None:
            out.append(resolved)
        elif idx == last_idx:
            out.append(last_dim_default)
        else:
            out.append(1)
    return tuple(out)


def _infer_stack_frames(obs_dim: int, *, fallback: int = 4) -> int:
    if obs_dim > 0 and obs_dim % RAW_OBS_DIM == 0:
        frames = obs_dim // RAW_OBS_DIM
        if frames > 0:
            return frames
    return fallback


def _fit_obs(obs: list[float], expected_dim: int) -> list[float]:
    if len(obs) < expected_dim:
        return obs + [0.0] * (expected_dim - len(obs))
    if len(obs) > expected_dim:
        return obs[-expected_dim:]
    return obs


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

    def __init__(self, model_path: str | Path, *, hidden_size: int = 128) -> None:
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
        self._obs_dim = _resolve_shape_dim(inputs[0].shape, 1, RAW_OBS_DIM)
        self.hidden_size = _resolve_shape_dim(inputs[1].shape, -1, hidden_size)
        self._h_shape = _materialize_shape(inputs[1].shape, last_dim_default=self.hidden_size)
        self._c_shape = _materialize_shape(inputs[2].shape, last_dim_default=self.hidden_size)

        stack_frames = _infer_stack_frames(self._obs_dim, fallback=1)
        self.frame_stack = (
            FrameStack(obs_dim=RAW_OBS_DIM, n_frames=stack_frames)
            if stack_frames > 1
            else None
        )

        self._h: np.ndarray | None = None
        self._c: np.ndarray | None = None

        logger.info(
            "ONNX LSTM agent loaded: %s (obs_dim=%s, hidden=%d, stack=%s, outputs=%d)",
            model_path,
            self._obs_dim,
            self.hidden_size,
            stack_frames,
            len(outputs),
        )

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        if self._h is None:
            self._h = np.zeros(self._h_shape, dtype=np.float32)
            self._c = np.zeros(self._c_shape, dtype=np.float32)

        raw_obs = build_obs(state, player=player)
        obs = self.frame_stack.push(raw_obs) if self.frame_stack is not None else raw_obs
        obs = _fit_obs(obs, self._obs_dim)
        obs_array = np.array([obs], dtype=np.float32)
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
        if self.frame_stack is not None:
            self.frame_stack.reset()


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

    def __init__(self, model_path: str | Path, *, det_size: int = 128) -> None:
        self.model_path = str(model_path)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])

        inputs = self.session.get_inputs()
        self._obs_name = inputs[0].name
        self._h_name = inputs[1].name
        self._act_name = inputs[2].name
        self._obs_dim = _resolve_shape_dim(inputs[0].shape, 1, RAW_OBS_DIM * 4)
        self.det_size = _resolve_shape_dim(inputs[1].shape, -1, det_size)
        self._h_shape = _materialize_shape(inputs[1].shape, last_dim_default=self.det_size)
        self._act_rank = len(inputs[2].shape)

        self._h: np.ndarray | None = None
        self._prev_act: int = 0
        stack_frames = _infer_stack_frames(self._obs_dim, fallback=4)
        self.frame_stack = (
            FrameStack(obs_dim=RAW_OBS_DIM, n_frames=stack_frames)
            if stack_frames > 1
            else None
        )

        logger.info(
            "ONNX DiscRSSM agent loaded: %s (obs_dim=%d, det=%d, stack=%s)",
            model_path,
            self._obs_dim,
            self.det_size,
            stack_frames,
        )

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        if self._h is None:
            self._h = np.zeros(self._h_shape, dtype=np.float32)

        raw_obs = build_obs(state, player=player)
        obs = self.frame_stack.push(raw_obs) if self.frame_stack is not None else raw_obs
        obs = _fit_obs(obs, self._obs_dim)
        obs_array = np.array([obs], dtype=np.float32)
        h_array = self._h
        if self._act_rank == 2:
            act_array = np.array([[self._prev_act]], dtype=np.int64)
        else:
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
        if self.frame_stack is not None:
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

        input_meta = self.session.get_inputs()[0]
        self._input_name = input_meta.name
        self._input_shape = input_meta.shape
        self._input_rank = len(self._input_shape)
        self.seq_len = _resolve_shape_dim(self._input_shape, -2, seq_len)
        self._obs_dim = _resolve_shape_dim(self._input_shape, -1, RAW_OBS_DIM * 4)
        self._buf: list[list[float]] = []
        stack_frames = _infer_stack_frames(self._obs_dim, fallback=4)
        self.frame_stack = (
            FrameStack(obs_dim=RAW_OBS_DIM, n_frames=stack_frames)
            if stack_frames > 1
            else None
        )

        logger.info(
            "ONNX Transformer agent loaded: %s (seq=%d, obs_dim=%d, stack=%s)",
            model_path,
            self.seq_len,
            self._obs_dim,
            stack_frames,
        )

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        raw_obs = build_obs(state, player=player)
        obs = self.frame_stack.push(raw_obs) if self.frame_stack is not None else raw_obs
        obs = _fit_obs(obs, self._obs_dim)
        self._buf.append(obs)
        if len(self._buf) > self.seq_len:
            self._buf.pop(0)

        # Pad with zeros if fewer than seq_len frames
        pad_count = self.seq_len - len(self._buf)
        padded = [[0.0] * self._obs_dim] * pad_count + self._buf
        obs_seq = np.array(padded, dtype=np.float32)
        if self._input_rank == 3:
            obs_seq = np.expand_dims(obs_seq, axis=0)

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
        if self.frame_stack is not None:
            self.frame_stack.reset()


def _softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    e = np.exp(x - np.max(x))
    return e / e.sum()
