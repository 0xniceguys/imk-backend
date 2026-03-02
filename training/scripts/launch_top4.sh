#!/usr/bin/env bash
# launch_top4.sh — Launch top 4 architecture training jobs
# lstm, obj_belief, transformer, disc_rssm
# 2 workers each = 8 emulators total

N64_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$N64_ROOT/training/scripts/mk4_train_parallel.py"
LOG_DIR="$N64_ROOT/training/data/logs"
mkdir -p "$LOG_DIR"

EPISODES="${EPISODES:-500}"
WPA="${WORKERS_PER_ARCH:-1}"

echo "=================================================="
echo "  MK4 Top-4 Architecture Training"
echo "  $(date)"
echo "  Episodes per arch : $EPISODES"
echo "  Workers per arch  : $WPA  (1 screen per agent)"
echo "  Total emulators   : $((WPA * 4))"
echo "=================================================="

PIDS=()

launch() {
    local AGENT="$1"
    local RUN_ID="$2"
    local WORKERS="${3:-$WPA}"
    echo "[launcher] Starting $AGENT (run-id=$RUN_ID, $WORKERS workers)..."
    python3 "$SCRIPT" \
        --agent   "$AGENT" \
        --run-id  "$RUN_ID" \
        --workers "$WORKERS" \
        --episodes "$EPISODES" \
        --save-every 10 \
        > "$LOG_DIR/arch-${RUN_ID}.log" 2>&1 &
    local PID=$!
    PIDS+=($PID)
    echo "  → pid=$PID  log=$LOG_DIR/arch-${RUN_ID}.log"
    sleep 4   # stagger emulator socket creation
}

# ── 4 best architectures ──────────────────────────────────
launch lstm          lstm
launch obj_belief    obj_belief
launch transformer   transformer
launch disc_rssm     disc_rssm

echo ""
echo "=================================================="
echo "  4 agents launched!"
echo "  PIDs: ${PIDS[*]}"
echo ""
echo "  Tail all logs:"
echo "    tail -f $LOG_DIR/arch-lstm.log"
echo "    tail -f $LOG_DIR/arch-obj_belief.log"
echo "    tail -f $LOG_DIR/arch-transformer.log"
echo "    tail -f $LOG_DIR/arch-disc_rssm.log"
echo ""
echo "  To stop all : pkill -f mk4_train_parallel"
echo "=================================================="

# Keep script alive — Ctrl+C kills all children
trap 'echo "[launcher] Stopping all..."; kill "${PIDS[@]}" 2>/dev/null; pkill -f mupen64plus; pkill -f run_bridge_server; exit 0' INT TERM

wait "${PIDS[@]}"
echo "[launcher] All training jobs finished."
