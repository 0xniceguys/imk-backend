#!/usr/bin/env python3
"""
Manual cleanup script for IMK.

Kills all orphaned processes, cleans up stale sockets, and removes zombie processes.
Safe to run at any time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.process_manager import full_cleanup

if __name__ == "__main__":
    print("🧹 IMK Cleanup Script")
    print("=" * 50)
    print()

    stats = full_cleanup()

    print()
    print("=" * 50)
    print("Cleanup Results:")
    print(f"  Processes killed: {stats['processes_killed']}")
    print(f"  Displays killed: {stats['displays_killed']}")
    print(f"  Sockets removed: {stats['sockets_removed']}")
    print()

    if sum(stats.values()) == 0:
        print("✅ No cleanup needed - system is clean!")
    else:
        print("✅ Cleanup complete!")
