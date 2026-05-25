"""
lock.py - Single-instance PID lock for the scheduler.

Prevents two scheduled backup runs from interleaving by writing the current
PID to a lock file. Validates that any existing lock points at a *live*
backup-handler process before honoring it, so a recycled PID from an
unrelated process doesn't block a legitimate run.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import sys
from pathlib import Path

from ._paths import LOCK_FILE


def _proc_looks_like_backup_handler(pid: int) -> bool:
    """
    Return True only when /proc/<pid>/comm or cmdline references python or
    the backup-handler entry point. PIDs get recycled; we don't want a stale
    lock file pointing at an unrelated process to refuse the lock forever.
    """
    comm = Path(f"/proc/{pid}/comm")
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        comm_value = comm.read_text().strip().lower() if comm.exists() else ""
        cmdline_value = cmdline.read_text().replace("\x00", " ").lower() if cmdline.exists() else ""
    except OSError:
        return False
    hints = ("python", "backup-handler", "main.py")
    return any(h in comm_value or h in cmdline_value for h in hints)


def acquire_lock(logger) -> None:
    """
    Acquire a PID lock file to prevent duplicate scheduled instances.

    Registers ``release_lock`` via ``atexit``. Exits the process if a live
    backup-handler instance already holds the lock.
    """
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            os.kill(old_pid, 0)
        except (ValueError, ProcessLookupError, PermissionError):
            logger.warning("Removing stale lock file (PID in file no longer running).")
        else:
            if _proc_looks_like_backup_handler(old_pid):
                logger.error(
                    f"Another backup-handler instance is already running (PID {old_pid}). "
                    f"Remove {LOCK_FILE} if this is incorrect."
                )
                sys.exit(1)
            logger.warning(
                f"Lock file references PID {old_pid} but that process is not "
                f"backup-handler (likely recycled). Reclaiming the lock."
            )

    LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(release_lock)


def release_lock() -> None:
    """Remove the PID lock file on exit."""
    with contextlib.suppress(OSError):
        LOCK_FILE.unlink(missing_ok=True)
