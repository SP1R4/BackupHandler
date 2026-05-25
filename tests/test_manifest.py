"""Tests for backup manifest loading and filename validation."""

from __future__ import annotations

import json

from src.manifest import BackupManifest, load_latest_manifest, load_manifests_up_to


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
        (tmp_dir / "backup_manifest_20260101_120000.json").write_text(
            json.dumps({"timestamp": "old"})
        )
        (tmp_dir / "backup_manifest_20260601_120000.json").write_text(
            json.dumps({"timestamp": "new"})
        )
        latest = load_latest_manifest(tmp_dir)
        assert latest["timestamp"] == "new"

    def test_load_latest_ignores_malformed_names(self, tmp_dir):
        # Should NOT be picked up even though the glob matches.
        (tmp_dir / "backup_manifest_truncated.json").write_text("{}")
        (tmp_dir / "backup_manifest_20260101_120000_extra.json").write_text("{}")
        (tmp_dir / "backup_manifest_20260101_120000.json").write_text(
            json.dumps({"timestamp": "good"})
        )
        latest = load_latest_manifest(tmp_dir)
        assert latest["timestamp"] == "good"

    def test_load_latest_empty(self, tmp_dir):
        assert load_latest_manifest(tmp_dir) is None

    def test_load_manifests_up_to_cutoff(self, tmp_dir):
        for ts in ("20260101_100000", "20260101_120000", "20260201_120000"):
            (tmp_dir / f"backup_manifest_{ts}.json").write_text(
                json.dumps({"timestamp": ts})
            )
        ms = load_manifests_up_to(tmp_dir, "20260101_120000")
        assert [m["timestamp"] for m in ms] == ["20260101_100000", "20260101_120000"]
