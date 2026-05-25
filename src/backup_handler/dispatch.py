"""
dispatch.py - Early-exit subcommand handlers.

Each handler in this module corresponds to a CLI flag that causes the
process to exit before the main backup pipeline runs (``--install``,
``--status``, ``--verify``, ``--snapshot``, ``--restore-snapshot``,
``--snapshot-diff``, ``--restore``). Lifted out of cli.py so the
entrypoint reads as a routing table, not a 200-line if/elif chain.

Each handler returns the integer exit code the process should use (or
calls ``sys.exit`` directly on hard validation errors).
"""

from __future__ import annotations

import sys
from pathlib import Path

from ._paths import PROJECT_ROOT
from .config import extract_config_values
from .installer import run_installer
from .restore import restore_backup
from .snapshot import create_snapshot, diff_snapshots, generate_restore_script
from .status import show_status
from .verify import print_verify_report, verify_backup_integrity


def handle_install(logger, args, config_path: str) -> int:
    """Run --install bootstrap and return the installer's exit code."""
    try:
        install_config = extract_config_values(logger, config_path, skip_validation=True)
    except Exception as e:
        logger.error(f"Cannot load config for installer: {e}")
        return 1
    return run_installer(install_config, PROJECT_ROOT, dry_run=args.dry_run)


def handle_status(logger, config_path: str) -> int:
    show_status(logger, config_path)
    return 0


def handle_verify(logger, args, config_path: str) -> int:
    try:
        verify_config = extract_config_values(logger, config_path, skip_validation=True)
    except Exception:
        verify_config = {}
    backup_dirs = args.backup_dirs or verify_config.get("backup_dirs", [])
    if not backup_dirs:
        logger.error(
            "No backup directories to verify. Specify --backup-dirs or configure [BACKUPS] backup_dirs."
        )
        return 1
    results = verify_backup_integrity(
        logger,
        backup_dirs,
        encryption_passphrase=verify_config.get("encryption_passphrase"),
        encryption_key_file=verify_config.get("encryption_key_file"),
    )
    return 0 if print_verify_report(results) else 1


def handle_snapshot(logger, args) -> int:
    output_path = args.snapshot_output or str(PROJECT_ROOT / "snapshots")
    snapshot_file = create_snapshot(logger, output_dir=output_path)
    print(f"\nSnapshot saved to: {snapshot_file}")
    return 0


def handle_restore_snapshot(logger, args) -> int:
    output = args.snapshot_output
    if output is None:
        snapshot_name = Path(args.restore_snapshot).stem
        output = str(PROJECT_ROOT / "snapshots" / f"{snapshot_name}_restore.sh")
    script_path = generate_restore_script(logger, args.restore_snapshot, output_path=output)
    if not script_path:
        print("Failed to generate restore script.", file=sys.stderr)
        return 1
    print(f"\nRestore script generated: {script_path}")
    print("Review it, then run: chmod +x restore.sh && sudo ./restore.sh")
    return 0


def handle_snapshot_diff(logger, args) -> int:
    diff = diff_snapshots(logger, args.snapshot_diff[0], args.snapshot_diff[1])
    if not diff:
        print("\nNo differences found between snapshots.")
        return 0
    print("\n=== Snapshot Diff ===\n")
    for category, changes in diff.items():
        print(f"  {category}:")
        for item in changes.get("added", []):
            print(f"    + {item}")
        for item in changes.get("removed", []):
            print(f"    - {item}")
        print()
    return 0


def handle_restore(logger, args, config_path: str) -> int:
    try:
        restore_config = extract_config_values(logger, config_path, skip_validation=True)
    except Exception:
        restore_config = {}

    logger.info(f"Restoring from {args.from_dir} to {args.to_dir}")
    success = restore_backup(
        logger,
        args.from_dir,
        args.to_dir,
        timestamp=args.restore_timestamp,
        encryption_passphrase=restore_config.get("encryption_passphrase"),
        encryption_key_file=restore_config.get("encryption_key_file"),
        ssh_password=restore_config.get("ssh_password"),
        s3_region=restore_config.get("s3_region"),
        s3_access_key=restore_config.get("s3_access_key"),
        s3_secret_key=restore_config.get("s3_secret_key"),
        dry_run=args.dry_run,
    )
    if success:
        logger.info("Restore completed successfully.")
        print("Restore completed successfully.")
        return 0
    logger.error("Restore completed with errors.")
    print("Restore completed with errors.", file=sys.stderr)
    return 1
