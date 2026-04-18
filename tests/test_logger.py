"""Tests for the structured logger module."""

from __future__ import annotations

import json
import logging

from src.logger import AppLogger, audit, current_run_id, new_run_id


class TestRunID:
    def test_new_run_id_is_unique(self):
        rid1 = new_run_id()
        rid2 = new_run_id()
        assert rid1 != rid2
        assert len(rid1) == 12

    def test_current_run_id_reflects_set(self):
        rid = new_run_id()
        assert current_run_id() == rid


class TestAppLogger:
    def test_creates_log_file(self, tmp_dir):
        log_file = tmp_dir / "app.log"
        AppLogger(log_file, logging.INFO)
        assert log_file.parent.exists()

    def test_audit_event_written_to_audit_log(self, tmp_dir, monkeypatch):
        monkeypatch.setenv("BACKUP_HANDLER_LOG_JSON", "1")
        # Reset any previously configured "backup_handler" logger.
        root = logging.getLogger("backup_handler")
        for h in list(root.handlers):
            root.removeHandler(h)

        log_file = tmp_dir / "app.log"
        audit_file = tmp_dir / "audit.log"
        app = AppLogger(log_file, logging.INFO, audit_file=audit_file)

        new_run_id()
        audit(app.logger, "audit.backup_complete", "ok", files=3)

        for h in app.logger.handlers:
            h.flush()

        contents = audit_file.read_text().strip().splitlines()
        assert contents, "audit handler wrote nothing"
        record = json.loads(contents[-1])
        assert record["audit_event"] == "audit.backup_complete"
        assert record["msg"] == "ok"
        assert record["run_id"] == current_run_id()
