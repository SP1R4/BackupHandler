"""Tests for backup verification / integrity checks."""

from __future__ import annotations

import json

from src.verify import print_verify_report, verify_backup_integrity


class TestVerification:
    def _create_backup_with_manifest(self, backup_dir):
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "file1.txt").write_text("content1")
        (backup_dir / "file2.txt").write_text("content2")

        manifest = {
            "timestamp": "20260101_120000",
            "mode": "full",
            "duration_seconds": 1.0,
            "files_copied": 2,
            "files_skipped": 0,
            "files_failed": 0,
            "total_bytes": 16,
            "copied": [
                {"path": str(backup_dir / "file1.txt"), "size": 8},
                {"path": str(backup_dir / "file2.txt"), "size": 8},
            ],
            "skipped": [],
            "failed": [],
        }
        manifest_path = backup_dir / "backup_manifest_20260101_120000.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    def test_verify_all_ok(self, logger, tmp_dir):
        backup_dir = tmp_dir / "backup"
        self._create_backup_with_manifest(backup_dir)

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results["total"] == 2
        assert results["verified"] == 2
        assert results["missing"] == 0
        assert results["corrupted"] == 0

    def test_verify_missing_file(self, logger, tmp_dir):
        backup_dir = tmp_dir / "backup"
        self._create_backup_with_manifest(backup_dir)
        (backup_dir / "file1.txt").unlink()

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results["missing"] == 1
        assert results["verified"] == 1

    def test_verify_size_mismatch(self, logger, tmp_dir):
        backup_dir = tmp_dir / "backup"
        self._create_backup_with_manifest(backup_dir)
        (backup_dir / "file1.txt").write_text("corrupted data that is different size")

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results["corrupted"] == 1

    def test_verify_no_manifest_fallback(self, logger, tmp_dir):
        backup_dir = tmp_dir / "backup"
        backup_dir.mkdir(parents=True)
        (backup_dir / "file.txt").write_text("data")

        results = verify_backup_integrity(logger, [str(backup_dir)])
        assert results["total"] == 1
        assert results["verified"] == 1

    def test_verify_nonexistent_dir(self, logger, tmp_dir):
        results = verify_backup_integrity(logger, [str(tmp_dir / "nonexistent")])
        assert results["total"] == 0

    def test_print_report(self, logger, tmp_dir, capsys):
        backup_dir = tmp_dir / "backup"
        self._create_backup_with_manifest(backup_dir)
        results = verify_backup_integrity(logger, [str(backup_dir)])
        all_ok = print_verify_report(results)
        assert all_ok is True
        captured = capsys.readouterr()
        assert "ALL BACKUPS VERIFIED OK" in captured.out
