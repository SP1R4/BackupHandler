"""Shared pytest fixtures for the backup_handler test suite."""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the project root is importable as ``src.*`` and ``main``.
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def logger() -> logging.Logger:
    """Quiet logger used by tests that need to pass a logger positionally."""
    log = logging.getLogger("test_backup_handler")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        log.addHandler(logging.StreamHandler())
    return log


@pytest.fixture
def tmp_dir() -> Path:
    """Isolated temporary directory cleaned up after the test."""
    d = tempfile.mkdtemp()
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    """
    Snapshot environment variables touched by individual tests and restore
    them on teardown. Prevents failures in a single test from leaking state
    into later runs.
    """
    yield
