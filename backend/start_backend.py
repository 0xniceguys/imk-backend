#!/usr/bin/env python3
"""
start_backend.py — properly starts the IMK backend in a new session
so the emulator child processes cannot suspend it via SIGTTOU.

Usage:
  cd /path/to/repo/backend
  python3 start_backend.py [--port 8000]
"""
import os
from pathlib import Path
import subprocess
import sys
import time

BACKEND_DIR = Path(__file__).resolve().parent
LOG_DIR = BACKEND_DIR / "logs"
LOG_FILE = LOG_DIR / "backend.log"


def _resolve_uvicorn_cmd() -> list[str]:
    override = os.environ.get("IMK_UVICORN_BIN", "").strip()
    if override:
        return [override, "app.main:app"]

    candidates = (
        BACKEND_DIR / ".venv" / "bin" / "uvicorn",
        BACKEND_DIR.parent / ".venv" / "bin" / "uvicorn",
    )
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), "app.main:app"]

    return [sys.executable, "-m", "uvicorn", "app.main:app"]


def _parse_port(argv: list[str]) -> int:
    port = 8000
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
    return port


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    port = _parse_port(args)

    env = os.environ.copy()
    env["DEV_ADMIN_BYPASS"] = "true"

    LOG_DIR.mkdir(exist_ok=True)

    with LOG_FILE.open("w", encoding="utf-8") as log:
        p = subprocess.Popen(
            [
                *_resolve_uvicorn_cmd(),
                "--host", "0.0.0.0",
                "--port", str(port),
                "--log-level", "info",
            ],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from controlling terminal → no SIGTTOU
        )

    print(f"Backend started: PID={p.pid} port={port}")
    print(f"Log: {LOG_FILE}")
    print(f"Health: http://localhost:{port}/health")

    time.sleep(3)
    if p.poll() is None:
        print("✅ Backend is running")
        return 0

    print(f"❌ Backend exited with code {p.returncode}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
