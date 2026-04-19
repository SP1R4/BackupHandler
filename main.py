"""
main.py - Backup Handler CLI Entry Point and Orchestrator

Central entry point that coordinates the entire backup pipeline:
  1. Parse CLI arguments and resolve configuration
  2. Execute early-exit commands (--status, --verify, --restore, --show-setup)
  3. Run pre-backup hooks
  4. Execute selected backup modes (local, SSH, S3, database)
  5. Save manifests, encrypt, deduplicate, and apply retention policies
  6. Run post-backup hooks and send notifications
  7. Support scheduled mode with configurable times and graceful shutdown

All backup operations are orchestrated through ``backup_operation()`` which
handles both one-off CLI invocations and scheduled recurring runs.
"""

import atexit
import contextlib
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from colorama import init

from banner.banner_show import print_banner
from bot.BotHandler import TelegramBot
from src.argparse_setup import setup_argparse, validate_args
from src.config import extract_config_values
from src.db_sync import perform_db_backup
from src.dedup import deduplicate_backup_dirs
from src.email_notify import send_smtp_email
from src.encryption import encrypt_directory
from src.heartbeat import send_heartbeat

# ─── Internal Module Imports ────────────────────────────────────────────────
from src.logger import AppLogger
from src.manifest import BackupManifest, load_latest_manifest
from src.restore import restore_backup
from src.retention import cleanup_old_backups
from src.s3_sync import sync_to_s3
from src.snapshot import create_snapshot, diff_snapshots, generate_restore_script
from src.sync import (
    perform_differential_backup,
    perform_full_backup,
    perform_incremental_backup,
    sync_ssh_servers_concurrently,
)
from src.tailscale import tailscale_down, tailscale_up
from src.utils import (
    get_last_backup_time,
    get_last_full_backup_time,
    run_hook,
    update_last_backup_time,
    update_last_full_backup_time,
)
from src.verify import print_verify_report, verify_backup_integrity
from src.webhook_notify import send_webhook

# ─── Project Paths ──────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = str(_PROJECT_ROOT / "config" / "config.ini")
LOG_PATH = str(_PROJECT_ROOT / "Logs" / "application.log")
LOCK_FILE = _PROJECT_ROOT / ".backup-handler.lock"


# ─── Instance Locking ───────────────────────────────────────────────────────


def _proc_looks_like_backup_handler(pid: int) -> bool:
    """
    Confirm that a PID corresponds to a backup-handler process.

    PIDs are recycled by the OS. A stale lock file can point at an
    unrelated process that happens to share the old PID. Cross-check
    ``/proc/<pid>/comm`` and ``/proc/<pid>/cmdline`` before trusting it.
    Returns True only when the process's identifiers reference python or
    the backup-handler entry point.
    """
    comm = Path(f"/proc/{pid}/comm")
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        comm_value = comm.read_text().strip().lower() if comm.exists() else ""
        cmdline_value = cmdline.read_text().replace("\x00", " ").lower() if cmdline.exists() else ""
    except OSError:
        return False
    hints = ("python", "backup-handler", "main.py")
    return any(h in comm_value or h in cmdline_value for h in hints)


def _acquire_lock(logger):
    """
    Acquire a PID lock file to prevent duplicate scheduled instances.

    Checks if an existing lock file references a still-running backup-handler
    process. Stale lock files (from crashed instances or recycled PIDs) are
    automatically cleaned up. Registers ``_release_lock`` via ``atexit``.
    """
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            os.kill(old_pid, 0)
        except (ValueError, ProcessLookupError, PermissionError):
            logger.warning("Removing stale lock file (PID in file no longer running).")
        else:
            if _proc_looks_like_backup_handler(old_pid):
                logger.error(
                    f"Another backup-handler instance is already running (PID {old_pid}). "
                    f"Remove {LOCK_FILE} if this is incorrect."
                )
                sys.exit(1)
            logger.warning(
                f"Lock file references PID {old_pid} but that process is not "
                f"backup-handler (likely recycled). Reclaiming the lock."
            )

    LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)


def _release_lock():
    """Remove the PID lock file on exit."""
    with contextlib.suppress(OSError):
        LOCK_FILE.unlink(missing_ok=True)


# Initialize colorama with autoreset to ensure color codes are reset after each print
init(autoreset=True)


# ─── Configuration Resolution ───────────────────────────────────────────────


def _resolve_config_path(args):
    """
    Resolve the configuration file path from CLI arguments.

    Priority: ``--profile`` > ``--config`` > default ``config/config.ini``.
    Profiles resolve to ``config/config.<name>.ini``.
    """
    if args.profile:
        profile_path = str(_PROJECT_ROOT / "config" / f"config.{args.profile}.ini")
        if not os.path.exists(profile_path):
            print(f"Error: Profile config not found: {profile_path}", file=sys.stderr)
            sys.exit(1)
        return profile_path
    if args.config and os.path.exists(args.config):
        return args.config
    return CONFIG_PATH


# ─── Status Dashboard ───────────────────────────────────────────────────────


def show_status(logger, config_path):
    """
    Display a backup status dashboard including last backup timestamps,
    scheduled times, backup directory sizes, and latest manifest summary.
    """
    print("\n=== Backup Status ===\n")

    # Last backup timestamps
    last_backup = get_last_backup_time()
    last_full = get_last_full_backup_time()

    if last_backup:
        print(f"Last backup:      {datetime.fromtimestamp(last_backup).strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("Last backup:      Never")

    if last_full:
        print(f"Last full backup: {datetime.fromtimestamp(last_full).strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("Last full backup: Never")

    # Load config for schedule and backup dirs
    try:
        config_values = extract_config_values(logger, config_path, skip_validation=True)
    except Exception:
        config_values = {}

    # Scheduled times
    schedule_times = config_values.get("schedule_times", [])
    if schedule_times:
        print(f"\nScheduled times: {', '.join(schedule_times)}")
    else:
        print("\nScheduled times: Not configured")

    # Backup directory sizes
    backup_dirs = config_values.get("backup_dirs", [])
    if backup_dirs:
        print("\nBackup directories:")
        for bdir in backup_dirs:
            bpath = Path(bdir)
            if bpath.exists():
                total_size = sum(f.stat().st_size for f in bpath.rglob("*") if f.is_file())
                # Human-readable size
                if total_size >= 1073741824:
                    size_str = f"{total_size / 1073741824:.2f} GB"
                elif total_size >= 1048576:
                    size_str = f"{total_size / 1048576:.2f} MB"
                elif total_size >= 1024:
                    size_str = f"{total_size / 1024:.2f} KB"
                else:
                    size_str = f"{total_size} B"
                print(f"  {bdir}: {size_str}")
            else:
                print(f"  {bdir}: (not found)")

    # Latest manifest summary
    if backup_dirs:
        print("\nLatest manifest:")
        found_manifest = False
        for bdir in backup_dirs:
            manifest = load_latest_manifest(bdir)
            if manifest:
                found_manifest = True
                print(f"  Directory: {bdir}")
                print(f"    Timestamp: {manifest.get('timestamp', 'Unknown')}")
                print(f"    Mode:      {manifest.get('mode', 'Unknown')}")
                print(f"    Duration:  {manifest.get('duration_seconds', 0):.1f}s")
                print(f"    Copied:    {manifest.get('files_copied', 0)} files")
                print(f"    Skipped:   {manifest.get('files_skipped', 0)} files")
                print(f"    Failed:    {manifest.get('files_failed', 0)} files")
                total_bytes = manifest.get("total_bytes", 0)
                if total_bytes >= 1048576:
                    print(f"    Size:      {total_bytes / 1048576:.2f} MB")
                else:
                    print(f"    Size:      {total_bytes / 1024:.2f} KB")
                break
        if not found_manifest:
            print("  No manifests found")

    print()


# ─── Main Entry Point ───────────────────────────────────────────────────────


def main():
    """CLI entry point — parses arguments, routes to the appropriate operation."""
    print_banner()

    # Set up logging using AppLogger
    logger = AppLogger(LOG_PATH, logging.DEBUG).logger
    args = setup_argparse()

    # Validate the parsed arguments
    validate_args(args, logger)

    # Resolve config path (--config, --profile, or default)
    config_path = _resolve_config_path(args)

    # Handle --status early exit
    if args.status:
        show_status(logger, config_path)
        return

    # Handle --verify early exit
    if args.verify:
        try:
            verify_config = extract_config_values(logger, config_path, skip_validation=True)
        except Exception:
            verify_config = {}
        backup_dirs = args.backup_dirs or verify_config.get("backup_dirs", [])
        if not backup_dirs:
            logger.error(
                "No backup directories to verify. Specify --backup-dirs or configure [BACKUPS] backup_dirs."
            )
            sys.exit(1)
        enc_passphrase = verify_config.get("encryption_passphrase")
        enc_key_file = verify_config.get("encryption_key_file")
        results = verify_backup_integrity(
            logger, backup_dirs, encryption_passphrase=enc_passphrase, encryption_key_file=enc_key_file
        )
        all_ok = print_verify_report(results)
        sys.exit(0 if all_ok else 1)

    # Handle --snapshot early exit
    if args.snapshot:
        output_path = args.snapshot_output or str(_PROJECT_ROOT / "snapshots")
        snapshot_file = create_snapshot(logger, output_dir=output_path)
        print(f"\nSnapshot saved to: {snapshot_file}")
        return

    # Handle --restore-snapshot early exit
    if args.restore_snapshot:
        output = args.snapshot_output
        if output is None:
            snapshot_name = Path(args.restore_snapshot).stem
            output = str(_PROJECT_ROOT / "snapshots" / f"{snapshot_name}_restore.sh")
        script_path = generate_restore_script(logger, args.restore_snapshot, output_path=output)
        if script_path:
            print(f"\nRestore script generated: {script_path}")
            print("Review it, then run: chmod +x restore.sh && sudo ./restore.sh")
        else:
            print("Failed to generate restore script.", file=sys.stderr)
            sys.exit(1)
        return

    # Handle --snapshot-diff early exit
    if args.snapshot_diff:
        diff = diff_snapshots(logger, args.snapshot_diff[0], args.snapshot_diff[1])
        if not diff:
            print("\nNo differences found between snapshots.")
        else:
            print("\n=== Snapshot Diff ===\n")
            for category, changes in diff.items():
                added = changes.get("added", [])
                removed = changes.get("removed", [])
                print(f"  {category}:")
                for item in added:
                    print(f"    + {item}")
                for item in removed:
                    print(f"    - {item}")
                print()
        return

    # Handle --restore early exit
    if args.restore:
        # Load config to get encryption/SSH/S3 params for restore
        try:
            restore_config = extract_config_values(logger, config_path, skip_validation=True)
        except Exception:
            restore_config = {}
        enc_passphrase = restore_config.get("encryption_passphrase")
        enc_key_file = restore_config.get("encryption_key_file")

        logger.info(f"Restoring from {args.from_dir} to {args.to_dir}")
        success = restore_backup(
            logger,
            args.from_dir,
            args.to_dir,
            timestamp=args.restore_timestamp,
            encryption_passphrase=enc_passphrase,
            encryption_key_file=enc_key_file,
            ssh_password=restore_config.get("ssh_password"),
            s3_region=restore_config.get("s3_region"),
            s3_access_key=restore_config.get("s3_access_key"),
            s3_secret_key=restore_config.get("s3_secret_key"),
            dry_run=args.dry_run,
        )
        if success:
            logger.info("Restore completed successfully.")
            print("Restore completed successfully.")
        else:
            logger.error("Restore completed with errors.")
            print("Restore completed with errors.", file=sys.stderr)
            sys.exit(1)
        return

    # Initialize TelegramBot if --notifications flag is used
    telegram_bot = None
    if args.notifications:
        try:
            telegram_bot = TelegramBot(logger)
        except FileNotFoundError:
            logger.error(
                "Telegram bot config not found. Create config/bot_config.ini from config/bot_config.ini.example"
            )
            print(
                "Error: config/bot_config.ini not found. Copy config/bot_config.ini.example and fill in your values.",
                file=sys.stderr,
            )
            sys.exit(1)
        except KeyError as e:
            logger.error(
                f"Missing key in bot_config.ini: {e}. Check that [TELEGRAM] api_token and [USERS] interacted_users are set."
            )
            print(
                f"Error: Missing key in config/bot_config.ini: {e}. Ensure api_token and interacted_users are set.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Use the provided receiver emails if notifications are enabled
    receiver_emails = args.receiver if args.notifications else None

    # Parse exclude patterns from CLI (overrides config)
    exclude_patterns = None
    if args.exclude:
        exclude_patterns = [p.strip() for p in args.exclude.split(",") if p.strip()]

    if args.scheduled:
        try:
            scheduled_operation(
                logger,
                config_path,
                telegram_bot=telegram_bot,
                exclude_patterns=exclude_patterns,
                retain=args.retain,
            )
        except Exception as e:
            logger.error(f"Failed to load configuration file: {config_path}. Error: {e}")
            sys.exit(1)
    else:
        rc = backup_operation(
            logger,
            source_dir=args.source_dir,
            backup_dirs=args.backup_dirs,
            ssh_servers=args.ssh_servers,
            operation_modes=args.operation_modes,
            backup_mode=args.backup_mode,
            compress=args.compress,
            receiver=receiver_emails,
            show_setup=args.show_setup,
            notifications=args.notifications,
            telegram_bot=telegram_bot,
            dry_run=args.dry_run,
            exclude_patterns=exclude_patterns,
            retain=args.retain,
            config_path=config_path,
            encrypt=args.encrypt,
            dedup=args.dedup,
            tailscale=args.tailscale,
            tailscale_authkey=args.tailscale_authkey,
        )
        if rc:
            sys.exit(rc)


# ─── Scheduled Mode ─────────────────────────────────────────────────────────


def scheduled_operation(logger, config_file, telegram_bot=None, exclude_patterns=None, retain=None):
    """
    Run backups on a configurable schedule with graceful shutdown support.

    Acquires a PID lock to prevent duplicate instances, then enters a polling
    loop that checks the current time against configured schedule times every
    30 seconds (matching the ±30s tolerance window). Handles SIGINT/SIGTERM
    for clean shutdown.

    Parameters:
        logger: Logger instance.
        config_file (str): Path to the INI configuration file.
        telegram_bot (TelegramBot, optional): Telegram bot for notifications.
        exclude_patterns (list, optional): Glob patterns to exclude.
        retain (int, optional): CLI override for max_count retention policy.
    """
    _acquire_lock(logger)

    # Handle SIGINT/SIGTERM for clean shutdown
    _shutdown_requested = False

    def _handle_shutdown(signum, frame):
        nonlocal _shutdown_requested
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down scheduler gracefully...")
        _shutdown_requested = True

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    try:
        # Loading the config file (with schedule validation)
        config_values = extract_config_values(logger, config_file, require_schedule=True)

        # Access the schedule times and interval
        times = config_values.get("schedule_times", [])
        config_values.get("interval_minutes", 60)
        # Ensure all times are in the correct format
        scheduled_times = []
        for t in times:
            try:
                scheduled_times.append(datetime.strptime(t, "%H:%M").time())
            except ValueError:
                logger.error(f"Time format error for value: {t}")
                continue

        # Use CLI exclude patterns if provided, otherwise use config
        if exclude_patterns is None:
            exclude_patterns = config_values.get("exclude_patterns", [])

        logger.info(f"Scheduled times: {scheduled_times}")
        while not _shutdown_requested:
            now = datetime.now()
            current_time = now.time()
            logger.info(f"Current time: {current_time}")

            # Check for matching scheduled time within a ±30 second tolerance
            matched = False
            for scheduled_time in scheduled_times:
                scheduled_dt = now.replace(
                    hour=scheduled_time.hour, minute=scheduled_time.minute, second=0, microsecond=0
                )
                diff = abs((now - scheduled_dt).total_seconds())
                if diff <= 30:
                    matched = True
                    break
            if matched:
                logger.info("Scheduled time matched. Performing backup operation...")
                # Pre-flight: verify backup directories are accessible
                sched_backup_dirs = config_values.get("backup_dirs", [])
                if sched_backup_dirs:
                    inaccessible = _check_backup_dirs_accessible(logger, sched_backup_dirs)
                    if inaccessible:
                        msg = (
                            f"Scheduled backup aborted: destination(s) inaccessible: "
                            f"{', '.join(inaccessible)}. Check that the disk is mounted."
                        )
                        logger.error(msg)
                        if telegram_bot:
                            try:
                                telegram_bot.send_notification(msg)
                            except Exception as e:
                                logger.error(f"Failed to send Telegram notification: {e}")
                        time.sleep(30)
                        continue
                # Build operation_modes from config flags
                operation_modes = []
                if config_values.get("local_mode"):
                    operation_modes.append("local")
                if config_values.get("ssh_mode"):
                    operation_modes.append("ssh")
                if config_values.get("s3_mode"):
                    operation_modes.append("s3")
                if config_values.get("db_mode"):
                    operation_modes.append("db")
                rc = backup_operation(
                    logger,
                    source_dir=config_values["source_dir"],
                    backup_dirs=config_values["backup_dirs"],
                    ssh_servers=config_values.get("ssh_servers"),
                    operation_modes=operation_modes,
                    backup_mode=config_values["mode"],
                    compress=config_values["compress_type"],
                    receiver=config_values["receiver_emails"],
                    notifications=bool(telegram_bot),
                    telegram_bot=telegram_bot,
                    ssh_username=config_values.get("ssh_username"),
                    ssh_password=config_values.get("ssh_password"),
                    exclude_patterns=exclude_patterns,
                    retain=retain,
                    config_path=None,
                    config_values=config_values,
                )
                if rc:
                    logger.error(f"Scheduled run returned exit code {rc}; scheduler continues.")
            else:
                logger.info("No scheduled time matched.")
            # Wait for 30 seconds before checking again (matches tolerance window)
            time.sleep(30)

        logger.info("Scheduler stopped cleanly.")

    except Exception as e:
        logger.error(f"Error in scheduled_operation: {e}")
        sys.exit(1)


# ─── Notification Helpers ───────────────────────────────────────────────────


def _notify(logger, telegram_bot, notifications, message, config_values=None):
    """
    Dispatch notifications via all configured channels (Telegram and SMTP).

    Telegram notifications require the ``--notifications`` flag and a valid bot.
    SMTP notifications are sent when ``[SMTP]`` host and recipients are configured
    in ``config_values``, regardless of the ``--notifications`` flag.
    """
    if notifications and telegram_bot:
        try:
            telegram_bot.send_notification(message)
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    # Webhook notification
    if config_values:
        webhook_url = config_values.get("webhook_url")
        if webhook_url:
            try:
                headers = {}
                auth_header = config_values.get("webhook_auth_header")
                if auth_header:
                    headers["Authorization"] = auth_header
                send_webhook(logger, webhook_url, message, headers=headers or None)
            except Exception as e:
                logger.error(f"Failed to send webhook notification: {e}")

    # SMTP email notification
    if config_values:
        smtp_host = config_values.get("smtp_host")
        smtp_to = config_values.get("smtp_to", [])
        if smtp_host and smtp_to:
            try:
                send_smtp_email(
                    logger,
                    smtp_host=smtp_host,
                    smtp_port=config_values.get("smtp_port", 587),
                    smtp_user=config_values.get("smtp_user"),
                    smtp_password=config_values.get("smtp_password"),
                    from_addr=config_values.get("smtp_from", config_values.get("smtp_user", "")),
                    to_addrs=smtp_to,
                    subject=f"Backup Handler: {message[:50]}",
                    body=message,
                    use_tls=config_values.get("smtp_tls", True),
                )
            except Exception as e:
                logger.error(f"Failed to send SMTP notification: {e}")


def _run_backup(logger, telegram_bot, notifications, mode_name, backup_fn, config_values=None):
    """
    Execute a backup function with standardized notification and error handling.

    Wraps the actual backup call in a try/except to ensure failure notifications
    are always sent, even if the backup raises an unexpected exception. Returns
    True on success, False on failure so the caller can track partial failures
    and propagate a non-zero exit code.
    """
    try:
        backup_fn()
        _notify(
            logger,
            telegram_bot,
            notifications,
            f"Local {mode_name} backup completed.",
            config_values=config_values,
        )
        return True
    except Exception as e:
        logger.error(f"{mode_name.capitalize()} backup failed: {e}")
        _notify(
            logger,
            telegram_bot,
            notifications,
            f"{mode_name.capitalize()} backup failed.",
            config_values=config_values,
        )
        return False


# ─── Pre-flight Checks ─────────────────────────────────────────────────────


def _check_backup_dirs_accessible(logger, backup_dirs):
    """
    Verify that backup directories are accessible before starting a backup.

    For paths under a mount point (e.g. /mnt/*), checks that the mount point
    is actually mounted. Also ensures each backup directory exists or can be
    created. Returns a list of inaccessible directories (empty = all OK).
    """
    inaccessible = []
    for bdir in backup_dirs or []:
        bpath = Path(bdir)
        # Check if the path is under a mount point (e.g. /mnt/data/...)
        parts = bpath.parts
        if len(parts) >= 3 and parts[1] == "mnt":
            mount_point = Path("/") / parts[1] / parts[2]  # e.g. /mnt/data
            if not os.path.ismount(str(mount_point)):
                logger.error(
                    f"Mount point {mount_point} is not mounted. Backup directory {bdir} is inaccessible."
                )
                inaccessible.append(bdir)
                continue
        # Check if the directory exists or its parent is writable
        if not bpath.exists():
            try:
                bpath.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created backup directory: {bdir}")
            except OSError as e:
                logger.error(f"Cannot create backup directory {bdir}: {e}")
                inaccessible.append(bdir)
    return inaccessible


# ─── Core Backup Pipeline ───────────────────────────────────────────────────


def backup_operation(
    logger,
    source_dir=None,
    backup_dirs=None,
    ssh_servers=None,
    operation_modes=None,
    backup_mode=None,
    compress=None,
    receiver=None,
    show_setup=False,
    notifications=False,
    telegram_bot=None,
    ssh_username=None,
    ssh_password=None,
    dry_run=False,
    exclude_patterns=None,
    retain=None,
    config_path=None,
    config_values=None,
    encrypt=False,
    dedup=False,
    tailscale=False,
    tailscale_authkey=None,
):
    """
    Orchestrate the full backup pipeline for a single run.

    Execution order:
      1. Pre-backup hook (failure aborts the run)
      2. Local / SSH / S3 / Database backup modes (based on ``operation_modes``)
      3. Save backup manifests to each backup directory
      4. Encrypt backup files (AES-256-GCM, if enabled)
      5. Deduplicate via hardlinks (if enabled)
      6. Update backup timestamps
      7. Apply retention policies (age-based and count-based)
      8. Post-backup hook (failure is logged but does not affect backup status)
      9. Send completion notification

    All parameters can be sourced from CLI args, config file, or both (CLI wins).

    Returns a process exit code:
      0 - all selected modes succeeded (or dry-run / show-setup completed)
      2 - pre-flight failure (backup dir inaccessible, pre-hook failed)
      3 - at least one backup mode failed
    Callers (main / scheduled_operation) decide whether to ``sys.exit()`` or
    continue looping.
    """
    # Show setup command (skip validation so incomplete configs can be inspected)
    if show_setup:
        extract_config_values(logger, config_path or CONFIG_PATH, show=True, skip_validation=True)
        return 0

    # Load config values if not provided (for hooks, retention, parallel, bandwidth, S3)
    if config_values is None and config_path:
        try:
            config_values = extract_config_values(logger, config_path, skip_validation=True)
        except Exception:
            config_values = {}

    if config_values is None:
        config_values = {}

    # Use config exclude patterns if CLI didn't provide them
    if exclude_patterns is None:
        exclude_patterns = config_values.get("exclude_patterns", [])

    # Pre-flight: verify backup directories are accessible
    if backup_dirs and not dry_run and not show_setup:
        inaccessible = _check_backup_dirs_accessible(logger, backup_dirs)
        if inaccessible:
            msg = (
                f"Backup aborted: destination(s) inaccessible: {', '.join(inaccessible)}. "
                f"Check that the disk is mounted."
            )
            logger.error(msg)
            _notify(logger, telegram_bot, notifications, msg, config_values=config_values)
            return 2

    # Hooks
    pre_hook = config_values.get("pre_backup_hook")
    post_hook = config_values.get("post_backup_hook")

    # Retention (CLI --retain overrides config max_count)
    max_age_days = config_values.get("max_age_days", 0)
    max_count = retain if retain is not None else config_values.get("max_count", 0)

    # Parallel copies
    parallel_copies = config_values.get("parallel_copies", 1)

    # Bandwidth limit
    bandwidth_limit = config_values.get("bandwidth_limit", 0)

    # S3 config
    s3_bucket = config_values.get("s3_bucket")
    s3_prefix = config_values.get("s3_prefix", "")
    s3_region = config_values.get("s3_region")
    s3_access_key = config_values.get("s3_access_key")
    s3_secret_key = config_values.get("s3_secret_key")

    # Run pre-backup hook
    if pre_hook and not run_hook(logger, pre_hook, "pre_backup"):
        logger.error("Pre-backup hook failed. Aborting backup.")
        _notify(
            logger,
            telegram_bot,
            notifications,
            "Backup aborted: pre-backup hook failed.",
            config_values=config_values,
        )
        return 2

    # Track per-mode failures so we can exit non-zero if any mode failed.
    # Systemd and Prometheus rely on this to page an operator.
    mode_failures: list[str] = []

    # Create manifest for this backup run
    manifest = BackupManifest(mode=backup_mode or "full")

    # Execute selected backup modes
    if operation_modes is None:
        operation_modes = []
    if "local" in operation_modes:
        if not backup_dirs:
            logger.warning("Local mode selected but no backup directories specified. Skipping local backup.")
        elif dry_run:
            logger.info(
                f"[DRY RUN] Would perform {backup_mode or 'full'} backup: '{source_dir}' -> {backup_dirs}"
            )
            print(f"[DRY RUN] Would perform {backup_mode or 'full'} backup")
            print(f"  Source:      {source_dir}")
            print(f"  Destinations: {', '.join(backup_dirs)}")
            if compress:
                print(f"  Compression: {compress}")
            if exclude_patterns:
                print(f"  Excluding:   {', '.join(exclude_patterns)}")
        elif backup_mode == "incremental":
            if compress:
                logger.error("Invalid option for incremental backup.")
                sys.exit(1)
            last_backup_time = get_last_backup_time()
            if not _run_backup(
                logger,
                telegram_bot,
                notifications,
                "incremental",
                lambda: perform_incremental_backup(
                    logger,
                    source_dir,
                    backup_dirs,
                    last_backup_time,
                    bot=telegram_bot,
                    receiver_emails=receiver,
                    exclude_patterns=exclude_patterns,
                    manifest=manifest,
                ),
                config_values=config_values,
            ):
                mode_failures.append("local-incremental")
        elif backup_mode == "differential":
            if compress:
                logger.error("Invalid option for differential backup.")
                sys.exit(1)
            last_full_backup_time = get_last_full_backup_time()
            if not _run_backup(
                logger,
                telegram_bot,
                notifications,
                "differential",
                lambda: perform_differential_backup(
                    logger,
                    source_dir,
                    backup_dirs,
                    last_full_backup_time,
                    bot=telegram_bot,
                    receiver_emails=receiver,
                    exclude_patterns=exclude_patterns,
                    manifest=manifest,
                ),
                config_values=config_values,
            ):
                mode_failures.append("local-differential")
        else:
            _notify(
                logger, telegram_bot, notifications, "Starting full backup...", config_values=config_values
            )
            if _run_backup(
                logger,
                telegram_bot,
                notifications,
                "full",
                lambda: perform_full_backup(
                    logger,
                    source_dir,
                    backup_dirs,
                    compress=compress,
                    bot=telegram_bot,
                    receiver_emails=receiver,
                    exclude_patterns=exclude_patterns,
                    manifest=manifest,
                    parallel_copies=parallel_copies,
                ),
                config_values=config_values,
            ):
                update_last_full_backup_time()
            else:
                mode_failures.append("local-full")

    # Resolve Tailscale settings (CLI flags override config)
    ts_enabled = tailscale or config_values.get("tailscale_enabled", False)
    ts_auth_key = tailscale_authkey or config_values.get("tailscale_auth_key")
    ts_hostname = config_values.get("tailscale_hostname")
    ts_tags = config_values.get("tailscale_advertise_tags")
    ts_accept_routes = config_values.get("tailscale_accept_routes", False)
    ts_disconnect_after = config_values.get("tailscale_disconnect_after", False)
    _ts_brought_up = False

    if operation_modes and ("ssh" in operation_modes):
        if not ssh_servers:
            logger.warning("SSH mode selected but no SSH servers specified. Skipping SSH backup.")
        elif dry_run:
            logger.info(f"[DRY RUN] Would sync '{source_dir}' to SSH servers: {ssh_servers}")
            print(f"[DRY RUN] Would sync '{source_dir}' to SSH servers: {', '.join(ssh_servers)}")
            if ts_enabled:
                print(
                    f"[DRY RUN] Would connect via Tailscale VPN (auth_key: {'set' if ts_auth_key else 'not set'})"
                )
        else:
            # Bring up Tailscale before SSH if enabled
            if ts_enabled:
                if not ts_auth_key:
                    logger.error(
                        "Tailscale enabled but no auth key provided. "
                        "Set --tailscale-authkey or [TAILSCALE] auth_key in config."
                    )
                    _notify(
                        logger,
                        telegram_bot,
                        notifications,
                        "SSH backup aborted: Tailscale auth key missing.",
                        config_values=config_values,
                    )
                    mode_failures.append("ssh")
                else:
                    _ts_brought_up = tailscale_up(
                        ts_auth_key,
                        logger=logger,
                        hostname=ts_hostname,
                        advertise_tags=ts_tags,
                        accept_routes=ts_accept_routes,
                    )
                    if not _ts_brought_up:
                        logger.error("Failed to establish Tailscale connection. Aborting SSH backup.")
                        _notify(
                            logger,
                            telegram_bot,
                            notifications,
                            "SSH backup aborted: Tailscale connection failed.",
                            config_values=config_values,
                        )
                        mode_failures.append("ssh")

            # Only proceed with SSH if Tailscale is not required or connected successfully
            if not ts_enabled or _ts_brought_up:
                _notify(
                    logger,
                    telegram_bot,
                    notifications,
                    f"Starting SSH backup{' via Tailscale' if ts_enabled else ''}...",
                    config_values=config_values,
                )
                logger.info("Running SSH backup...")
                try:
                    sync_ssh_servers_concurrently(
                        source_dir,
                        ssh_servers,
                        username=ssh_username or "",
                        password=ssh_password,
                        logger=logger,
                        exclude_patterns=exclude_patterns,
                        manifest=manifest,
                        bandwidth_limit=bandwidth_limit,
                    )
                    _notify(
                        logger,
                        telegram_bot,
                        notifications,
                        "SSH backup completed.",
                        config_values=config_values,
                    )
                except Exception as e:
                    logger.error(f"SSH backup failed: {e}")
                    _notify(
                        logger, telegram_bot, notifications, "SSH backup failed.", config_values=config_values
                    )
                    mode_failures.append("ssh")
                finally:
                    # Disconnect Tailscale after SSH backup if configured
                    if ts_enabled and _ts_brought_up and ts_disconnect_after:
                        tailscale_down(logger=logger)

    if operation_modes and ("s3" in operation_modes):
        if not s3_bucket:
            logger.warning("S3 mode selected but no bucket configured. Skipping S3 backup.")
        elif dry_run:
            logger.info(f"[DRY RUN] Would sync '{source_dir}' to s3://{s3_bucket}/{s3_prefix}")
            print(f"[DRY RUN] Would sync '{source_dir}' to s3://{s3_bucket}/{s3_prefix}")
        else:
            _notify(logger, telegram_bot, notifications, "Starting S3 backup...", config_values=config_values)
            logger.info("Running S3 backup...")
            try:
                sync_to_s3(
                    logger,
                    source_dir,
                    s3_bucket,
                    prefix=s3_prefix,
                    region=s3_region,
                    access_key=s3_access_key,
                    secret_key=s3_secret_key,
                    mode=backup_mode or "full",
                    exclude_patterns=exclude_patterns,
                    manifest=manifest,
                    max_bandwidth=config_values.get("s3_max_bandwidth"),
                    multipart_threshold=config_values.get("s3_multipart_threshold"),
                    max_concurrency=config_values.get("s3_max_concurrency"),
                )
                _notify(
                    logger, telegram_bot, notifications, "S3 backup completed.", config_values=config_values
                )
            except Exception as e:
                logger.error(f"S3 backup failed: {e}")
                _notify(logger, telegram_bot, notifications, "S3 backup failed.", config_values=config_values)
                mode_failures.append("s3")

    if operation_modes and ("db" in operation_modes):
        db_database = config_values.get("db_database")
        if not db_database:
            logger.warning(
                "DB mode selected but no database configured in [DATABASE]. Skipping database backup."
            )
        elif dry_run:
            perform_db_backup(logger, config_values, backup_dirs or [], manifest, dry_run=True)
        else:
            _notify(
                logger,
                telegram_bot,
                notifications,
                "Starting database backup...",
                config_values=config_values,
            )
            logger.info("Running database backup...")
            try:
                success = perform_db_backup(logger, config_values, backup_dirs or [], manifest)
                if success:
                    _notify(
                        logger,
                        telegram_bot,
                        notifications,
                        "Database backup completed.",
                        config_values=config_values,
                    )
                else:
                    _notify(
                        logger,
                        telegram_bot,
                        notifications,
                        "Database backup failed.",
                        config_values=config_values,
                    )
                    mode_failures.append("db")
            except Exception as e:
                logger.error(f"Database backup failed: {e}")
                _notify(
                    logger,
                    telegram_bot,
                    notifications,
                    "Database backup failed.",
                    config_values=config_values,
                )
                mode_failures.append("db")

    if dry_run:
        # Show encryption info in dry-run
        dry_encrypt = encrypt or config_values.get("encryption_enabled", False)
        if dry_encrypt:
            enc_method = "key_file" if config_values.get("encryption_key_file") else "passphrase"
            print(f"[DRY RUN] Would encrypt backup files using AES-256-GCM ({enc_method})")
        dry_dedup = dedup or config_values.get("dedup_enabled", False)
        if dry_dedup:
            print("[DRY RUN] Would deduplicate identical files using hardlinks")
        logger.info("[DRY RUN] Complete. No files were modified.")
        print("\n[DRY RUN] Complete. No files were modified.")
        return 0

    # Save manifest to each backup directory
    if backup_dirs:
        for bdir in backup_dirs:
            try:
                manifest_path = manifest.save(bdir)
                logger.info(f"Backup manifest saved to {manifest_path}")
            except Exception as e:
                logger.error(f"Failed to save manifest to {bdir}: {e}")

    # Warn about compression + encryption interaction
    if compress and compress != "none":
        enc_check = encrypt or config_values.get("encryption_enabled", False)
        if enc_check:
            logger.warning(
                "Both compression and encryption are enabled. "
                "Encrypted data does not compress well — compression runs first, "
                "then encryption is applied to the compressed archive."
            )

    # Encrypt backup files (after manifest save, before retention)
    encryption_enabled = encrypt or config_values.get("encryption_enabled", False)
    enc_passphrase = config_values.get("encryption_passphrase")
    enc_key_file = config_values.get("encryption_key_file")

    enc_workers = config_values.get("encryption_workers", 1)

    if encryption_enabled and backup_dirs:
        if not enc_passphrase and not enc_key_file:
            logger.error("Encryption enabled but no passphrase or key_file configured in [ENCRYPTION].")
        else:
            for bdir in backup_dirs:
                try:
                    count = encrypt_directory(
                        bdir,
                        passphrase=enc_passphrase,
                        key_file=enc_key_file,
                        logger=logger,
                        workers=enc_workers,
                    )
                    logger.info(f"Encrypted {count} files in {bdir}")
                except Exception as e:
                    logger.error(f"Encryption failed for {bdir}: {e}")

    # Deduplicate backup files (after encryption, before retention)
    dedup_enabled = dedup or config_values.get("dedup_enabled", False)
    if dedup_enabled and backup_dirs:
        try:
            dedup_result = deduplicate_backup_dirs(logger, backup_dirs)
            if dedup_result["duplicates_found"] > 0:
                logger.info(
                    f"Dedup: {dedup_result['duplicates_found']} duplicates, "
                    f"{dedup_result['bytes_saved']} bytes saved"
                )
        except Exception as e:
            logger.error(f"Deduplication failed: {e}")

    # Update the backup timestamp only if every mode succeeded. A partial
    # success must not advance the "last good backup" marker — doing so
    # would let a broken mode silently skew future incremental windows.
    if not mode_failures:
        update_last_backup_time()

    # Run retention cleanup
    if backup_dirs and (max_age_days > 0 or max_count > 0):
        cleanup_old_backups(logger, backup_dirs, max_age_days=max_age_days, max_count=max_count)

    # Run post-backup hook
    if post_hook and not run_hook(logger, post_hook, "post_backup"):
        logger.warning("Post-backup hook failed (backup itself succeeded).")

    if mode_failures:
        failed_str = ", ".join(mode_failures)
        logger.error(f"Backup run finished with failures in: {failed_str}")
        _notify(
            logger,
            telegram_bot,
            notifications,
            f"Backup run finished with failures in: {failed_str}",
            config_values=config_values,
        )
        return 3

    _notify(
        logger,
        telegram_bot,
        notifications,
        "All backup operations completed successfully.",
        config_values=config_values,
    )

    # Dead-man's-switch ping. Only on full success — a failed run must not
    # reset the heartbeat window, or the external watchdog will never page.
    hb_url = config_values.get("heartbeat_url")
    if hb_url:
        try:
            send_heartbeat(logger, hb_url, timeout=config_values.get("heartbeat_timeout", 10))
        except Exception as e:
            logger.error(f"Heartbeat dispatch failed (non-fatal): {e}")

    return 0


if __name__ == "__main__":
    main()
