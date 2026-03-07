from app.agents.onnx_agent import _fit_obs_dim, _infer_stack_frames


def test_infer_stack_frames_from_obs_dim():
    assert _infer_stack_frames(28, 4) == 2
    assert _infer_stack_frames(56, 4) == 4
    assert _infer_stack_frames(14, 4) == 1


def test_infer_stack_frames_fallback():
    assert _infer_stack_frames(None, 4) == 4
    assert _infer_stack_frames(30, 4) == 4


def test_fit_obs_dim_pad_and_trim():
    obs = [1.0, 2.0, 3.0]
    assert _fit_obs_dim(obs, 5) == [1.0, 2.0, 3.0, 0.0, 0.0]
    assert _fit_obs_dim(obs, 2) == [1.0, 2.0]
    assert _fit_obs_dim(obs, None) == [1.0, 2.0, 3.0]

