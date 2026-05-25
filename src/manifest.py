"""
manifest.py - Backup Manifest Tracking

Records per-file backup operations (copied, skipped, failed) and writes
a timestamped JSON manifest to each backup directory. Manifests enable:
  - Backup verification (compare files against recorded checksums/sizes)
  - Point-in-time restore (replay manifests chronologically)
  - Status dashboard (display latest backup summary)
  - Incremental/differential tracking (know which files changed)

Manifest files are named ``backup_manifest_YYYYMMDD_HHMMSS.json`` and are
excluded from encryption and deduplication to remain accessible without
decryption keys.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Manifest filenames must match exactly: backup_manifest_YYYYMMDD_HHMMSS.json
# Anything else (truncated names, foreign files) is ignored when sorting.
_MANIFEST_RE = re.compile(r"^backup_manifest_(\d{8}_\d{6})\.json$")


def _valid_manifests(directory: Path) -> list[tuple[str, Path]]:
    """Return (timestamp, path) pairs for files whose name matches the manifest pattern."""
    found: list[tuple[str, Path]] = []
    for p in directory.glob("backup_manifest_*.json"):
        m = _MANIFEST_RE.match(p.name)
        if m:
            found.append((m.group(1), p))
    return found


class BackupManifest:
    """
    Records per-file backup operations and writes a summary manifest JSON.

    Usage:
        manifest = BackupManifest(mode='full')
        manifest.record_copy('/path/to/file', 1024)
        manifest.record_skip('/path/to/unchanged')
        manifest.record_failure('/path/to/bad', 'permission denied')
        manifest.save('/backups/daily')
    """

    def __init__(self, mode: str = "full") -> None:
        self._start_time = time.time()
        self._mode = mode
        self._copied: list[dict[str, Any]] = []
        self._skipped: list[dict[str, Any]] = []
        self._failed: list[dict[str, Any]] = []
        self._total_bytes = 0

    def record_copy(
        self,
        file_path: Path | str,
        size_bytes: int,
        checksum: str | None = None,
    ) -> None:
        """Record a successfully copied file with optional SHA-256 checksum."""
        entry: dict[str, Any] = {"path": str(file_path), "size": size_bytes}
        if checksum:
            entry["checksum"] = checksum
        self._copied.append(entry)
        self._total_bytes += size_bytes

    def record_skip(self, file_path: Path | str) -> None:
        """Record a skipped (unchanged) file."""
        self._skipped.append({"path": str(file_path)})

    def record_failure(self, file_path: Path | str, reason: str) -> None:
        """Record a failed file operation."""
        self._failed.append({"path": str(file_path), "reason": reason})

    def save(self, output_dir: Path | str) -> Path:
        """
        Write the manifest JSON to output_dir.

        Parameters:
        - output_dir (str or Path): Directory to write the manifest file.

        Returns:
        - Path: Path to the written manifest file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        duration = time.time() - self._start_time
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        manifest_data = {
            "timestamp": timestamp,
            "mode": self._mode,
            "duration_seconds": round(duration, 2),
            "files_copied": len(self._copied),
            "files_skipped": len(self._skipped),
            "files_failed": len(self._failed),
            "total_bytes": self._total_bytes,
            "copied": self._copied,
            "skipped": self._skipped,
            "failed": self._failed,
        }

        manifest_path = output_dir / f"backup_manifest_{timestamp}.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest_data, f, indent=2)

        return manifest_path

    def summary(self) -> dict[str, Any]:
        """Return a summary dict (without per-file details)."""
        duration = time.time() - self._start_time
        return {
            "mode": self._mode,
            "duration_seconds": round(duration, 2),
            "files_copied": len(self._copied),
            "files_skipped": len(self._skipped),
            "files_failed": len(self._failed),
            "total_bytes": self._total_bytes,
        }


def load_latest_manifest(directory: Path | str) -> dict[str, Any] | None:
    """Load the most recent backup manifest from a directory, or None."""
    directory = Path(directory)
    matches = _valid_manifests(directory)
    if not matches:
        return None
    matches.sort(reverse=True)  # lexicographic on YYYYMMDD_HHMMSS == chronological
    _, latest_path = matches[0]
    with open(latest_path) as f:
        return json.load(f)


def load_manifests_up_to(directory: Path | str, timestamp: str) -> list[dict[str, Any]]:
    """
    Load all manifests up to (and including) ``timestamp``, sorted oldest-first.

    Files whose names don't match ``backup_manifest_YYYYMMDD_HHMMSS.json`` are
    silently skipped.
    """
    directory = Path(directory)
    matches = _valid_manifests(directory)
    matches.sort()
    manifests = []
    for ts, manifest_file in matches:
        if ts <= timestamp:
            with open(manifest_file) as f:
                data = json.load(f)
                data["_manifest_path"] = str(manifest_file)
                manifests.append(data)
    return manifests
