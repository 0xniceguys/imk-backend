#!/usr/bin/env python3
"""Run mk4_probe with a fixed action: hk."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ACTION = 'hk'


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    cmd = [sys.executable, str(root / 'training/scripts/mk4_probe.py'), '--action', ACTION]
    cmd.extend(sys.argv[1:])
    return subprocess.call(cmd)


if __name__ == '__main__':
    raise SystemExit(main())
