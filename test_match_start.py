#!/usr/bin/env python3
"""
Test script to verify match runner can start and stream video.
Usage: python3 test_match_start.py
"""
import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

async def test_match_start():
    """Start a test match and verify streaming."""
    from app.services.match_runner import start_match, get_runner, stop_match
    from app.services.emulator import REPO_ROOT

    # Find a savestate
    savestate_dir = REPO_ROOT / "training" / "data" / "savestates"
    savestates = list(savestate_dir.rglob("*.st"))

    if not savestates:
        print(f"❌ No savestates found in {savestate_dir}")
        return False

    savestate = str(savestates[0])
    print(f"✓ Using savestate: {savestate}")

    test_match_id = "test-match-001"

    try:
        # Start match
        print(f"\n🚀 Starting match {test_match_id}...")
        runner = await start_match(
            match_id=test_match_id,
            savestate_path=savestate,
            p1_agent_id="random",
            p2_agent_id="random",
            best_of=1,
        )

        print(f"✓ Match runner created: {runner.state}")

        # Wait for runner to stabilize
        await asyncio.sleep(5)

        # Check runner status
        runner = get_runner(test_match_id)
        if not runner:
            print("❌ Runner not found in registry!")
            return False

        print(f"✓ Runner state: {runner.state}")
        print(f"✓ Latest frame size: {len(runner.latest_frame) if runner.latest_frame else 0} bytes")
        print(f"✓ Frame capture running: {runner._frame_capture._running if runner._frame_capture else False}")
        print(f"✓ P1 Health: {runner.latest_snapshot.p1_health}")
        print(f"✓ P2 Health: {runner.latest_snapshot.p2_health}")

        if runner.latest_frame:
            print(f"\n✅ SUCCESS! Video frames are being captured!")
            print(f"   Frame size: {len(runner.latest_frame) / 1024:.1f} KB")
        else:
            print(f"\n⚠️  WARNING: No frames captured yet (might need more time)")

        # Let it run for a bit
        print("\n⏳ Running for 10 seconds to verify stability...")
        await asyncio.sleep(10)

        # Check final status
        runner = get_runner(test_match_id)
        if runner and runner.latest_frame:
            print(f"✅ CONFIRMED: Match is running and streaming!")
            print(f"   Frames: {runner.latest_snapshot.frame_id}")
            print(f"   Latest frame: {len(runner.latest_frame) / 1024:.1f} KB")

        # Cleanup
        print(f"\n🛑 Stopping match...")
        await stop_match(test_match_id)
        await asyncio.sleep(2)

        print(f"\n✅ Test complete!")
        return True

    except Exception as e:
        import traceback
        print(f"\n❌ Test failed: {e}")
        print(traceback.format_exc())

        # Try to cleanup
        try:
            await stop_match(test_match_id)
        except:
            pass

        return False

if __name__ == "__main__":
    result = asyncio.run(test_match_start())
    sys.exit(0 if result else 1)
