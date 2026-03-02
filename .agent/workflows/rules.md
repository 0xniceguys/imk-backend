---
description: Agent operating rules for IMK project — read this every session
---

# IMK Agent Rules

## 🚨 RULE #1 — TERMINAL: NEVER WAIT > 5 SECONDS

- `WaitMsBeforeAsync` = **3000 max** on ALL run_command calls. No exceptions.
- `WaitDurationSeconds` = **0** on ALL command_status calls. Never block.
- Long commands: pipe output to `/tmp/task.log 2>&1` and check with `tail /tmp/task.log` later
- curl, pip install, sleep, restart — ALL follow this rule

## 🎮 Emulator / Display

- **Savestate to use**: `p1p2state.st` at `/home/ubuntu/imk/training/data/savestates/mk4_arcade/p1p2state.st`
  - This is mid-fight — no intro skip needed
  - Do NOT use `_round_start` savestates (they cause black screen from intro animation)
- **z64 window name on Xvfb**: `Z64gl` (not "Mupen64Plus")
- **Xvfb display**: `:99` for match instances
- **FFmpeg captures**: `:99` at 640x480 via x11grab

## 🌐 Networking

- **Domain**: `https://immortalkombat.mercle.ai`
- **Reverse proxy**: nginx (`/etc/nginx/sites-enabled/imk.conf`) — NOT Caddy
- **Backend port**: 8000
- **WebSocket**: `/ws/*` routed through nginx with upgrade headers

## 🐍 Python / Venv

- **Running venv**: `/home/ubuntu/imk/.venv` (root project venv)
- **Start backend**: `cd /home/ubuntu/imk/backend && /home/ubuntu/imk/.venv/bin/python start_backend.py`
- **Kill port**: `fuser -k 8000/tcp`
- **Backend log**: `/home/ubuntu/imk/backend/logs/backend.log`

## 🧪 Testing

- No browser — use `curl` only
- Health check: `curl -s http://localhost:8000/health`
- Live streams: `curl -s http://localhost:8000/api/stream/live`
- Frame check: `curl -s http://localhost:8000/api/stream/{match_id}/frame -o /tmp/frame.jpg`

## 📋 Background Task Tracking

Track long commands in `/tmp/` log files:
```bash
some_long_command > /tmp/task_name.log 2>&1 &
# Check later:
tail /tmp/task_name.log
```
