"""Tests for backup manifest loading and filename validation."""

from __future__ import annotations

import hashlib
import json

from backup_handler.manifest import (
    BackupManifest,
    load_latest_manifest,
    load_manifests_up_to,
    record_encrypted_checksums,
)


class TestBackupManifest:
    def test_records_and_saves(self, tmp_dir):
        m = BackupManifest(mode="full")
        m.record_copy("/x/a", 100, checksum="abc")
        m.record_skip("/x/b")
        m.record_failure("/x/c", "perm denied")
        path = m.save(tmp_dir)

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["mode"] == "full"
        assert data["files_copied"] == 1
        assert data["files_skipped"] == 1
        assert data["files_failed"] == 1
        assert data["total_bytes"] == 100


class TestManifestLoading:
    def test_load_latest_picks_newest_valid_name(self, tmp_dir):
        (tmp_dir / "backup_manifest_20260101_120000.json").write_text(json.dumps({"timestamp": "old"}))
        (tmp_dir / "backup_manifest_20260601_120000.json").write_text(json.dumps({"timestamp": "new"}))
        latest = load_latest_manifest(tmp_dir)
        assert latest["timestamp"] == "new"

    def test_load_latest_ignores_malformed_names(self, tmp_dir):
        # Should NOT be picked up even though the glob matches.
        (tmp_dir / "backup_manifest_truncated.json").write_text("{}")
        (tmp_dir / "backup_manifest_20260101_120000_extra.json").write_text("{}")
        (tmp_dir / "backup_manifest_20260101_120000.json").write_text(json.dumps({"timestamp": "good"}))
        latest = load_latest_manifest(tmp_dir)
        assert latest["timestamp"] == "good"

    def test_load_latest_empty(self, tmp_dir):
        assert load_latest_manifest(tmp_dir) is None

    def test_load_manifests_up_to_cutoff(self, tmp_dir):
        for ts in ("20260101_100000", "20260101_120000", "20260201_120000"):
            (tmp_dir / f"backup_manifest_{ts}.json").write_text(json.dumps({"timestamp": ts}))
        ms = load_manifests_up_to(tmp_dir, "20260101_120000")
        assert [m["timestamp"] for m in ms] == ["20260101_100000", "20260101_120000"]


class TestRelPathAndEncChecksums:
    def test_record_copy_includes_rel_path(self, tmp_dir):
        m = BackupManifest(mode="full")
        m.record_copy("/src/docs/a.txt", 100, checksum="abc", rel_path="docs/a.txt")
        m.record_copy("/src/b.txt", 50)  # rel_path optional
        path = m.save(tmp_dir)

        data = json.loads(path.read_text())
        assert data["copied"][0]["rel_path"] == "docs/a.txt"
        assert "rel_path" not in data["copied"][1]

    def test_record_encrypted_checksums(self, tmp_dir):
        backup = tmp_dir / "backup"
        (backup / "docs").mkdir(parents=True)
        (backup / "docs" / "a.txt.enc").write_bytes(b"ciphertext-a")
        (backup / "plain.txt").write_text("never encrypted")

        m = BackupManifest(mode="full")
        m.record_copy("/src/docs/a.txt", 100, rel_path="docs/a.txt")
        m.record_copy("/src/plain.txt", 15, rel_path="plain.txt")  # no .enc on disk
        m.record_copy("/src/legacy.txt", 5)  # no rel_path
        manifest_path = m.save(backup)

        updated = record_encrypted_checksums(manifest_path, backup)
        assert updated == 1

        data = json.loads(manifest_path.read_text())
        expected = hashlib.sha256(b"ciphertext-a").hexdigest()
        assert data["copied"][0]["enc_checksum"] == expected
        assert "enc_checksum" not in data["copied"][1]
        assert "enc_checksum" not in data["copied"][2]

    def test_record_encrypted_checksums_no_matches_leaves_file_alone(self, tmp_dir):
        m = BackupManifest(mode="full")
        m.record_copy("/src/a.txt", 1, rel_path="a.txt")
        manifest_path = m.save(tmp_dir)
        before = manifest_path.read_bytes()

        assert record_encrypted_checksums(manifest_path, tmp_dir) == 0
        assert manifest_path.read_bytes() == before
