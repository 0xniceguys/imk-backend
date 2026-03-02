#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _add_src_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent / "src"
    sys.path.insert(0, str(src_dir))


_add_src_to_path()

from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402
from n64train.runtime.memory import MemoryProbe  # noqa: E402


def _parse_probe(value: str) -> MemoryProbe:
    # NAME:ADDR:SIZE, addr can be hex
    try:
        name, addr, size = value.split(":", 2)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("probe format must be NAME:ADDR:SIZE") from exc
    return MemoryProbe(name=name, address=int(addr, 0), size=int(size, 0))


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll bridge traced state and RAM probe bytes")
    parser.add_argument("--socket-path", default="/Users/ichiropractic/code/n64/training/data/bridge/mk4.sock")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--probe", action="append", default=[], type=_parse_probe)
    args = parser.parse_args()

    bridge = SocketEmulatorBridge(args.socket_path)
    try:
        for idx in range(args.count):
            obs = bridge.latest_observation()
            ram = bridge.get_ram_features(args.probe or None)
            print(
                json.dumps(
                    {
                        "sample": idx + 1,
                        "frame_id": obs.timing.emulator_frame_id,
                        "episode_id": obs.timing.episode_id,
                        "meta": obs.meta_context,
                        "traced_state": ram.get("traced_state"),
                        "placeholder_ram_export": ram.get("placeholder_ram_export"),
                        "probe_bytes_b64": ram.get("probe_bytes_b64", {}),
                    },
                    indent=2,
                )
            )
            if idx + 1 < args.count:
                time.sleep(max(0.0, args.interval))
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
