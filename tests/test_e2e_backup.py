"""
End-to-end test for the local backup pipeline.

Walks the full → verify → restore sequence against a temp tree:

  1. Sync source files into a backup directory (sync_directories_with_progress).
  2. Persist the manifest.
  3. Encrypt the backup at rest.
  4. Verify the backup against its manifest (verify_backup_integrity).
  5. Restore from the encrypted backup to a fresh destination.
  6. Confirm bytes match the source.

This is the single test that catches the cross-module regressions that
unit tests miss — checksum mismatches, encryption header drift,
manifest path encoding bugs, restore decryption flow failures.
"""

from __future__ import annotations

import os

import pytest

from backup_handler.encryption import encrypt_directory
from backup_handler.manifest import BackupManifest
from backup_handler.restore import restore_backup
from backup_handler.sync import sync_directories_with_progress
from backup_handler.verify import verify_backup_integrity


def _populate_source(root):
    """Create a small representative tree: nested dirs, mixed sizes, a symlink."""
    (root / "a.txt").write_text("alpha file")
    (root / "b.bin").write_bytes(os.urandom(2048))
    sub = root / "nested" / "deep"
    sub.mkdir(parents=True)
    (sub / "c.log").write_text("log line 1\nlog line 2\n")
    (root / "link_to_a").symlink_to(root / "a.txt")


@pytest.mark.integration
class TestE2EBackup:
    def test_full_then_verify_then_restore_roundtrip(self, tmp_dir, logger):
        source = tmp_dir / "source"
        source.mkdir()
        backup = tmp_dir / "backup"
        backup.mkdir()
        restored = tmp_dir / "restored"

        _populate_source(source)

        # 1. Full backup with manifest.
        manifest = BackupManifest(mode="full")
        sync_directories_with_progress(
            logger,
            source_dirs=[str(source)],
            backup_dirs=[str(backup)],
            manifest=manifest,
        )
        manifest_path = manifest.save(backup)
        assert manifest_path.exists()

        # Backup tree mirrors the source structure (with the symlink preserved).
        assert (backup / "a.txt").read_text() == "alpha file"
        assert (backup / "nested" / "deep" / "c.log").exists()
        assert (backup / "link_to_a").is_symlink()

        # 2. Verify the manifest before encryption.
        results = verify_backup_integrity(logger, [str(backup)])
        assert results["verified"] >= 3  # a.txt, b.bin, c.log (symlink may skip)
        assert results["missing"] == 0

        # 3. Encrypt the backup at rest.
        n = encrypt_directory(backup, passphrase="e2e-pass", logger=logger)
        assert n >= 3
        # Manifest must remain readable post-encryption.
        assert manifest_path.exists()

        # 4. Verify still passes with the passphrase.
        results = verify_backup_integrity(logger, [str(backup)], encryption_passphrase="e2e-pass")
        assert results["missing"] == 0
        assert results["corrupted"] == 0

        # 5. Restore to a fresh destination, decrypting as we go.
        ok = restore_backup(
            logger,
            from_dir=str(backup),
            to_dir=str(restored),
            encryption_passphrase="e2e-pass",
        )
        assert ok is True

        # 6. Restored bytes match source.
        assert (restored / "a.txt").read_text() == "alpha file"
        assert (restored / "nested" / "deep" / "c.log").read_text() == "log line 1\nlog line 2\n"
        # b.bin is binary — compare via hash.
        import hashlib

        src_hash = hashlib.sha256((source / "b.bin").read_bytes()).hexdigest()
        dst_hash = hashlib.sha256((restored / "b.bin").read_bytes()).hexdigest()
        assert src_hash == dst_hash

    def test_unencrypted_roundtrip(self, tmp_dir, logger):
        """The simpler path: skip encryption entirely and confirm restore still works."""
        source = tmp_dir / "src"
        source.mkdir()
        (source / "only.txt").write_text("hello")
        backup = tmp_dir / "bk"
        backup.mkdir()
        restored = tmp_dir / "rs"

        manifest = BackupManifest(mode="full")
        sync_directories_with_progress(
            logger,
            source_dirs=[str(source)],
            backup_dirs=[str(backup)],
            manifest=manifest,
        )
        manifest.save(backup)

        ok = restore_backup(logger, str(backup), str(restored))
        assert ok is True
        assert (restored / "only.txt").read_text() == "hello"
