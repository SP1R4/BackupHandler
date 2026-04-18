"""Tests for file-level deduplication (hardlinks)."""

from __future__ import annotations

from src.dedup import _file_hash, deduplicate_backup_dirs, deduplicate_directory


class TestDeduplication:
    def test_file_hash_consistent(self, tmp_dir):
        f = tmp_dir / "test.txt"
        f.write_text("hello world")
        h1 = _file_hash(f)
        h2 = _file_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_dedup_identical_files(self, logger, tmp_dir):
        content = b"identical content for dedup test"
        (tmp_dir / "file1.txt").write_bytes(content)
        (tmp_dir / "file2.txt").write_bytes(content)
        (tmp_dir / "file3.txt").write_bytes(content)

        result = deduplicate_directory(logger, tmp_dir)
        assert result["files_checked"] == 3
        assert result["duplicates_found"] == 2
        assert result["bytes_saved"] == len(content) * 2

        inode1 = (tmp_dir / "file1.txt").stat().st_ino
        inode2 = (tmp_dir / "file2.txt").stat().st_ino
        inode3 = (tmp_dir / "file3.txt").stat().st_ino
        assert inode1 == inode2 == inode3

    def test_dedup_unique_files(self, logger, tmp_dir):
        (tmp_dir / "file1.txt").write_text("unique1")
        (tmp_dir / "file2.txt").write_text("unique2")

        result = deduplicate_directory(logger, tmp_dir)
        assert result["duplicates_found"] == 0
        assert result["bytes_saved"] == 0

    def test_dedup_skips_manifests(self, logger, tmp_dir):
        content = b"same content"
        (tmp_dir / "file1.txt").write_bytes(content)
        (tmp_dir / "backup_manifest_20260101_120000.json").write_bytes(content)

        result = deduplicate_directory(logger, tmp_dir)
        assert result["duplicates_found"] == 0

    def test_dedup_skips_enc_files(self, logger, tmp_dir):
        content = b"same content"
        (tmp_dir / "file1.txt").write_bytes(content)
        (tmp_dir / "file1.txt.enc").write_bytes(content)

        result = deduplicate_directory(logger, tmp_dir)
        assert result["duplicates_found"] == 0

    def test_dedup_empty_directory(self, logger, tmp_dir):
        result = deduplicate_directory(logger, tmp_dir)
        assert result["files_checked"] == 0
        assert result["duplicates_found"] == 0

    def test_dedup_nonexistent_directory(self, logger, tmp_dir):
        result = deduplicate_directory(logger, tmp_dir / "nonexistent")
        assert result["files_checked"] == 0

    def test_deduplicate_backup_dirs(self, logger, tmp_dir):
        dir1 = tmp_dir / "backup1"
        dir2 = tmp_dir / "backup2"
        dir1.mkdir()
        dir2.mkdir()

        content = b"same file content"
        (dir1 / "file.txt").write_bytes(content)
        (dir2 / "file.txt").write_bytes(content)

        result = deduplicate_backup_dirs(logger, [str(dir1), str(dir2)])
        assert result["duplicates_found"] >= 1

    def test_dedup_preserves_content(self, logger, tmp_dir):
        content = b"important data to preserve"
        (tmp_dir / "original.txt").write_bytes(content)
        (tmp_dir / "copy.txt").write_bytes(content)

        deduplicate_directory(logger, tmp_dir)

        assert (tmp_dir / "original.txt").read_bytes() == content
        assert (tmp_dir / "copy.txt").read_bytes() == content
