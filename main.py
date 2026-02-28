import os
import sys
import time
import json
import signal
import atexit
import logging
from colorama import init
from pathlib import Path
from datetime import datetime
# Import logging object
from src.logger import AppLogger
# Import TelegramBot
from bot.BotHandler import TelegramBot
# Import banner function
from banner.banner_show import print_banner
# Import config utils
from src.config import extract_config_values
# Import sync utils
from src.sync import (sync_ssh_servers_concurrently,
                      perform_full_backup,
                      perform_incremental_backup,
                      perform_differential_backup)
# Import general utils
from src.utils import (get_last_backup_time,
                      update_last_backup_time,
                      get_last_full_backup_time,
                      update_last_full_backup_time,
                      run_hook)
# Import argparse setup
from src.argparse_setup import setup_argparse, validate_args
# Import manifest
from src.manifest import BackupManifest, load_latest_manifest
# Import retention
from src.retention import cleanup_old_backups
# Import restore
from src.restore import restore_backup
# Import S3 sync
from src.s3_sync import sync_to_s3


_PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = str(_PROJECT_ROOT / 'config' / 'config.ini')
LOG_PATH = str(_PROJECT_ROOT / 'Logs' / 'application.log')
LOCK_FILE = _PROJECT_ROOT / '.backup-handler.lock'


def _acquire_lock(logger):
    """Acquire a PID lock file to prevent duplicate scheduled instances."""
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            # Check if the process is still running
            os.kill(old_pid, 0)
            logger.error(f"Another backup-handler instance is already running (PID {old_pid}). "
                         f"Remove {LOCK_FILE} if this is incorrect.")
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            # PID file is stale — process no longer exists
            logger.warning(f"Removing stale lock file (PID in file no longer running).")

    LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)


def _release_lock():
    """Remove the PID lock file on exit."""
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass

# Initialize colorama with autoreset to ensure color codes are reset after each print
init(autoreset=True)


def _resolve_config_path(args):
    """Resolve config file path from --config, --profile, or default."""
    if args.profile:
        profile_path = str(_PROJECT_ROOT / 'config' / f'config.{args.profile}.ini')
        if not os.path.exists(profile_path):
            print(f"Error: Profile config not found: {profile_path}", file=sys.stderr)
            sys.exit(1)
        return profile_path
    if args.config and os.path.exists(args.config):
        return args.config
    return CONFIG_PATH


def show_status(logger, config_path):
    """Display backup status: last backup times, directory sizes, and latest manifest."""
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
    schedule_times = config_values.get('schedule_times', [])
    if schedule_times:
        print(f"\nScheduled times: {', '.join(schedule_times)}")
    else:
        print("\nScheduled times: Not configured")

    # Backup directory sizes
    backup_dirs = config_values.get('backup_dirs', [])
    if backup_dirs:
        print("\nBackup directories:")
        for bdir in backup_dirs:
            bpath = Path(bdir)
            if bpath.exists():
                total_size = sum(f.stat().st_size for f in bpath.rglob('*') if f.is_file())
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
                total_bytes = manifest.get('total_bytes', 0)
                if total_bytes >= 1048576:
                    print(f"    Size:      {total_bytes / 1048576:.2f} MB")
                else:
                    print(f"    Size:      {total_bytes / 1024:.2f} KB")
                break
        if not found_manifest:
            print("  No manifests found")

    print()


def main():
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

    # Handle --restore early exit
    if args.restore:
        logger.info(f"Restoring from {args.from_dir} to {args.to_dir}")
        success = restore_backup(logger, args.from_dir, args.to_dir,
                                 timestamp=args.restore_timestamp)
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
            logger.error("Telegram bot config not found. Create config/bot_config.ini from config/bot_config.ini.example")
            print("Error: config/bot_config.ini not found. Copy config/bot_config.ini.example and fill in your values.", file=sys.stderr)
            sys.exit(1)
        except KeyError as e:
            logger.error(f"Missing key in bot_config.ini: {e}. Check that [TELEGRAM] api_token and [USERS] interacted_users are set.")
            print(f"Error: Missing key in config/bot_config.ini: {e}. Ensure api_token and interacted_users are set.", file=sys.stderr)
            sys.exit(1)

    # Use the provided receiver emails if notifications are enabled
    receiver_emails = args.receiver if args.notifications else None

    # Parse exclude patterns from CLI (overrides config)
    exclude_patterns = None
    if args.exclude:
        exclude_patterns = [p.strip() for p in args.exclude.split(',') if p.strip()]

    if args.scheduled:
        try:
            scheduled_operation(logger, config_path, telegram_bot=telegram_bot,
                                exclude_patterns=exclude_patterns, retain=args.retain)
        except Exception as e:
            logger.error(f"Failed to load configuration file: {config_path}. Error: {e}")
            sys.exit(1)
    else:
        backup_operation(logger,
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
                         config_path=config_path)

def scheduled_operation(logger, config_file, telegram_bot=None, exclude_patterns=None,
                        retain=None):
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
        times = config_values.get('schedule_times', [])
        interval_minutes = config_values.get('interval_minutes', 60)
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
            exclude_patterns = config_values.get('exclude_patterns', [])

        logger.info(f"Scheduled times: {scheduled_times}")
        while not _shutdown_requested:
            now = datetime.now()
            current_time = now.time()
            logger.info(f"Current time: {current_time}")

            # Check for matching scheduled time within a ±30 second tolerance
            matched = False
            for scheduled_time in scheduled_times:
                scheduled_dt = now.replace(
                    hour=scheduled_time.hour,
                    minute=scheduled_time.minute,
                    second=0, microsecond=0
                )
                diff = abs((now - scheduled_dt).total_seconds())
                if diff <= 30:
                    matched = True
                    break
            if matched:
                logger.info("Scheduled time matched. Performing backup operation...")
                # Build operation_modes from config flags
                operation_modes = []
                if config_values.get('local_mode'):
                    operation_modes.append('local')
                if config_values.get('ssh_mode'):
                    operation_modes.append('ssh')
                if config_values.get('s3_mode'):
                    operation_modes.append('s3')
                backup_operation(logger,
                                 source_dir=config_values['source_dir'],
                                 backup_dirs=config_values['backup_dirs'],
                                 ssh_servers=config_values.get('ssh_servers'),
                                 operation_modes=operation_modes,
                                 backup_mode=config_values['mode'],
                                 compress=config_values['compress_type'],
                                 receiver=config_values['receiver_emails'],
                                 notifications=bool(telegram_bot),
                                 telegram_bot=telegram_bot,
                                 ssh_username=config_values.get('ssh_username'),
                                 ssh_password=config_values.get('ssh_password'),
                                 exclude_patterns=exclude_patterns,
                                 retain=retain,
                                 config_path=None,
                                 config_values=config_values)
            else:
                logger.info("No scheduled time matched.")
            # Wait for 30 seconds before checking again (matches tolerance window)
            time.sleep(30)

        logger.info("Scheduler stopped cleanly.")

    except Exception as e:
        logger.error(f"Error in scheduled_operation: {e}")
        sys.exit(1)

def _notify(logger, telegram_bot, notifications, message):
    """Send a Telegram notification if bot is available and notifications are enabled."""
    if notifications and telegram_bot:
        try:
            telegram_bot.send_notification(message)
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")


def _run_backup(logger, telegram_bot, notifications, mode_name, backup_fn):
    """Run a backup function with standard notification and error handling."""
    try:
        backup_fn()
        _notify(logger, telegram_bot, notifications, f"Local {mode_name} backup completed.")
    except Exception as e:
        logger.error(f"{mode_name.capitalize()} backup failed: {e}")
        _notify(logger, telegram_bot, notifications, f"{mode_name.capitalize()} backup failed.")


def backup_operation(logger, source_dir=None, backup_dirs=None, ssh_servers=None,
                     operation_modes=None, backup_mode=None, compress=None,
                     receiver=None, show_setup=False, notifications=False,
                     telegram_bot=None, ssh_username=None, ssh_password=None,
                     dry_run=False, exclude_patterns=None, retain=None,
                     config_path=None, config_values=None):

    # Show setup command (skip validation so incomplete configs can be inspected)
    if show_setup:
        extract_config_values(logger, config_path or CONFIG_PATH, show=True, skip_validation=True)
        return

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
        exclude_patterns = config_values.get('exclude_patterns', [])

    # Hooks
    pre_hook = config_values.get('pre_backup_hook')
    post_hook = config_values.get('post_backup_hook')

    # Retention (CLI --retain overrides config max_count)
    max_age_days = config_values.get('max_age_days', 0)
    max_count = retain if retain is not None else config_values.get('max_count', 0)

    # Parallel copies
    parallel_copies = config_values.get('parallel_copies', 1)

    # Bandwidth limit
    bandwidth_limit = config_values.get('bandwidth_limit', 0)

    # S3 config
    s3_bucket = config_values.get('s3_bucket')
    s3_prefix = config_values.get('s3_prefix', '')
    s3_region = config_values.get('s3_region')
    s3_access_key = config_values.get('s3_access_key')
    s3_secret_key = config_values.get('s3_secret_key')

    # Run pre-backup hook
    if pre_hook:
        if not run_hook(logger, pre_hook, 'pre_backup'):
            logger.error("Pre-backup hook failed. Aborting backup.")
            _notify(logger, telegram_bot, notifications, "Backup aborted: pre-backup hook failed.")
            return

    # Create manifest for this backup run
    manifest = BackupManifest(mode=backup_mode or 'full')

    # Execute selected backup modes
    if operation_modes is None:
        operation_modes = []
    if 'local' in operation_modes:
        if not backup_dirs:
            logger.warning("Local mode selected but no backup directories specified. Skipping local backup.")
        elif dry_run:
            logger.info(f"[DRY RUN] Would perform {backup_mode or 'full'} backup: '{source_dir}' -> {backup_dirs}")
            print(f"[DRY RUN] Would perform {backup_mode or 'full'} backup")
            print(f"  Source:      {source_dir}")
            print(f"  Destinations: {', '.join(backup_dirs)}")
            if compress:
                print(f"  Compression: {compress}")
            if exclude_patterns:
                print(f"  Excluding:   {', '.join(exclude_patterns)}")
        elif backup_mode == 'incremental':
            if compress:
                logger.error("Invalid option for incremental backup.")
                sys.exit(1)
            last_backup_time = get_last_backup_time()
            _run_backup(logger, telegram_bot, notifications, "incremental",
                        lambda: perform_incremental_backup(logger, source_dir, backup_dirs,
                                                           last_backup_time, bot=telegram_bot,
                                                           receiver_emails=receiver,
                                                           exclude_patterns=exclude_patterns,
                                                           manifest=manifest))
        elif backup_mode == 'differential':
            if compress:
                logger.error("Invalid option for differential backup.")
                sys.exit(1)
            last_full_backup_time = get_last_full_backup_time()
            _run_backup(logger, telegram_bot, notifications, "differential",
                        lambda: perform_differential_backup(logger, source_dir, backup_dirs,
                                                            last_full_backup_time, bot=telegram_bot,
                                                            receiver_emails=receiver,
                                                            exclude_patterns=exclude_patterns,
                                                            manifest=manifest))
        else:
            _notify(logger, telegram_bot, notifications, "Starting full backup...")
            _run_backup(logger, telegram_bot, notifications, "full",
                        lambda: perform_full_backup(logger, source_dir, backup_dirs,
                                                     compress=compress, bot=telegram_bot,
                                                     receiver_emails=receiver,
                                                     exclude_patterns=exclude_patterns,
                                                     manifest=manifest,
                                                     parallel_copies=parallel_copies))
            update_last_full_backup_time()

    if operation_modes and ('ssh' in operation_modes):
        if not ssh_servers:
            logger.warning("SSH mode selected but no SSH servers specified. Skipping SSH backup.")
        elif dry_run:
            logger.info(f"[DRY RUN] Would sync '{source_dir}' to SSH servers: {ssh_servers}")
            print(f"[DRY RUN] Would sync '{source_dir}' to SSH servers: {', '.join(ssh_servers)}")
        else:
            _notify(logger, telegram_bot, notifications, "Starting SSH backup...")
            logger.info("Running SSH backup...")
            try:
                sync_ssh_servers_concurrently(source_dir, ssh_servers, username=ssh_username or '',
                                              password=ssh_password, logger=logger,
                                              exclude_patterns=exclude_patterns,
                                              manifest=manifest,
                                              bandwidth_limit=bandwidth_limit)
                _notify(logger, telegram_bot, notifications, "SSH backup completed.")
            except Exception as e:
                logger.error(f"SSH backup failed: {e}")
                _notify(logger, telegram_bot, notifications, "SSH backup failed.")

    if operation_modes and ('s3' in operation_modes):
        if not s3_bucket:
            logger.warning("S3 mode selected but no bucket configured. Skipping S3 backup.")
        elif dry_run:
            logger.info(f"[DRY RUN] Would sync '{source_dir}' to s3://{s3_bucket}/{s3_prefix}")
            print(f"[DRY RUN] Would sync '{source_dir}' to s3://{s3_bucket}/{s3_prefix}")
        else:
            _notify(logger, telegram_bot, notifications, "Starting S3 backup...")
            logger.info("Running S3 backup...")
            try:
                sync_to_s3(logger, source_dir, s3_bucket, prefix=s3_prefix,
                           region=s3_region, access_key=s3_access_key,
                           secret_key=s3_secret_key, mode=backup_mode or 'full',
                           exclude_patterns=exclude_patterns, manifest=manifest)
                _notify(logger, telegram_bot, notifications, "S3 backup completed.")
            except Exception as e:
                logger.error(f"S3 backup failed: {e}")
                _notify(logger, telegram_bot, notifications, "S3 backup failed.")

    if dry_run:
        logger.info("[DRY RUN] Complete. No files were modified.")
        print("\n[DRY RUN] Complete. No files were modified.")
        return

    # Save manifest to each backup directory
    if backup_dirs:
        for bdir in backup_dirs:
            try:
                manifest_path = manifest.save(bdir)
                logger.info(f"Backup manifest saved to {manifest_path}")
            except Exception as e:
                logger.error(f"Failed to save manifest to {bdir}: {e}")

    # Update the backup timestamp after successful execution
    update_last_backup_time()

    # Run retention cleanup
    if backup_dirs and (max_age_days > 0 or max_count > 0):
        cleanup_old_backups(logger, backup_dirs, max_age_days=max_age_days, max_count=max_count)

    # Run post-backup hook
    if post_hook:
        if not run_hook(logger, post_hook, 'post_backup'):
            logger.warning("Post-backup hook failed (backup itself succeeded).")

    _notify(logger, telegram_bot, notifications, "All backup operations completed successfully.")

if __name__ == "__main__":
    main()
