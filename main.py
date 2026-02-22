import os
import sys
import time
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
    telegram_bot = TelegramBot(logger) if args.notifications else None

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
                         telegram_bot=telegram_bot)

def scheduled_operation(logger, config_file, telegram_bot=None):
    try:
        # Loading the config file
        config_values = extract_config_values(logger, config_file)

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
        while True:
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
                if config_values.get('local'):
                    operation_modes.append('local')
                if config_values.get('ssh'):
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
                                 telegram_bot=telegram_bot)
            else:
                logger.info("No scheduled time matched.")
            # Wait for 30 seconds before checking again (matches tolerance window)
            time.sleep(30)

    except Exception as e:
        logger.error(f"Error in scheduled_operation: {e}")
        sys.exit(1)

def backup_operation(logger, source_dir=None, backup_dirs=None, ssh_servers=None,
                     operation_modes=None, backup_mode=None, compress=None,
                     receiver=None, show_setup=False, notifications=False,
                     telegram_bot=None):

    # Show setup command
    if show_setup:
        extract_config_values(logger, CONFIG_PATH, show=True)
        return

    # Execute selected backup modes
    if operation_modes is None:
        operation_modes = []
    if 'local' in operation_modes:
        if backup_mode == 'incremental':
            if compress:
                logger.error("Invalid option for incremental backup.")
                sys.exit(1)
            last_backup_time = get_last_backup_time()
            try:
                perform_incremental_backup(logger, source_dir, backup_dirs, last_backup_time, bot=telegram_bot)
                if notifications:
                    telegram_bot.send_notification(f"Local incremental backup completed.")
            except Exception as e:
                logger.error(f"Incremental backup failed: {e}")
                if notifications:
                    telegram_bot.send_notification("Incremental backup failed.")
        elif backup_mode == 'differential':
            if compress:
                logger.error("Invalid option for differential backup.")
                sys.exit(1)
            last_full_backup_time = get_last_full_backup_time()
            try:
                perform_differential_backup(logger, source_dir, backup_dirs, last_full_backup_time)
                if notifications:
                    telegram_bot.send_notification(f"Local differential backup completed.")
            except Exception as e:
                logger.error(f"Differential backup failed: {e}")
                if notifications:
                    telegram_bot.send_notification("Differential backup failed.")
        else:
            if notifications:
                telegram_bot.send_notification("Starting full backup...")
            try:
                perform_full_backup(logger, source_dir, backup_dirs, compress=compress, bot=telegram_bot, receiver_emails=receiver)
                update_last_full_backup_time()
                if notifications:
                    telegram_bot.send_notification(f"Local full backup completed.")
            except Exception as e:
                logger.error(f"Full backup failed: {e}")
                if notifications:
                    telegram_bot.send_notification("Full backup failed.")

    if operation_modes and ('ssh' in operation_modes):
        if notifications:
            telegram_bot.send_notification("Starting SSH backup...")
        logger.info("Running SSH backup...")
        try:
            sync_ssh_servers_concurrently(source_dir, ssh_servers, username='', logger=logger)
            if notifications:
                telegram_bot.send_notification("SSH backup completed.")
        except Exception as e:
            logger.error(f"SSH backup failed: {e}")
            if notifications:
                telegram_bot.send_notification("SSH backup failed.")

    # Update the backup timestamp after successful execution
    update_last_backup_time()

    if notifications:
        telegram_bot.send_notification("All backup operations completed successfully.")

if __name__ == "__main__":
    main()
