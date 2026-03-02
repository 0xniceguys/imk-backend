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


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a debugger command through the bridge memory backend")
    parser.add_argument("--socket-path", default="/Users/ichiropractic/code/n64/training/data/bridge/mk4.sock")
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=20.0,
        help="Bridge-side debugger command timeout",
    )
    parser.add_argument(
        "--output-tail-chars",
        type=int,
        default=4000,
        help="Return only the tail of command output (avoids giant JSON responses)",
    )
    parser.add_argument(
        "--sleep-after",
        type=float,
        default=0.0,
        help="Optional local sleep after the command completes (useful in shell sequences)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Debugger command to send, e.g. 'run' or 'pause' or 'frame 1'",
    )
    args = parser.parse_args()

    if not args.command:
        parser.error("Provide a debugger command after '--', e.g. re_debugger_ctl.py -- run")
    command_text = " ".join(args.command).strip()
    if not command_text:
        parser.error("Debugger command cannot be empty")

    bridge = SocketEmulatorBridge(args.socket_path, timeout_sec=max(args.timeout_sec + 5.0, 10.0))
    try:
        resp = bridge.debugger_command(
            command_text,
            timeout_sec=args.timeout_sec,
            output_tail_chars=args.output_tail_chars,
        )
        print(json.dumps(resp, indent=2))
        if args.sleep_after > 0:
            time.sleep(args.sleep_after)
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
