"""
status.py - Backup status dashboard rendered for the CLI.

Splits the read-only ``--status`` subcommand out of cli.py so the
entrypoint stays small. Reads timestamps, the resolved config, and the
latest manifest in each backup directory.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .config import extract_config_values
from .manifest import load_latest_manifest
from .utils import get_last_backup_time, get_last_full_backup_time


def _human_bytes(n: int) -> str:
    if n >= 1073741824:
        return f"{n / 1073741824:.2f} GB"
    if n >= 1048576:
        return f"{n / 1048576:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.2f} KB"
    return f"{n} B"


def show_status(logger: logging.Logger, config_path: str) -> None:
    """Print last-run timestamps, schedule, sizes, and the latest manifest summary."""
    print("\n=== Backup Status ===\n")

    last_backup = get_last_backup_time()
    last_full = get_last_full_backup_time()

    print(
        "Last backup:      "
        + (datetime.fromtimestamp(last_backup).strftime("%Y-%m-%d %H:%M:%S") if last_backup else "Never")
    )
    print(
        "Last full backup: "
        + (datetime.fromtimestamp(last_full).strftime("%Y-%m-%d %H:%M:%S") if last_full else "Never")
    )

    try:
        config_values = extract_config_values(logger, config_path, skip_validation=True)
    except Exception:
        config_values = {}

    schedule_times = config_values.get("schedule_times", [])
    print("\nScheduled times: " + (", ".join(schedule_times) if schedule_times else "Not configured"))

    backup_dirs = config_values.get("backup_dirs", [])
    if backup_dirs:
        print("\nBackup directories:")
        for bdir in backup_dirs:
            bpath = Path(bdir)
            if not bpath.exists():
                print(f"  {bdir}: (not found)")
                continue
            cached = load_latest_manifest(bdir)
            if cached and cached.get("total_bytes") is not None:
                size_str = _human_bytes(cached["total_bytes"]) + " (from manifest)"
            else:
                total = sum(f.stat().st_size for f in bpath.rglob("*") if f.is_file())
                size_str = _human_bytes(total)
            print(f"  {bdir}: {size_str}")

        print("\nLatest manifest:")
        found = False
        for bdir in backup_dirs:
            manifest = load_latest_manifest(bdir)
            if manifest:
                found = True
                print(f"  Directory: {bdir}")
                print(f"    Timestamp: {manifest.get('timestamp', 'Unknown')}")
                print(f"    Mode:      {manifest.get('mode', 'Unknown')}")
                print(f"    Duration:  {manifest.get('duration_seconds', 0):.1f}s")
                print(f"    Copied:    {manifest.get('files_copied', 0)} files")
                print(f"    Skipped:   {manifest.get('files_skipped', 0)} files")
                print(f"    Failed:    {manifest.get('files_failed', 0)} files")
                print(f"    Size:      {_human_bytes(manifest.get('total_bytes', 0))}")
                break
        if not found:
            print("  No manifests found")

    print()
