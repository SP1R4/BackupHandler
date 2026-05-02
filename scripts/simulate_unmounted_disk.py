"""
End-to-end simulation of the 2026-04-16 unmounted-disk failure mode.

This script does NOT modify any real filesystems, sudoers, fstab, or
production config. It builds a temporary directory layout, points the
preflight machinery at it, and demonstrates two scenarios:

  1. FATAL: destination is not a mountpoint, auto-mount cannot recover
     (no sudoers rule available in the simulator). Run aborts with
     exit-style code 2, sentinel records "failure", local mail is
     attempted (will likely fail without a configured MTA — that is
     also what we want to surface).

  2. HEALED: ismount/findmnt are mocked to mimic a successful auto-mount
     of the right LABEL. Preflight reports ok=True and healed=True.

Run with: venv/bin/python scripts/simulate_unmounted_disk.py
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Make `src.*` importable when run from the project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src import preflight  # noqa: E402
from src.preflight import (  # noqa: E402
    PreflightConfig,
    read_status_sentinel,
    run_preflight,
    write_status_sentinel,
)


def _logger() -> logging.Logger:
    log = logging.getLogger("preflight_sim")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        log.addHandler(h)
    return log


def scenario_fatal(log: logging.Logger, workdir: Path) -> int:
    print("\n=== Scenario 1: unmounted destination, no auto-mount possible ===")
    fake_mount = workdir / "mnt" / "data"
    fake_mount.mkdir(parents=True)  # Exists as a regular dir, NOT a mountpoint
    sentinel = workdir / "Logs" / "last_run_status.json"

    cfg = PreflightConfig(
        enabled=True,
        expected_mount=str(fake_mount),
        expected_label="DATA",
        auto_mount=True,
        ensure_fstab=False,
        local_mail_to="root",
        status_sentinel=str(sentinel),
    )

    write_status_sentinel(sentinel, status="started", run_id="sim-1", message="sim start")

    # Force the auto-mount path to fail (simulator has no sudoers rule).
    with mock.patch.object(preflight, "_try_mount", return_value=False):
        result = run_preflight(log, cfg, backup_dirs=[str(fake_mount)], interval_minutes=60)

    print(f"-> ok={result.ok} fatal={result.fatal} message={result.message!r}")
    if not result.ok:
        write_status_sentinel(
            sentinel,
            status="failure",
            run_id="sim-1",
            message=f"preflight: {result.message}",
        )

    data = read_status_sentinel(sentinel)
    print(f"-> sentinel: {data}")
    assert result.ok is False and result.fatal is True, "Scenario 1 must abort"
    assert data and data["status"] == "failure", "Sentinel must record failure"
    return 0


def scenario_healed(log: logging.Logger, workdir: Path) -> int:
    print("\n=== Scenario 2: unmounted destination, auto-mount succeeds ===")
    fake_mount = workdir / "mnt" / "data2"
    fake_mount.mkdir(parents=True)
    sentinel = workdir / "Logs" / "last_run_status_2.json"

    cfg = PreflightConfig(
        enabled=True,
        expected_mount=str(fake_mount),
        expected_label="DATA",
        auto_mount=True,
        ensure_fstab=False,
        status_sentinel=str(sentinel),
    )

    # First ismount call returns False (disk gone), second returns True (remounted).
    ismount_state = {"n": 0}

    def fake_ismount(_):
        ismount_state["n"] += 1
        return ismount_state["n"] > 1

    write_status_sentinel(sentinel, status="started", run_id="sim-2")

    with (
        mock.patch("os.path.ismount", side_effect=fake_ismount),
        mock.patch.object(preflight, "_try_mount", return_value=True) as m_mount,
        mock.patch.object(preflight, "_findmnt_source", return_value="/dev/sdb1"),
        mock.patch.object(preflight, "_device_label", return_value="DATA"),
        mock.patch.object(preflight, "_device_uuid", return_value="aaaa-bbbb"),
        mock.patch.object(preflight, "_fstab_has_entry", return_value=True),
    ):
        result = run_preflight(log, cfg, backup_dirs=[str(fake_mount)], interval_minutes=60)

    print(f"-> ok={result.ok} healed={result.healed} message={result.message!r}")
    print(f"-> mount attempt invoked: {m_mount.call_count} time(s)")

    if result.ok:
        write_status_sentinel(sentinel, status="success", run_id="sim-2", message=result.message)

    data = read_status_sentinel(sentinel)
    print(f"-> sentinel: {data}")
    assert result.ok is True and result.healed is True, "Scenario 2 must self-heal"
    assert data and data["status"] == "success", "Sentinel must record success"
    return 0


def scenario_wrong_volume(log: logging.Logger, workdir: Path) -> int:
    print("\n=== Scenario 3: mountpoint mounted but WRONG volume ===")
    # Tests the case where /mnt/data is somehow mounted (e.g. from rootfs
    # bind mount or a stale loopback) but the device label is not DATA.
    # Backup must refuse to write to avoid corrupting the wrong disk.
    fake_mount = workdir / "mnt" / "data3"
    fake_mount.mkdir(parents=True)
    sentinel = workdir / "Logs" / "last_run_status_3.json"

    cfg = PreflightConfig(
        enabled=True,
        expected_mount=str(fake_mount),
        expected_label="DATA",
        auto_mount=True,
        ensure_fstab=False,
        status_sentinel=str(sentinel),
    )

    with (
        mock.patch("os.path.ismount", return_value=True),
        mock.patch.object(preflight, "_findmnt_source", return_value="/dev/sda1"),
        mock.patch.object(preflight, "_device_label", return_value="ROOT"),
        mock.patch.object(preflight, "_device_uuid", return_value=None),
    ):
        result = run_preflight(log, cfg, backup_dirs=[str(fake_mount)], interval_minutes=60)

    print(f"-> ok={result.ok} fatal={result.fatal} message={result.message!r}")
    assert result.ok is False and result.fatal is True
    assert "WRONG volume" in result.message
    return 0


def main() -> int:
    log = _logger()
    with tempfile.TemporaryDirectory(prefix="preflight_sim_") as tmp:
        workdir = Path(tmp)
        print(f"Simulator workdir: {workdir}")
        scenario_fatal(log, workdir)
        scenario_healed(log, workdir)
        scenario_wrong_volume(log, workdir)
    print("\nAll scenarios passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
