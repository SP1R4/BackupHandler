"""Tests for remote-path parsing used by the restore pipeline."""

from __future__ import annotations

from src.restore import _is_s3_path, _is_ssh_path, _parse_s3_path, _parse_ssh_path


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
