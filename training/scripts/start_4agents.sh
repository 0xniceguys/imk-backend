#!/bin/bash
# start_4agents.sh — Launch all 4 agents directly (bypasses watchdog socket issue)
# Each agent runs mk4_train_parallel_cpu.py as a direct process.
# Workers handle their own bridge reconnection.

set -e
cd "$(dirname "$0")/../.."
N64_ROOT="$(pwd)"
LOG_DIR="$N64_ROOT/training/data/logs"
PYTHON="/opt/homebrew/bin/python3"
SCRIPT="$N64_ROOT/training/scripts/mk4_train_parallel_cpu.py"
AGENTS=("lstm" "obj_belief" "transformer" "disc_rssm")
EPISODES=25000
SAVE_EVERY=10
STAGGER=60

mkdir -p "$LOG_DIR"

# Kill any stale training processes
pkill -9 -f 'run_bridge_server' 2>/dev/null || true
pkill -9 -f 'mk4_train_parallel_cpu' 2>/dev/null || true
pkill -9 -f 'mupen64plus.*train-' 2>/dev/null || true
sleep 2
rm -f "$N64_ROOT"/training/data/bridge/mk4-train-*.sock /tmp/mk4_ctrl_* 2>/dev/null || true

echo "═══════════════════════════════════════"
echo " PHASE 1 — CPU Training (4 agents)"
echo "═══════════════════════════════════════"
echo " Savestate : arcade_training_scorpion.st"
echo " Episodes  : $EPISODES per agent"
echo " Save every: $SAVE_EVERY episodes"
echo " Stagger   : ${STAGGER}s between launches"
echo ""

PIDS=()
for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    log="$LOG_DIR/arch-${agent}.log"

    # Clean stale configs
    pkill -9 -f "train-${agent}" 2>/dev/null || true
    rm -f "$N64_ROOT/training/data/bridge/mk4-train-${agent}-0.sock" 2>/dev/null || true
    rm -rf "$N64_ROOT/.m64p/instances/train-${agent}-0" 2>/dev/null || true
    rm -f "$LOG_DIR/learner_heartbeat_${agent}" 2>/dev/null || true
    sleep 1

    echo "[launch] Starting $agent → $log"
    PYTHONUNBUFFERED=1 nohup $PYTHON "$SCRIPT" \
        --agent "$agent" \
        --run-id "$agent" \
        --workers 1 \
        --episodes "$EPISODES" \
        --save-every "$SAVE_EVERY" \
        > "$log" 2>&1 &
    PIDS+=($!)
    echo "[launch] $agent pid=${PIDS[-1]}"

    if [ $i -lt $((${#AGENTS[@]} - 1)) ]; then
        echo "[launch] waiting ${STAGGER}s before next agent..."
        sleep "$STAGGER"
    fi
done

echo ""
echo "[launch] All 4 agents running: ${PIDS[*]}"
echo "[launch] Monitor with: tail -f $LOG_DIR/arch-lstm.log"
echo ""

# Simple monitoring loop
while true; do
    sleep 60
    alive=0
    for i in "${!AGENTS[@]}"; do
        agent="${AGENTS[$i]}"
        pid="${PIDS[$i]}"
        if kill -0 "$pid" 2>/dev/null; then
            alive=$((alive + 1))
        else
            wait "$pid" 2>/dev/null
            rc=$?
            if [ $rc -eq 0 ]; then
                echo "[monitor] $agent (pid=$pid) completed ✓"
            else
                echo "[monitor] $agent (pid=$pid) died (rc=$rc), restarting..."
                log="$LOG_DIR/arch-${agent}.log"
                pkill -9 -f "train-${agent}" 2>/dev/null || true
                rm -f "$N64_ROOT/training/data/bridge/mk4-train-${agent}-0.sock" 2>/dev/null || true
                sleep 2
                PYTHONUNBUFFERED=1 nohup $PYTHON "$SCRIPT" \
                    --agent "$agent" --run-id "$agent" --workers 1 \
                    --episodes "$EPISODES" --save-every "$SAVE_EVERY" \
                    >> "$log" 2>&1 &
                PIDS[$i]=$!
                echo "[monitor] $agent restarted pid=${PIDS[$i]}"
                alive=$((alive + 1))
            fi
        fi
    done
    if [ $alive -eq 0 ]; then
        echo "[monitor] All agents done."
        break
    fi
done
