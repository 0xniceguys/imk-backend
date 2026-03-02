#!/bin/bash
# mk4_stress_test.sh — Full 16-emulator stress test
#
# Tests ALL 8 architectures × 2 workers = 16 emulators simultaneously.
# Hard kill after TIMEOUT_SECS (default 300s = 5 minutes).
# Each arch runs only 2 episodes per worker to complete quickly.
#
# Usage:
#   chmod +x training/scripts/mk4_stress_test.sh
#   ./training/scripts/mk4_stress_test.sh
#
# Output:
#   training/data/logs/stress_test/arch-*.log  (per-arch logs)
#   training/data/logs/stress_test/summary.log (start/stop times)

set -u
N64_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$N64_ROOT/training/scripts/mk4_train_parallel.py"
LOG_DIR="$N64_ROOT/training/data/logs/stress_test"
TIMEOUT_SECS="${TIMEOUT_SECS:-300}"   # 5 minutes
EPISODES="${EPISODES:-2}"             # 2 episodes per worker — very fast
WPA="${WORKERS_PER_ARCH:-2}"          # 2 workers per arch = 16 total

mkdir -p "$LOG_DIR"

TEST_START=$(date +%s)
echo "=================================================="
echo "  MK4 STRESS TEST — $(date)"
echo "  Architectures  : 8"
echo "  Workers/arch   : $WPA  (total: $((WPA * 8)) emulators)"
echo "  Episodes/worker: $EPISODES"
echo "  Timeout        : ${TIMEOUT_SECS}s"
echo "  Logs           : $LOG_DIR"
echo "=================================================="
echo ""

# Write summary header
SUMMARY="$LOG_DIR/summary.log"
echo "STRESS TEST — $(date)" > "$SUMMARY"
echo "TIMEOUT=${TIMEOUT_SECS}s  WPA=$WPA  EPISODES=$EPISODES" >> "$SUMMARY"
echo "" >> "$SUMMARY"

PIDS=()
ARCHS=(mlp lstm gru cont_rssm disc_rssm transformer obj_belief latent_planner)

launch() {
    local AGENT="$1"
    local START_T=$(date +%s)
    python3 "$SCRIPT" \
        --agent   "$AGENT" \
        --run-id  "stress-${AGENT}" \
        --workers "$WPA" \
        --episodes "$EPISODES" \
        --save-every 99 \
        > "$LOG_DIR/arch-${AGENT}.log" 2>&1 &
    echo $!
}

echo "[stress] Launching all 8 architecture jobs..."
for ARCH in "${ARCHS[@]}"; do
    PID=$(launch "$ARCH")
    PIDS+=($PID)
    echo "  [${ARCH}] pid=${PID}" | tee -a "$SUMMARY"
    sleep 2  # stagger by 2s to avoid simultaneous socket creation
done

echo ""
echo "[stress] All launched. Monitoring for ${TIMEOUT_SECS}s..."
echo "[stress] Kill at: $(date -v +${TIMEOUT_SECS}S 2>/dev/null || date -d "+${TIMEOUT_SECS} seconds" 2>/dev/null || echo 'N/A')"
echo ""

# Monitor loop — print live tail every 15s and check if all done
ELAPSED=0
DONE_COUNT=0
while [ $ELAPSED -lt $TIMEOUT_SECS ] && [ ${#PIDS[@]} -gt 0 ]; do
    sleep 15
    ELAPSED=$(($(date +%s) - TEST_START))

    # Check which pids are still alive
    STILL_RUNNING=0
    for PID in "${PIDS[@]}"; do
        if kill -0 "$PID" 2>/dev/null; then
            STILL_RUNNING=$((STILL_RUNNING + 1))
        fi
    done

    echo "[stress] t=${ELAPSED}s  running=${STILL_RUNNING}/8 arch-jobs"

    # Print last line from each log
    for ARCH in "${ARCHS[@]}"; do
        LOG="$LOG_DIR/arch-${ARCH}.log"
        if [ -f "$LOG" ]; then
            LAST=$(tail -1 "$LOG" 2>/dev/null)
            [ -n "$LAST" ] && echo "  ${ARCH}: $LAST"
        fi
    done

    if [ $STILL_RUNNING -eq 0 ]; then
        echo "[stress] All jobs finished early at t=${ELAPSED}s ✅"
        break
    fi
done

ELAPSED=$(($(date +%s) - TEST_START))

# Hard kill anything still running
echo ""
echo "[stress] Time's up (t=${ELAPSED}s). Killing all remaining processes..."
for PID in "${PIDS[@]}"; do
    if kill -0 "$PID" 2>/dev/null; then
        kill -TERM "$PID" 2>/dev/null
        echo "  killed pid=$PID"
    fi
done

# Also kill any orphan mupen processes from this test
sleep 2
pkill -f "stress-" 2>/dev/null || true

echo ""
echo "=================================================="
echo "  STRESS TEST DONE — elapsed=${ELAPSED}s"
echo "  Run analysis: python3 training/scripts/mk4_analyze_results.py --mode stress"
echo "=================================================="
echo ""
echo "TOTAL_ELAPSED=${ELAPSED}s" >> "$SUMMARY"
