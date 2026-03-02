#!/usr/bin/env bash
# start_training.sh — kills stale processes and launches the watchdog
N64=/Users/ichiropractic/code/n64
LOG=$N64/training/data/logs

# ── Deep clean ────────────────────────────────────────────────────────────────
echo "[start] Killing stale processes..."
pkill -9 -f watchdog.py         2>/dev/null
pkill -9 -f mk4_train_parallel  2>/dev/null
pkill -9 -f run_bridge_server   2>/dev/null
pkill -9 -f mupen64plus         2>/dev/null
sleep 3

echo "[start] Removing stale sockets and configs..."
rm -f $N64/training/data/bridge/*.sock
rm -rf $N64/.m64p/instances/
rm -rf "$HOME/Library/Application Support/mupen64plus/"
echo "[start] Clean done."
echo ""

# ── Launch watchdog (manages all 4 agents, auto-restarts crashes) ─────────────
PYTHONUNBUFFERED=1 nohup python3 $N64/training/scripts/watchdog.py \
    > $LOG/watchdog.log 2>&1 &
echo "Watchdog pid=$!"
echo "Watchdog managing all 4 agents. Logs: $LOG/watchdog.log"
echo "Check status: python3 $N64/training/scripts/check_training.py"
