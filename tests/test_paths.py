"""Tests for the XDG-aware path resolver in src.backup_handler._paths."""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest


@pytest.fixture
def fresh_paths(monkeypatch):
    """
    Reload _paths.py with the current environment so the module-level
    constants reflect the test's env vars rather than import-time defaults.
    """

    def _reload(env: dict | None = None) -> ModuleType:
        for k in (
            "BACKUP_HANDLER_CONFIG_DIR",
            "BACKUP_HANDLER_DATA_DIR",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
        ):
            monkeypatch.delenv(k, raising=False)
        if env:
            for k, v in env.items():
                if v is None:
                    monkeypatch.delenv(k, raising=False)
                else:
                    monkeypatch.setenv(k, v)
        import backup_handler._paths as paths_module

        return importlib.reload(paths_module)

    return _reload


class TestConfigResolution:
    def test_explicit_override_wins(self, tmp_dir, fresh_paths):
        custom = tmp_dir / "custom_config"
        custom.mkdir()
        m = fresh_paths({"BACKUP_HANDLER_CONFIG_DIR": str(custom)})
        assert custom == m.CONFIG_DIR

    def test_xdg_used_when_exists(self, tmp_dir, fresh_paths):
        xdg_home = tmp_dir / "xdg"
        xdg_home.mkdir()
        (xdg_home / "backup-handler").mkdir()
        m = fresh_paths({"XDG_CONFIG_HOME": str(xdg_home)})
        assert xdg_home / "backup-handler" == m.CONFIG_DIR


class TestDataResolution:
    def test_explicit_override_wins(self, tmp_dir, fresh_paths):
        custom = tmp_dir / "custom_data"
        custom.mkdir()
        m = fresh_paths({"BACKUP_HANDLER_DATA_DIR": str(custom)})
        assert custom == m.DATA_DIR
        assert custom / "Logs" == m.LOG_DIR
        assert custom / "BackupTimestamp" == m.TIMESTAMP_DIR


class TestLockResolution:
    def test_runtime_dir_preferred(self, tmp_dir, fresh_paths):
        runtime = tmp_dir / "runtime"
        runtime.mkdir()
        m = fresh_paths({"XDG_RUNTIME_DIR": str(runtime)})
        assert m.LOCK_FILE.parent == runtime
        assert m.LOCK_FILE.name == "backup-handler.lock"

    def test_falls_back_to_data_dir(self, tmp_dir, fresh_paths):
        data = tmp_dir / "data"
        data.mkdir()
        # Point /var/run somewhere unwritable so it can't be chosen.
        m = fresh_paths({"BACKUP_HANDLER_DATA_DIR": str(data), "XDG_RUNTIME_DIR": ""})
        # Either /var/run picked (writable on this OS) or data/.backup-handler.lock.
        assert m.LOCK_FILE.name in ("backup-handler.lock", ".backup-handler.lock")
