#!/usr/bin/env bash
# launch_all_archs.sh — Launch ALL 8 architecture training jobs simultaneously
#
# Each runs in TURBO mode with isolated sockets/ctrl/cfg/log paths.
#
# Defaults: 2 workers per architecture (16 total emulators)

N64_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$N64_ROOT/training/scripts/mk4_train_parallel.py"
LOG_DIR="$N64_ROOT/training/data/logs"
mkdir -p "$LOG_DIR"

EPISODES="${EPISODES:-200}"
WPA="${WORKERS_PER_ARCH:-2}"

echo "=================================================="
echo "  MK4 ALL-8 Architecture Training"
echo "  $(date)"
echo "  Episodes per arch : $EPISODES"
echo "  Workers per arch  : $WPA"
echo "  Total emulators   : $((WPA * 8))"
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
    sleep 3   # stagger to avoid simultaneous socket creation
}

# ── 8 architectures ──────────────────────────────────────
launch mlp            mlp
launch lstm           lstm
launch gru            gru
launch cont_rssm      cont_rssm
launch disc_rssm      disc_rssm
launch transformer    transformer
launch obj_belief     obj_belief
launch latent_planner latent_planner

echo ""
echo "=================================================="
echo "  All 8 architectures launched!"
echo "  PIDs: ${PIDS[*]}"
echo ""
echo "  Dashboard : http://localhost:7860"
echo ""
echo "  Tail logs :"
for RUN_ID in mlp lstm gru cont_rssm disc_rssm transformer obj_belief latent_planner; do
    echo "    tail -f $LOG_DIR/arch-${RUN_ID}.log"
done
echo ""
echo "  To stop all : kill ${PIDS[*]}"
echo "  Or          : pkill -f mk4_train_parallel"
echo "=================================================="

# Wait for all (Ctrl+C to stop)
wait "${PIDS[@]}"
echo "[launcher] All training jobs finished."
