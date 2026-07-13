"""Tests for the early-exit subcommand handlers — focus on restore path safety."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from backup_handler.dispatch import _is_remote_path, handle_restore


class TestIsRemotePath:
    def test_ssh_scheme(self):
        assert _is_remote_path("ssh://user@host/path") is True

    def test_s3_scheme(self):
        assert _is_remote_path("s3://bucket/key") is True

    def test_user_at_host_colon(self):
        assert _is_remote_path("user@host:/remote") is True

    def test_plain_local_path(self):
        assert _is_remote_path("/tmp/backup") is False

    def test_local_relative(self):
        assert _is_remote_path("./backup") is False


class TestRestorePathValidation:
    def test_refuses_same_path(self, tmp_dir, logger):
        d = tmp_dir / "shared"
        d.mkdir()
        args = SimpleNamespace(
            from_dir=str(d),
            to_dir=str(d),
            restore_timestamp=None,
            dry_run=False,
        )
        rc = handle_restore(logger, args, config_path="/nonexistent")
        assert rc == 1

    def test_refuses_nested_destination(self, tmp_dir, logger):
        backup = tmp_dir / "backup"
        backup.mkdir()
        nested = backup / "restored"
        # Don't pre-create — resolve still computes the path.
        args = SimpleNamespace(
            from_dir=str(backup),
            to_dir=str(nested),
            restore_timestamp=None,
            dry_run=False,
        )
        rc = handle_restore(logger, args, config_path="/nonexistent")
        assert rc == 1

    def test_allows_disjoint_paths(self, tmp_dir, logger):
        backup = tmp_dir / "backup"
        backup.mkdir()
        restored = tmp_dir / "restored"
        args = SimpleNamespace(
            from_dir=str(backup),
            to_dir=str(restored),
            restore_timestamp=None,
            dry_run=False,
        )
        with (
            mock.patch("backup_handler.dispatch.extract_config_values", return_value={}),
            mock.patch("backup_handler.dispatch.restore_backup", return_value=True) as m,
        ):
            rc = handle_restore(logger, args, config_path="/dev/null")
        assert rc == 0
        # Validation didn't bail out — restore_backup was actually called.
        assert m.called
