"""Tests for remote-path parsing used by the restore pipeline."""

from __future__ import annotations

from backup_handler.restore import _is_s3_path, _is_ssh_path, _parse_s3_path, _parse_ssh_path


class TestRemoteRestore:
    def test_is_ssh_path(self):
        assert _is_ssh_path("user@host:/backup/dir") is True
        assert _is_ssh_path("ssh://user@host/backup") is True
        assert _is_ssh_path("/local/path") is False
        assert _is_ssh_path("s3://bucket/prefix") is False

    def test_is_s3_path(self):
        assert _is_s3_path("s3://my-bucket/prefix") is True
        assert _is_s3_path("s3://bucket") is True
        assert _is_s3_path("/local/path") is False
        assert _is_s3_path("user@host:/path") is False

    def test_parse_ssh_path_user_host(self):
        user, host, path = _parse_ssh_path("admin@backup-server:/backups/daily")
        assert user == "admin"
        assert host == "backup-server"
        assert path == "/backups/daily"

    def test_parse_ssh_path_no_user(self):
        user, host, path = _parse_ssh_path("backup-server:/backups/daily")
        assert user is None
        assert host == "backup-server"
        assert path == "/backups/daily"

    def test_parse_ssh_url(self):
        user, host, path = _parse_ssh_path("ssh://admin@backup-server/backups/daily")
        assert user == "admin"
        assert host == "backup-server"
        assert path == "/backups/daily"

    def test_parse_s3_path(self):
        bucket, prefix = _parse_s3_path("s3://my-bucket/backups/2026")
        assert bucket == "my-bucket"
        assert prefix == "backups/2026"

    def test_parse_s3_path_no_prefix(self):
        bucket, prefix = _parse_s3_path("s3://my-bucket")
        assert bucket == "my-bucket"
        assert prefix == ""

    def test_parse_s3_path_single_prefix(self):
        bucket, prefix = _parse_s3_path("s3://bucket/prefix")
        assert bucket == "bucket"
        assert prefix == "prefix"


class TestRelPathRestore:
    def test_same_name_files_restore_to_correct_locations(self, logger, tmp_dir):
        """Two same-named files must each restore to their own subdirectory.

        Pre-rel_path manifests resolved entries by rglob(filename)[0], which
        restored whichever file the walk found first — for both entries.
        """
        import json

        from backup_handler.restore import restore_backup

        backup = tmp_dir / "backup"
        (backup / "app_a").mkdir(parents=True)
        (backup / "app_b").mkdir(parents=True)
        (backup / "app_a" / "config.txt").write_text("config for A")
        (backup / "app_b" / "config.txt").write_text("config for B")

        manifest = {
            "timestamp": "20260101_120000",
            "mode": "full",
            "copied": [
                {"path": "/src/app_a/config.txt", "size": 12, "rel_path": "app_a/config.txt"},
                {"path": "/src/app_b/config.txt", "size": 12, "rel_path": "app_b/config.txt"},
            ],
            "skipped": [],
            "failed": [],
        }
        (backup / "backup_manifest_20260101_120000.json").write_text(json.dumps(manifest))

        restore_dir = tmp_dir / "restore"
        ok = restore_backup(logger, str(backup), str(restore_dir), timestamp="20260101_120000")
        assert ok
        assert (restore_dir / "app_a" / "config.txt").read_text() == "config for A"
        assert (restore_dir / "app_b" / "config.txt").read_text() == "config for B"

    def test_restore_rejects_content_not_matching_manifest_checksum(self, logger, tmp_dir):
        """Backup content altered after the manifest was written must fail the
        restore, even when the altered file is internally consistent."""
        import hashlib
        import json

        from backup_handler.restore import restore_backup

        backup = tmp_dir / "backup"
        backup.mkdir()
        (backup / "data.txt").write_text("swapped-in content")

        manifest = {
            "timestamp": "20260101_120000",
            "mode": "full",
            "copied": [
                {
                    "path": "/src/data.txt",
                    "size": 16,
                    "rel_path": "data.txt",
                    "checksum": hashlib.sha256(b"original content").hexdigest(),
                }
            ],
            "skipped": [],
            "failed": [],
        }
        (backup / "backup_manifest_20260101_120000.json").write_text(json.dumps(manifest))

        ok = restore_backup(logger, str(backup), str(tmp_dir / "out"), timestamp="20260101_120000")
        assert not ok
