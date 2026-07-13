"""
_paths.py - XDG-aware filesystem path resolution for backup_handler.

Search order is fixed and predictable so users can layer system, user,
and bundled defaults without surprise:

  Config (read):
    1. $BACKUP_HANDLER_CONFIG_DIR (explicit override, if set)
    2. $XDG_CONFIG_HOME/backup-handler   (~/.config/backup-handler)
    3. /etc/backup-handler
    4. <package_root>/config             (dev/legacy fallback)

  Data (Logs, BackupTimestamp, snapshots — read/write):
    1. $BACKUP_HANDLER_DATA_DIR (explicit override, if set)
    2. <package_root> when Logs/ or BackupTimestamp/ already exist there
       (preserves dev checkouts and existing on-prem installs)
    3. $XDG_DATA_HOME/backup-handler     (~/.local/share/backup-handler)
    4. /var/lib/backup-handler

  Lock:
    1. $XDG_RUNTIME_DIR/backup-handler.lock
    2. /var/run/backup-handler.lock (only if writable)
    3. <DATA_DIR>/.backup-handler.lock

A directory is selected if it already exists (so existing installs are
preserved) or, for write paths, the first plausible default is chosen.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo-root fallback. parents[2]: _paths.py -> backup_handler/ -> src/ -> <root>.
_PACKAGE_FALLBACK_ROOT = Path(__file__).resolve().parents[2]


def _xdg_dir(env_var: str, default_subpath: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw)
    return Path.home() / default_subpath


def _resolve_config_dir() -> Path:
    """Pick the first existing config directory from the search order."""
    override = os.environ.get("BACKUP_HANDLER_CONFIG_DIR")
    if override:
        return Path(override)
    candidates = [
        _xdg_dir("XDG_CONFIG_HOME", ".config") / "backup-handler",
        Path("/etc/backup-handler"),
        _PACKAGE_FALLBACK_ROOT / "config",
    ]
    for c in candidates:
        if c.exists():
            return c
    # No directory exists yet. Prefer XDG for new installs.
    return candidates[0]


def _resolve_data_dir() -> Path:
    """Pick the first existing data directory from the search order."""
    override = os.environ.get("BACKUP_HANDLER_DATA_DIR")
    if override:
        return Path(override)
    # If the dev checkout / existing install has Logs or BackupTimestamp at
    # the package root, keep using that — don't silently move user data.
    if (_PACKAGE_FALLBACK_ROOT / "Logs").exists() or (_PACKAGE_FALLBACK_ROOT / "BackupTimestamp").exists():
        return _PACKAGE_FALLBACK_ROOT
    candidates = [
        _xdg_dir("XDG_DATA_HOME", ".local/share") / "backup-handler",
        Path("/var/lib/backup-handler"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _resolve_lock_file(data_dir: Path) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        return Path(runtime) / "backup-handler.lock"
    var_run = Path("/var/run")
    if var_run.is_dir() and os.access(var_run, os.W_OK):
        return var_run / "backup-handler.lock"
    return data_dir / ".backup-handler.lock"


PROJECT_ROOT = _PACKAGE_FALLBACK_ROOT  # kept for legacy references
CONFIG_DIR = _resolve_config_dir()
DATA_DIR = _resolve_data_dir()
LOG_DIR = DATA_DIR / "Logs"
TIMESTAMP_DIR = DATA_DIR / "BackupTimestamp"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
LOCK_FILE = _resolve_lock_file(DATA_DIR)
