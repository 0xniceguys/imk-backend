"""
Process Manager - Handles cleanup and monitoring of emulator processes.

Ensures no orphaned processes, proper cleanup, and graceful shutdown.
"""
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

# Track all managed PIDs globally
_managed_pids: Set[int] = set()


def register_pid(pid: int) -> None:
    """Register a PID for tracking."""
    _managed_pids.add(pid)
    logger.debug(f"Registered PID {pid} for tracking")


def unregister_pid(pid: int) -> None:
    """Unregister a PID."""
    _managed_pids.discard(pid)
    logger.debug(f"Unregistered PID {pid}")


def kill_process_tree(pid: int, timeout: float = 5.0) -> bool:
    """
    Kill a process and all its children.

    Returns True if successfully killed, False otherwise.
    """
    try:
        # Get all child processes
        children = _get_children(pid)

        # Send SIGTERM to parent
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to process {pid}")
        except ProcessLookupError:
            logger.warning(f"Process {pid} already gone")
            return True
        except PermissionError:
            logger.error(f"Permission denied killing process {pid}")
            return False

        # Send SIGTERM to all children
        for child_pid in children:
            try:
                os.kill(child_pid, signal.SIGTERM)
                logger.debug(f"Sent SIGTERM to child process {child_pid}")
            except (ProcessLookupError, PermissionError):
                pass

        # Wait for graceful shutdown
        import time
        wait_time = 0
        while wait_time < timeout:
            if not _process_exists(pid):
                logger.info(f"Process {pid} terminated gracefully")
                unregister_pid(pid)
                return True
            time.sleep(0.1)
            wait_time += 0.1

        # Force kill if still alive
        logger.warning(f"Process {pid} didn't terminate gracefully, force killing")
        try:
            os.kill(pid, signal.SIGKILL)
            for child_pid in _get_children(pid):
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except ProcessLookupError:
            pass

        unregister_pid(pid)
        return True

    except Exception as e:
        logger.error(f"Error killing process tree for {pid}: {e}")
        return False


def cleanup_orphaned_processes() -> int:
    """
    Find and kill orphaned emulator and bridge processes OWNED BY THIS APP.

    Only kills processes that are registered in our _managed_pids set.
    This prevents killing unrelated mupen64plus instances on shared hosts.

    Returns number of processes killed.
    """
    killed_count = 0

    # Only clean up processes we explicitly registered
    registered_pids = list(_managed_pids)

    if not registered_pids:
        logger.debug("No registered processes to clean up")
        return 0

    logger.info(f"Checking {len(registered_pids)} registered processes for cleanup")

    for pid in registered_pids:
        try:
            # Check if process still exists
            if _process_exists(pid):
                logger.warning(f"Cleaning up orphaned registered process: {pid}")
                if kill_process_tree(pid):
                    killed_count += 1
            else:
                # Process already dead, just unregister
                unregister_pid(pid)
        except Exception as e:
            logger.error(f"Error cleaning up process {pid}: {e}")

    # NOTE: We DO NOT search for arbitrary bridge_server or zombie processes.
    # Only processes explicitly registered via register_pid() are cleaned up.
    # This prevents killing unrelated processes on shared hosts.

    if killed_count > 0:
        logger.info(f"Cleaned up {killed_count} orphaned/zombie processes")

    return killed_count


def cleanup_orphaned_displays() -> int:
    """
    Clean up Xvfb displays for registered processes only.

    NOTE: We DO NOT kill arbitrary Xvfb processes.
    Xvfb processes are children of registered emulator processes
    and will be cleaned up via kill_process_tree().

    Returns number of displays killed.
    """
    killed_count = 0

    # Xvfb processes are already handled by kill_process_tree()
    # when we clean up the parent emulator processes.
    # No additional cleanup needed.
    logger.debug("Xvfb cleanup handled by process tree cleanup")
    return killed_count

def _old_cleanup_orphaned_displays_dangerous() -> int:
    """
    DEPRECATED: This function is too aggressive and kills ALL Xvfb processes.
    Kept for reference only - DO NOT USE.
    """
    killed_count = 0

    try:
        result = subprocess.run(
            ["pgrep", "-f", "Xvfb"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout.strip():
            pids = [int(pid) for pid in result.stdout.strip().split('\n')]
            logger.info(f"Found {len(pids)} Xvfb processes")
            for pid in pids:
                if kill_process_tree(pid):
                    killed_count += 1
    except Exception as e:
        logger.error(f"Error cleaning Xvfb displays: {e}")

    if killed_count > 0:
        logger.info(f"Cleaned up {killed_count} orphaned Xvfb displays")

    return killed_count


def cleanup_stale_sockets() -> int:
    """
    Remove stale Unix sockets.

    Returns number of sockets removed.
    """
    removed_count = 0
    socket_dirs = [
        Path("/home/ubuntu/imk/training/data/bridge"),
        Path("/tmp/imk"),
    ]

    for socket_dir in socket_dirs:
        if not socket_dir.exists():
            continue

        for socket_file in socket_dir.glob("**/*.sock"):
            try:
                # Check if process is using this socket
                result = subprocess.run(
                    ["lsof", str(socket_file)],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if not result.stdout.strip():
                    # No process using it, remove it
                    socket_file.unlink()
                    removed_count += 1
                    logger.info(f"Removed stale socket: {socket_file}")
            except Exception as e:
                logger.debug(f"Error checking socket {socket_file}: {e}")

    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} stale sockets")

    return removed_count


def full_cleanup() -> dict:
    """
    Perform full system cleanup.

    Returns dictionary with cleanup stats.
    """
    logger.info("Starting full system cleanup...")

    stats = {
        "processes_killed": cleanup_orphaned_processes(),
        "displays_killed": cleanup_orphaned_displays(),
        "sockets_removed": cleanup_stale_sockets(),
    }

    logger.info(f"Cleanup complete: {stats}")
    return stats


def _process_exists(pid: int) -> bool:
    """Check if a process exists."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _get_children(pid: int) -> list[int]:
    """Get all child process PIDs."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.stdout.strip():
            return [int(p) for p in result.stdout.strip().split('\n')]
    except Exception:
        pass
    return []


# Cleanup on module import (startup)
def _startup_cleanup():
    """Run cleanup when module is imported."""
    try:
        stats = full_cleanup()
        if any(stats.values()):
            logger.info("Startup cleanup performed")
    except Exception as e:
        logger.error(f"Startup cleanup failed: {e}")


# REMOVED: Automatic cleanup on module import is dangerous
# Cleanup is now only run explicitly via lifespan events in main.py
# This prevents killing unrelated processes on shared hosts.
#
# _startup_cleanup()  # DO NOT UNCOMMENT
