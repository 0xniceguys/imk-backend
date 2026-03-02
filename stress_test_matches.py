#!/usr/bin/env python3
"""
Safe stress test for IMK match streaming system.
Tests how many simultaneous matches can run without OOM or performance issues.

SAFETY FEATURES:
- Monitors RAM usage every second
- Auto-stops if RAM > 85% to prevent OOM
- Auto-stops if CPU > 95% sustained
- Gradual ramp-up (one match at a time)
- Graceful cleanup on exit
"""
import asyncio
import sys
import psutil
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "backend"))

# Safety thresholds
MAX_RAM_PERCENT = 85  # Stop if RAM usage exceeds this
MAX_CPU_PERCENT = 95  # Stop if sustained CPU exceeds this
CPU_CHECK_SAMPLES = 3  # Check CPU over this many samples
RAMP_UP_DELAY = 15  # Seconds between starting each match
MAX_MATCHES = 12  # Hard limit (safety cap)

class ResourceMonitor:
    """Monitor system resources in real-time."""

    def __init__(self):
        self.running = True
        self.cpu_samples = []

    def get_stats(self):
        """Get current resource usage."""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.5)

        return {
            'ram_used_gb': mem.used / (1024**3),
            'ram_total_gb': mem.total / (1024**3),
            'ram_percent': mem.percent,
            'ram_available_gb': mem.available / (1024**3),
            'cpu_percent': cpu,
        }

    def check_safety(self, stats):
        """Check if it's safe to continue."""
        # Check RAM
        if stats['ram_percent'] > MAX_RAM_PERCENT:
            return False, f"RAM usage too high: {stats['ram_percent']:.1f}% (max {MAX_RAM_PERCENT}%)"

        # Check sustained CPU
        self.cpu_samples.append(stats['cpu_percent'])
        if len(self.cpu_samples) > CPU_CHECK_SAMPLES:
            self.cpu_samples.pop(0)

        if len(self.cpu_samples) >= CPU_CHECK_SAMPLES:
            avg_cpu = sum(self.cpu_samples) / len(self.cpu_samples)
            if avg_cpu > MAX_CPU_PERCENT:
                return False, f"CPU usage too high: {avg_cpu:.1f}% sustained (max {MAX_CPU_PERCENT}%)"

        return True, "OK"


async def stress_test():
    """Run the stress test."""
    from app.services.match_runner import start_match, stop_match, get_all_runners

    print("=" * 70)
    print("🔥 IMK MATCH STREAMING STRESS TEST")
    print("=" * 70)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Show initial stats
    mem = psutil.virtual_memory()
    print(f"📊 Initial System Stats:")
    print(f"   CPU Cores: {psutil.cpu_count()}")
    print(f"   Total RAM: {mem.total / (1024**3):.1f} GB")
    print(f"   Available RAM: {mem.available / (1024**3):.1f} GB")
    print(f"   Used RAM: {mem.used / (1024**3):.1f} GB ({mem.percent:.1f}%)")
    print()

    print(f"⚙️  Safety Settings:")
    print(f"   Max RAM: {MAX_RAM_PERCENT}%")
    print(f"   Max CPU: {MAX_CPU_PERCENT}% sustained")
    print(f"   Max Matches: {MAX_MATCHES}")
    print(f"   Ramp-up delay: {RAMP_UP_DELAY}s between matches")
    print()

    # Find savestate
    savestate = Path("/home/ubuntu/imk/training/data/savestates/mk4_arcade/p1p2state.st")
    if not savestate.exists():
        print(f"❌ Savestate not found: {savestate}")
        return

    print(f"✓ Using savestate: {savestate}")
    print()

    monitor = ResourceMonitor()
    match_ids = []
    max_reached = 0

    try:
        print("🚀 Starting stress test...")
        print("-" * 70)

        for i in range(1, MAX_MATCHES + 1):
            match_id = f"stress-test-{i:02d}"

            # Check safety before starting
            stats = monitor.get_stats()
            safe, reason = monitor.check_safety(stats)

            if not safe:
                print()
                print(f"⚠️  SAFETY LIMIT REACHED: {reason}")
                print(f"   Stopping at {i-1} matches")
                max_reached = i - 1
                break

            # Start match
            print(f"\n[Match {i:2d}] Starting...")
            try:
                await start_match(
                    match_id=match_id,
                    savestate_path=str(savestate),
                    p1_agent_id="random",
                    p2_agent_id="random",
                    best_of=1,
                )
                match_ids.append(match_id)
                max_reached = i

                # Wait for stabilization
                await asyncio.sleep(3)

                # Check stats after start
                stats = monitor.get_stats()
                print(f"[Match {i:2d}] ✓ Running")
                print(f"           RAM: {stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} GB ({stats['ram_percent']:.1f}%)")
                print(f"           CPU: {stats['cpu_percent']:.1f}%")
                print(f"           Available: {stats['ram_available_gb']:.1f} GB")

            except Exception as e:
                print(f"[Match {i:2d}] ❌ Failed to start: {e}")
                max_reached = i - 1
                break

            # Wait before next match
            if i < MAX_MATCHES:
                print(f"           Waiting {RAMP_UP_DELAY}s before next match...")

                # Monitor during wait
                for sec in range(RAMP_UP_DELAY):
                    await asyncio.sleep(1)
                    stats = monitor.get_stats()
                    safe, reason = monitor.check_safety(stats)
                    if not safe:
                        print()
                        print(f"⚠️  SAFETY LIMIT REACHED: {reason}")
                        max_reached = i
                        break

                if not safe:
                    break

        # Final stats
        print()
        print("=" * 70)
        print("📈 FINAL RESULTS")
        print("=" * 70)

        runners = get_all_runners()
        stats = monitor.get_stats()

        print(f"✅ Successfully running: {len(runners)} matches")
        print(f"   Match IDs: {', '.join(runners.keys())}")
        print()
        print(f"📊 Final Resource Usage:")
        print(f"   RAM: {stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} GB ({stats['ram_percent']:.1f}%)")
        print(f"   Available: {stats['ram_available_gb']:.1f} GB")
        print(f"   CPU: {stats['cpu_percent']:.1f}%")
        print()

        # Calculate per-match overhead
        if len(runners) > 0:
            ram_per_match = (stats['ram_used_gb'] - 2.0) / len(runners)  # Subtract ~2GB for OS/backend
            cpu_per_match = stats['cpu_percent'] / len(runners)
            print(f"📉 Estimated Per-Match Usage:")
            print(f"   RAM: ~{ram_per_match * 1024:.0f} MB")
            print(f"   CPU: ~{cpu_per_match:.1f}%")
            print()

        # Recommendations
        print(f"💡 Recommendations:")
        total_cores = psutil.cpu_count()

        if stats['ram_percent'] < 50:
            rec_matches = min(int(total_cores * 0.75), max_reached + 2)
            print(f"   ✅ LOW RAM USAGE - Can handle more!")
            print(f"   Recommended: {rec_matches} simultaneous matches")
        elif stats['ram_percent'] < 70:
            rec_matches = max_reached
            print(f"   ✓ GOOD - This is a safe operating point")
            print(f"   Recommended: {rec_matches} simultaneous matches")
        else:
            rec_matches = max(1, max_reached - 1)
            print(f"   ⚠️  HIGH USAGE - Running close to limits")
            print(f"   Recommended: {rec_matches} simultaneous matches (leave headroom)")

        print()
        print(f"🎯 CONCLUSION: Your instance can safely handle {rec_matches} simultaneous matches")
        print()

        # Keep matches running briefly for manual inspection
        print("Matches will run for 30 seconds for inspection...")
        print("Press Ctrl+C to stop early")
        await asyncio.sleep(30)

    except KeyboardInterrupt:
        print()
        print("⏹️  Test interrupted by user")

    finally:
        # Cleanup
        print()
        print("🧹 Cleaning up...")

        for match_id in match_ids:
            try:
                print(f"   Stopping {match_id}...")
                await stop_match(match_id)
            except Exception as e:
                print(f"   Warning: Failed to stop {match_id}: {e}")

        await asyncio.sleep(2)

        # Final cleanup check
        stats = monitor.get_stats()
        print()
        print(f"✓ Cleanup complete")
        print(f"  Final RAM: {stats['ram_used_gb']:.1f} GB ({stats['ram_percent']:.1f}%)")
        print()
        print("=" * 70)
        print(f"Test completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(stress_test())
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
