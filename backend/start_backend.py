#!/usr/bin/env python3
"""
start_backend.py — properly starts the IMK backend in a new session
so the emulator child processes cannot suspend it via SIGTTOU.

Usage:
  cd /Users/ichiropractic/code/n64/backend
  python3 start_backend.py [--port 8000]
"""
import subprocess
import os
import sys
import time
import signal

PORT = 8000
for i, arg in enumerate(sys.argv):
    if arg == "--port" and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])

env = os.environ.copy()
env["DEV_ADMIN_BYPASS"] = "true"

LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "backend.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

with open(LOG_FILE, "w") as log:
    p = subprocess.Popen(
        [
            ".venv/bin/uvicorn", "app.main:app",
            "--host", "0.0.0.0",
            "--port", str(PORT),
            "--log-level", "warning",
        ],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # detach from controlling terminal → no SIGTTOU
    )

print(f"Backend started: PID={p.pid} port={PORT}")
print(f"Log: {LOG_FILE}")
print(f"Health: http://localhost:{PORT}/health")

# Wait 3s and confirm it started
time.sleep(3)
if p.poll() is None:
    print("✅ Backend is running")
else:
    print(f"❌ Backend exited with code {p.returncode}")
    sys.exit(1)
