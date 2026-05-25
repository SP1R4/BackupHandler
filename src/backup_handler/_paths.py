"""
_paths.py - Filesystem path resolution for backup_handler.

Locates the project root (the directory containing ``config/``, ``Logs/``,
``BackupTimestamp/``, etc.) relative to this module's installation site.
Centralizes the path math so individual modules don't drift apart.

PR 6 replaces these hard-coded relatives with XDG-aware lookups.
"""

from __future__ import annotations

from pathlib import Path

# This file lives at <root>/src/backup_handler/_paths.py during development.
# parents[2] climbs: _paths.py -> backup_handler/ -> src/ -> <root>.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = PROJECT_ROOT / "Logs"
TIMESTAMP_DIR = PROJECT_ROOT / "BackupTimestamp"
SNAPSHOT_DIR = PROJECT_ROOT / "snapshots"
LOCK_FILE = PROJECT_ROOT / ".backup-handler.lock"
