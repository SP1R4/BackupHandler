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

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import qsafe_backend

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
        rel_path: Path | str | None = None,
    ) -> None:
        """
        Record a successfully copied file with optional SHA-256 checksum.

        ``rel_path`` is the file's path relative to the backup destination
        root. Recording it lets restore/verify resolve files exactly instead
        of guessing by filename, which can match the wrong file when names
        repeat across subdirectories.
        """
        entry: dict[str, Any] = {"path": str(file_path), "size": size_bytes}
        if checksum:
            entry["checksum"] = checksum
        if rel_path is not None:
            entry["rel_path"] = str(rel_path)
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


def latest_manifest_path(directory: Path | str) -> Path | None:
    """Return the path of the most recent backup manifest, or None."""
    matches = _valid_manifests(Path(directory))
    if not matches:
        return None
    matches.sort(reverse=True)  # lexicographic on YYYYMMDD_HHMMSS == chronological
    return matches[0][1]


def load_latest_manifest(directory: Path | str) -> dict[str, Any] | None:
    """Load the most recent backup manifest from a directory, or None."""
    latest_path = latest_manifest_path(directory)
    if latest_path is None:
        return None
    with open(latest_path) as f:
        data: dict[str, Any] = json.load(f)
    return data


def _sha256_file(path: Path) -> str:
    """Stream a file through SHA-256 in 1 MiB chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def record_encrypted_checksums(manifest_path: Path | str, backup_dir: Path | str) -> int:
    """
    Post-encryption pass: record the SHA-256 of each entry's ciphertext.

    For every copied entry whose ``rel_path`` now exists as ``<rel_path>.enc``
    under ``backup_dir``, adds an ``enc_checksum`` field (hash of the encrypted
    bytes). This enables keyless integrity verification of encrypted or remote
    backups and detects two validly-encrypted files being swapped — which
    AEAD authentication alone cannot catch.

    Rewrites the manifest atomically. Callers that sign manifests must sign
    AFTER this pass. Returns the number of entries updated.
    """
    manifest_path = Path(manifest_path)
    backup_dir = Path(backup_dir)
    with open(manifest_path) as f:
        data: dict[str, Any] = json.load(f)

    updated = 0
    for entry in data.get("copied", []):
        rel = entry.get("rel_path")
        if not rel:
            continue
        enc_file = backup_dir / (rel + ".enc")
        if not enc_file.is_file():
            continue
        entry["enc_checksum"] = _sha256_file(enc_file)
        updated += 1

    if updated:
        tmp = manifest_path.with_name(manifest_path.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, manifest_path)
    return updated


def manifest_signature_status(manifest_path: Path | str, sign_public_key: str) -> str:
    """
    Check the detached Qsafe (ML-DSA-87) signature for a manifest file.

    Returns 'valid', 'invalid', or 'missing' (no ``.sig`` alongside — normal
    for backups made before signing was enabled).
    """
    sig_path = Path(str(manifest_path) + ".sig")
    if not sig_path.exists():
        return "missing"
    ok = qsafe_backend.verify_signature_file(manifest_path, sig_path, sign_public_key)
    return "valid" if ok else "invalid"


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
