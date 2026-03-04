#!/usr/bin/env bash
# start_training.sh — kills stale processes and launches the self-play watchdog

set -euo pipefail

cd "$(dirname "$0")/../.."

N64="$(pwd)"
LOG="$N64/training/data/logs"

echo "[start] Killing stale processes..."
pkill -9 -f watchdog.py        2>/dev/null || true
pkill -9 -f mk4_train_parallel 2>/dev/null || true
pkill -9 -f run_bridge_server  2>/dev/null || true
pkill -9 -f mupen64plus        2>/dev/null || true
sleep 3

echo "[start] Removing stale sockets and configs..."
rm -f "$N64"/training/data/bridge/*.sock 2>/dev/null || true
rm -rf "$N64/.m64p/instances/" 2>/dev/null || true
rm -rf "$HOME/Library/Application Support/mupen64plus/" 2>/dev/null || true
echo "[start] Clean done."
echo ""

mkdir -p "$LOG"

PYTHONUNBUFFERED=1 nohup python3 "$N64/training/scripts/watchdog.py" \
    > "$LOG/watchdog.log" 2>&1 &
echo "Watchdog pid=$!"
echo "Watchdog managing all 4 agents. Logs: $LOG/watchdog.log"
echo "Check status: python3 $N64/training/scripts/check_training.py"
