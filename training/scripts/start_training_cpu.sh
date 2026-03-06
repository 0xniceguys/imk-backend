#!/bin/bash
# start_training_cpu.sh — Launch Phase 1: Agent vs In-Game CPU
#
# Usage:
#   bash training/scripts/start_training_cpu.sh
#
# Starts 4 agents (lstm, obj_belief, transformer, disc_rssm) all training
# against MK4's built-in arcade CPU. No self-play. 25000 episodes each.
# Savestate: arcade_training_scorpion.st

set -euo pipefail

# Prevent stale .pyc bytecode from overriding source changes
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

cd "$(dirname "$0")/../.."   # cd to n64 root

# Clear pycache so worker.py changes are guaranteed to load
find training/src -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "[start-cpu] Killing stale processes..."
pkill -9 -f watchdog          2>/dev/null || true   # kill ALL watchdogs (old and new)
pkill -9 -f mk4_train_parallel 2>/dev/null || true  # kill ALL parallel trainers
pkill -9 -f run_bridge_server 2>/dev/null || true
pkill -9 -f mupen64plus       2>/dev/null || true
sleep 3

echo "[start-cpu] Removing stale sockets and configs..."
rm -f training/data/bridge/mk4-train-*.sock 2>/dev/null || true
rm -f /tmp/mk4_ctrl_*              2>/dev/null || true
rm -rf .m64p/instances/train-*     2>/dev/null || true
echo "[start-cpu] Clean done."
echo ""

# Check savestate exists
SAVESTATE="training/data/savestates/mk4_arcade/arcade_training_scorpion.st"
if [ ! -f "$SAVESTATE" ]; then
    echo "[start-cpu] ERROR: Savestate not found: $SAVESTATE"
    echo "[start-cpu] Please add arcade_training_scorpion.st to training/data/savestates/mk4_arcade/"
    exit 1
fi
echo "[start-cpu] Savestate: $SAVESTATE ✓"

mkdir -p training/data/logs

PYTHONUNBUFFERED=1 nohup /opt/homebrew/bin/python3 training/scripts/watchdog_cpu.py \
    > training/data/logs/watchdog_cpu.log 2>&1 &
WATCHDOG_PID=$!

echo "Watchdog pid=$WATCHDOG_PID"
echo "Phase 1 CPU training: 4 agents × 25000 episodes vs MK4 arcade AI"
echo "Logs: $(pwd)/training/data/logs/"
echo ""
echo "Monitor progress:  python3 training/scripts/check_training.py"
echo "Watch a live log:  tail -f training/data/logs/arch-lstm.log"
echo ""
echo "When Phase 1 is complete (win rate >85%), stop with:"
echo "  pkill -f watchdog_cpu.py"
echo "Then start Phase 2 (self-play):"
echo "  bash training/scripts/start_training.sh"
