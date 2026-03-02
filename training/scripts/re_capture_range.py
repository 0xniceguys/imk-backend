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


def _parse_int(value: str) -> int:
    return int(value, 0)


_add_src_to_path()

from n64train.reverse.scanner import (  # noqa: E402
    AddressRange,
    BridgeMemoryScanner,
    reverse_capture_dir,
)
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a bridge RAM range snapshot for reverse engineering")
    parser.add_argument("label", help="Capture label (e.g. difficulty_max)")
    parser.add_argument("--socket-path", default="/Users/ichiropractic/code/n64/training/data/bridge/mk4.sock")
    parser.add_argument("--start", type=_parse_int, required=True, help="Range start address (hex or decimal)")
    parser.add_argument("--end", type=_parse_int, required=True, help="Range end address (exclusive)")
    parser.add_argument("--chunk-size", type=_parse_int, default=0x1000, help="Probe chunk size")
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=30.0,
        help="Bridge socket timeout (increase for first real RAM dump boot)",
    )
    parser.add_argument("--out-dir", default="", help="Output directory (defaults to training/data/reverse)")
    parser.add_argument("--task-id", default="", help="Reverse task id (metadata only)")
    parser.add_argument("--notes", default="", help="Notes stored in snapshot metadata")
    args = parser.parse_args()

    addr_range = AddressRange(start=args.start, end=args.end)
    out_dir = Path(args.out_dir) if args.out_dir else reverse_capture_dir()

    bridge = SocketEmulatorBridge(args.socket_path, timeout_sec=args.timeout_sec)
    scanner = BridgeMemoryScanner(bridge)
    try:
        snapshot = scanner.capture_range(
            label=args.label,
            addr_range=addr_range,
            chunk_size=args.chunk_size,
            metadata={
                "task_id": args.task_id,
                "notes": args.notes,
                "socket_path": args.socket_path,
            },
        )
        json_path, bin_path = snapshot.save(out_dir)
        print(
            json.dumps(
                {
                    "manifest": str(json_path),
                    "payload": str(bin_path),
                    "byte_len": len(snapshot.payload),
                    "sha256": snapshot.sha256,
                    "placeholder_ram_export": snapshot.bridge_status.get("placeholder_ram_export"),
                },
                indent=2,
            )
        )
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
