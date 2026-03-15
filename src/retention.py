"""
retention.py - Backup Retention Policy Enforcement

Automatically cleans up old backup files and directories based on two
complementary strategies:
  - Age-based:   Remove backups older than N days.
  - Count-based: Keep only the N most recent backups per directory.

Both policies can be active simultaneously. Age-based cleanup runs first,
then count-based cleanup applies to the remaining entries. Manifest JSON
files are excluded from cleanup to preserve backup history metadata.
"""

import os
import time
import shutil
from pathlib import Path


def cleanup_old_backups(logger, backup_dirs, max_age_days=0, max_count=0):
    """
    Remove old backups based on age and count retention policies.

    Parameters:
    - logger: Logger instance.
    - backup_dirs (list of str): Backup directories to apply retention to.
    - max_age_days (int): Remove backups older than this many days. 0 = disabled.
    - max_count (int): Keep only the N most recent backups. 0 = unlimited.
    """
    if max_age_days <= 0 and max_count <= 0:
        return

    for backup_dir in backup_dirs:
        backup_path = Path(backup_dir)
        if not backup_path.exists():
            continue

        # Collect backup entries (directories and zip files, but not manifest files)
        entries = []
        for entry in backup_path.iterdir():
            # Skip manifest files
            if entry.name.startswith('backup_manifest_') and entry.suffix == '.json':
                continue
            entries.append(entry)

        if not entries:
            continue

        # Sort by modification time, newest first
        entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)

        now = time.time()
        removed = []

        # Apply age-based retention
        if max_age_days > 0:
            max_age_seconds = max_age_days * 86400
            for entry in entries:
                age = now - entry.stat().st_mtime
                if age > max_age_seconds:
                    _remove_entry(logger, entry)
                    removed.append(entry)

        # Remove aged entries from the list
        entries = [e for e in entries if e not in removed]

        # Apply count-based retention
        if max_count > 0 and len(entries) > max_count:
            to_remove = entries[max_count:]
            for entry in to_remove:
                _remove_entry(logger, entry)
                removed.append(entry)

        if removed:
            logger.info(f"Retention cleanup: removed {len(removed)} old backup(s) from {backup_dir}")


def _remove_entry(logger, entry):
    """
    Safely remove a file or directory, logging the outcome.

    Uses ``shutil.rmtree`` for directories and ``unlink`` for files.
    Errors are logged but do not propagate.
    """
    try:
        if entry.is_dir():
            shutil.rmtree(entry)
            logger.info(f"Removed old backup directory: {entry}")
        else:
            entry.unlink()
            logger.info(f"Removed old backup file: {entry}")
    except Exception as e:
        logger.error(f"Failed to remove {entry}: {e}")
