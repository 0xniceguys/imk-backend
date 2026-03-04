---
description: Agent operating rules for IMK project — read this every session
---

# IMK Agent Rules

## 🚨 RULE #1 — TERMINAL: NEVER WAIT > 3 SECONDS

### Exact tool parameters — ALWAYS:
- `WaitMsBeforeAsync`: **1000** (1 second max, always send to background)
- `WaitDurationSeconds`: **0** (NEVER block on command_status)

### Commands that WILL cause waiting — ALWAYS use `&` suffix or background:
| Command | Why it waits | Fix |
|---|---|---|
| `sleep N` | Waits N seconds | NEVER use sleep in commands |
| `pip install ...` | Network + compile, can take minutes | Always `pip install ... > /tmp/pip.log 2>&1 &` |
| `start_backend.py` | Has internal `time.sleep(3)` | Always `python start_backend.py > /tmp/br.log 2>&1 &` |
| `curl http://...` | Network, can hang on closed port | Always `curl ... > /tmp/out.log 2>&1 &` |
| `pkill && nextcmd` | Chained: kill can wait for process death | Use separate commands |
| `fuser -k 8000/tcp` | Can wait for process to die | Add `2>/dev/null; true` |
| `command_status WaitDurationSeconds>0` | Explicitly blocks | Always use `WaitDurationSeconds: 0` |
| `WaitMsBeforeAsync > 1000` | Waits in foreground | Max 1000ms |

### Check results by reading log files, not waiting:
```bash
# Fire:  some_command > /tmp/x.log 2>&1 &
# Check: cat /tmp/x.log  (or view_file)
# Never: wait for the command to finish
```

## 📋 Background Task Tracking

Track long commands in `/tmp/` log files:
```bash
some_long_command > /tmp/task_name.log 2>&1 &
# Check later:
tail /tmp/task_name.log
```