import struct
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.actions import Button, ControllerState
from app.services.match_runner import MatchRunner

try:
    from app import admin_views
except ModuleNotFoundError:
    admin_views = None


def _read_ctrl(path: str) -> tuple[int, int, int]:
    raw = Path(path).read_bytes()
    return struct.unpack("<Hbb", raw)


class MatchRunnerManualControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_controller_state_writes_and_releases(self) -> None:
        runner = MatchRunner(match_id="viewer-test", savestate_path="dummy.st")
        with tempfile.TemporaryDirectory() as tmpdir:
            ctrl_p1 = str(Path(tmpdir) / "p1.ctrl")
            ctrl_p2 = str(Path(tmpdir) / "p2.ctrl")
            runner._ctrl_p1_path = ctrl_p1
            runner._ctrl_p2_path = ctrl_p2

            await runner.set_manual_controller_state(
                1,
                ControllerState(
                    analog_x=0.5,
                    analog_y=-0.25,
                    pressed=frozenset({Button.A, Button.D_RIGHT}),
                ),
            )

            self.assertTrue(runner.manual_control_payload()["p1"]["enabled"])
            mask, x_axis, y_axis = _read_ctrl(ctrl_p1)
            self.assertEqual(mask, (1 << 0) | (1 << 7))
            self.assertEqual(x_axis, 40)
            self.assertEqual(y_axis, -20)

            await runner.release_manual_controls(1, disable=False)
            self.assertTrue(runner.manual_control_payload()["p1"]["enabled"])
            self.assertEqual(runner.manual_control_payload()["p1"]["pressed"], [])
            self.assertEqual(_read_ctrl(ctrl_p1), (0, 0, 0))


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def set_manual_controller_state(self, player: int, controller_state: ControllerState, *, enable: bool = True):
        self.calls.append(("controller", player, controller_state, enable))
        return {
            "p1": {
                "enabled": enable if player == 1 else False,
                "pressed": sorted(button.value for button in controller_state.pressed) if player == 1 else [],
                "analog_x": controller_state.analog_x if player == 1 else 0.0,
                "analog_y": controller_state.analog_y if player == 1 else 0.0,
            },
            "p2": {
                "enabled": False,
                "pressed": [],
                "analog_x": 0.0,
                "analog_y": 0.0,
            },
        }


@unittest.skipIf(admin_views is None, "admin view dependencies are not installed in this environment")
class ViewerControlEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_controller_endpoint_decodes_and_forwards_state(self) -> None:
        fake_runner = _FakeRunner()
        request_payload = {
            "player": 1,
            "enabled": True,
            "analog_x": 0.25,
            "analog_y": -0.5,
            "pressed": ["A", "D_RIGHT", "Z"],
        }

        async def _json():
            return request_payload

        request = SimpleNamespace(cookies={"imk_admin": "admin"}, json=_json)

        with patch("app.admin_views.get_runner", return_value=fake_runner):
            response = await admin_views.viewer_control_controller(request, "test-match")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(len(fake_runner.calls), 1)
        _, player, controller_state, enabled = fake_runner.calls[0]
        self.assertEqual(player, 1)
        self.assertTrue(enabled)
        self.assertEqual(controller_state.analog_x, 0.25)
        self.assertEqual(controller_state.analog_y, -0.5)
        self.assertEqual(
            controller_state.pressed,
            frozenset({Button.A, Button.D_RIGHT, Button.Z}),
        )


if __name__ == "__main__":
    unittest.main()
