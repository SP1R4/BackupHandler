import os
import sys
import time
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
                      update_last_full_backup_time)
# Import argparse setup
from src.argparse_setup import setup_argparse, validate_args


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

def main():
    print_banner()

    # Set up logging using AppLogger
    logger = AppLogger(LOG_PATH, logging.DEBUG).logger
    args = setup_argparse()

    # Validate the parsed arguments
    validate_args(args, logger)

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

    config_path = args.config if args.config and os.path.exists(args.config) else CONFIG_PATH

    if args.scheduled:
        try:
            scheduled_operation(logger, config_path, telegram_bot=telegram_bot)
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
                         dry_run=args.dry_run)

def scheduled_operation(logger, config_file, telegram_bot=None):
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
                                 ssh_password=config_values.get('ssh_password'))
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
                     dry_run=False):

    # Show setup command (skip validation so incomplete configs can be inspected)
    if show_setup:
        extract_config_values(logger, CONFIG_PATH, show=True, skip_validation=True)
        return

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
        elif backup_mode == 'incremental':
            if compress:
                logger.error("Invalid option for incremental backup.")
                sys.exit(1)
            last_backup_time = get_last_backup_time()
            _run_backup(logger, telegram_bot, notifications, "incremental",
                        lambda: perform_incremental_backup(logger, source_dir, backup_dirs,
                                                           last_backup_time, bot=telegram_bot,
                                                           receiver_emails=receiver))
        elif backup_mode == 'differential':
            if compress:
                logger.error("Invalid option for differential backup.")
                sys.exit(1)
            last_full_backup_time = get_last_full_backup_time()
            _run_backup(logger, telegram_bot, notifications, "differential",
                        lambda: perform_differential_backup(logger, source_dir, backup_dirs,
                                                            last_full_backup_time, bot=telegram_bot,
                                                            receiver_emails=receiver))
        else:
            _notify(logger, telegram_bot, notifications, "Starting full backup...")
            _run_backup(logger, telegram_bot, notifications, "full",
                        lambda: perform_full_backup(logger, source_dir, backup_dirs,
                                                     compress=compress, bot=telegram_bot,
                                                     receiver_emails=receiver))
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
                                              password=ssh_password, logger=logger)
                _notify(logger, telegram_bot, notifications, "SSH backup completed.")
            except Exception as e:
                logger.error(f"SSH backup failed: {e}")
                _notify(logger, telegram_bot, notifications, "SSH backup failed.")

    if dry_run:
        logger.info("[DRY RUN] Complete. No files were modified.")
        print("\n[DRY RUN] Complete. No files were modified.")
        return

    # Update the backup timestamp after successful execution
    update_last_backup_time()

    _notify(logger, telegram_bot, notifications, "All backup operations completed successfully.")

if __name__ == "__main__":
    main()
