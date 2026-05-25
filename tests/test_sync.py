"""Unit tests for the local-copy path in src.sync."""

from __future__ import annotations

import os

from backup_handler.manifest import BackupManifest
from backup_handler.sync import _copy_single_file, sync_directories_with_progress


class TestCopySingleFile:
    def test_copies_and_records_manifest(self, tmp_dir, logger):
        src = tmp_dir / "src"
        src.mkdir()
        dst = tmp_dir / "dst"
        f = src / "data.txt"
        f.write_text("payload")

        manifest = BackupManifest(mode="full")
        _copy_single_file(logger, f, str(src), str(dst), manifest)

        assert (dst / "data.txt").read_text() == "payload"
        summary = manifest.summary()
        assert summary["files_copied"] == 1
        assert summary["files_failed"] == 0
        assert summary["total_bytes"] == len("payload")

    def test_records_failure_when_dest_unwritable(self, tmp_dir, logger):
        src = tmp_dir / "s"
        src.mkdir()
        f = src / "a.txt"
        f.write_text("x")

        # Create a regular file where _copy_single_file expects a directory.
        # mkdir of dst/ will fail because dst is a file.
        dst_root = tmp_dir / "dst"
        dst_root.write_text("not a directory")

        manifest = BackupManifest(mode="full")
        _copy_single_file(logger, f, str(src), str(dst_root), manifest)

        summary = manifest.summary()
        assert summary["files_failed"] == 1
        assert summary["files_copied"] == 0

    def test_preserves_symlinks(self, tmp_dir, logger):
        src = tmp_dir / "src"
        src.mkdir()
        target = src / "real.txt"
        target.write_text("real")
        link = src / "link.txt"
        link.symlink_to(target)
        dst = tmp_dir / "dst"

        manifest = BackupManifest(mode="full")
        # Copy the symlink itself.
        _copy_single_file(logger, link, str(src), str(dst), manifest)

        copied = dst / "link.txt"
        assert copied.is_symlink()
        assert os.readlink(copied) == str(target)


class TestSyncDirectories:
    def test_exclude_patterns_skip_files(self, tmp_dir, logger):
        src = tmp_dir / "src"
        src.mkdir()
        (src / "keep.txt").write_text("keep")
        (src / "skip.log").write_text("skip")
        dst = tmp_dir / "dst"
        dst.mkdir()

        sync_directories_with_progress(
            logger,
            source_dirs=[str(src)],
            backup_dirs=[str(dst)],
            exclude_patterns=["*.log"],
        )

        assert (dst / "keep.txt").exists()
        assert not (dst / "skip.log").exists()
