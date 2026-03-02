#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.actions import ControllerState, MacroAction  # noqa: E402
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402
from n64train.runtime.types import ActionPacket, ResetSpec, SpeedMode  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the local MK4 bridge")
    parser.add_argument(
        "--socket-path",
        default="/Users/ichiropractic/code/n64/training/data/bridge/mk4.sock",
    )
    parser.add_argument("--steps", type=int, default=3, help="Number of single-frame bridge steps")
    parser.add_argument("--terminate", action="store_true", help="Send TERMINATE at the end")
    args = parser.parse_args()

    bridge = SocketEmulatorBridge(args.socket_path)
    try:
        hello = bridge.hello()
        print(json.dumps({"hello": hello.payload, "status": hello.status.to_payload()}, indent=2))
        bridge.set_speed_mode(SpeedMode.TRAIN_TURBO)
        obs = bridge.reset_match(ResetSpec())
        print(
            json.dumps(
                {
                    "reset": {
                        "episode_id": obs.timing.episode_id,
                        "frame_id": obs.timing.emulator_frame_id,
                        "meta": obs.meta_context,
                    }
                },
                indent=2,
            )
        )
        for idx in range(args.steps):
            result = bridge.step(
                ActionPacket(
                    macro_action=MacroAction.ADVANCE,
                    micro_controller_state=ControllerState(),
                    repeat_frames=1,
                )
            )
            print(
                json.dumps(
                    {
                        "step": idx + 1,
                        "frame_id": result.observation.timing.emulator_frame_id,
                        "reward": result.reward_terms.scalar(),
                        "events": [event.name for event in result.events],
                        "info": result.info,
                    },
                    indent=2,
                )
            )
        ram = bridge.get_ram_features()
        print(json.dumps({"ram": ram}, indent=2))
        if args.terminate:
            bridge.terminate_server()
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
