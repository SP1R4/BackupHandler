"""Tests for backup verification / integrity checks."""

from __future__ import annotations

import json

from backup_handler.verify import print_verify_report, verify_backup_integrity


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


class TestEncChecksumVerification:
    def _make_encrypted_backup(self, backup_dir, enc_bytes, enc_checksum):
        import hashlib

        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "secret.txt.enc").write_bytes(enc_bytes)
        manifest = {
            "timestamp": "20260101_120000",
            "mode": "full",
            "copied": [
                {
                    "path": "/src/secret.txt",
                    "size": 6,
                    "rel_path": "secret.txt",
                    "enc_checksum": enc_checksum or hashlib.sha256(enc_bytes).hexdigest(),
                }
            ],
            "skipped": [],
            "failed": [],
        }
        (backup_dir / "backup_manifest_20260101_120000.json").write_text(json.dumps(manifest))

    def test_keyless_ciphertext_checksum_ok(self, logger, tmp_dir):
        """With enc_checksum recorded, verify proves integrity without any keys."""
        backup = tmp_dir / "backup"
        self._make_encrypted_backup(backup, b"sealed-bytes", None)

        results = verify_backup_integrity(logger, [str(backup)])
        assert results["verified"] == 1
        assert results["corrupted"] == 0
        details = results["directories"][str(backup)]["details"]
        assert any("ciphertext checksum" in d for d in details)

    def test_keyless_ciphertext_checksum_mismatch(self, logger, tmp_dir):
        """A modified .enc file is flagged corrupted — no keys needed."""
        import hashlib

        backup = tmp_dir / "backup"
        self._make_encrypted_backup(backup, b"tampered-bytes", hashlib.sha256(b"original-bytes").hexdigest())

        results = verify_backup_integrity(logger, [str(backup)])
        assert results["corrupted"] == 1
        assert results["verified"] == 0
        details = results["directories"][str(backup)]["details"]
        assert any("ENC CHECKSUM MISMATCH" in d for d in details)

    def test_rel_path_exact_resolution(self, logger, tmp_dir):
        """rel_path entries resolve exactly; same-named files can't shadow."""
        backup = tmp_dir / "backup"
        (backup / "a").mkdir(parents=True)
        (backup / "b").mkdir(parents=True)
        (backup / "a" / "config.txt").write_text("AAAA")
        (backup / "b" / "config.txt").write_text("BB")
        manifest = {
            "timestamp": "20260101_120000",
            "mode": "full",
            "copied": [
                {"path": "/src/a/config.txt", "size": 4, "rel_path": "a/config.txt"},
                {"path": "/src/b/config.txt", "size": 2, "rel_path": "b/config.txt"},
            ],
            "skipped": [],
            "failed": [],
        }
        (backup / "backup_manifest_20260101_120000.json").write_text(json.dumps(manifest))

        results = verify_backup_integrity(logger, [str(backup)])
        assert results["verified"] == 2
        assert results["corrupted"] == 0

    def test_rel_path_missing_is_missing(self, logger, tmp_dir):
        """A rel_path entry with no file (plain or .enc) is missing — no
        filename-guessing fallback that could mask deletion."""
        backup = tmp_dir / "backup"
        backup.mkdir(parents=True)
        (backup / "elsewhere").mkdir()
        (backup / "elsewhere" / "data.txt").write_text("decoy with same name")
        manifest = {
            "timestamp": "20260101_120000",
            "mode": "full",
            "copied": [{"path": "/src/data.txt", "size": 5, "rel_path": "data.txt"}],
            "skipped": [],
            "failed": [],
        }
        (backup / "backup_manifest_20260101_120000.json").write_text(json.dumps(manifest))

        results = verify_backup_integrity(logger, [str(backup)])
        assert results["missing"] == 1
