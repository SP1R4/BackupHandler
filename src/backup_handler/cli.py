"""
cli.py - Backup Handler CLI entrypoint.

Parses arguments, resolves the config path, routes to either an early-exit
subcommand handler (see :mod:`backup_handler.dispatch`) or the main
backup pipeline (:func:`backup_handler.orchestrator.backup_operation` /
:func:`backup_handler.orchestrator.scheduled_operation`).

Stays deliberately small. Subcommand logic lives in dispatch.py, the
backup pipeline in orchestrator.py, status rendering in status.py, and
single-instance locking in lock.py.
"""

from __future__ import annotations

import logging
import os
import sys

from colorama import init

from ._paths import CONFIG_DIR, LOG_DIR
from .argparse_setup import setup_argparse, validate_args
from .banner.banner_show import print_banner
from .bot.BotHandler import TelegramBot
from .config import extract_config_values
from .dispatch import (
    handle_install,
    handle_restore,
    handle_restore_snapshot,
    handle_snapshot,
    handle_snapshot_diff,
    handle_status,
    handle_verify,
)
from .logger import AppLogger, new_run_id
from .orchestrator import backup_operation, scheduled_operation

CONFIG_PATH = str(CONFIG_DIR / "config.ini")
LOG_PATH = str(LOG_DIR / "application.log")

init(autoreset=True)


def _resolve_config_path(args) -> str:
    """Pick the config file: ``--profile`` > ``--config`` > default."""
    if args.profile:
        profile_path = str(CONFIG_DIR / f"config.{args.profile}.ini")
        if not os.path.exists(profile_path):
            print(f"Error: Profile config not found: {profile_path}", file=sys.stderr)
            sys.exit(1)
        return profile_path
    if args.config and os.path.exists(args.config):
        return args.config
    return CONFIG_PATH


def _init_telegram_bot(logger):
    """Construct a TelegramBot, exiting with a useful message if config is missing."""
    try:
        return TelegramBot(logger)
    except FileNotFoundError:
        logger.error(
            "Telegram bot config not found. Create config/bot_config.ini from config/bot_config.ini.example"
        )
        print(
            "Error: config/bot_config.ini not found. "
            "Copy config/bot_config.ini.example and fill in your values.",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyError as e:
        logger.error(
            f"Missing key in bot_config.ini: {e}. "
            f"Check that [TELEGRAM] api_token and [USERS] interacted_users are set."
        )
        print(
            f"Error: Missing key in config/bot_config.ini: {e}. "
            f"Ensure api_token and interacted_users are set.",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    """Entry point — parses arguments, routes to the appropriate operation."""
    # Initialize the logger BEFORE anything else (banner, argparse, filesystem).
    # On 2026-04-16 the script crashed in a pre-logger code path and 16 days of
    # cron firings produced zero log lines. Logger first, always.
    logger = AppLogger(LOG_PATH, logging.DEBUG).logger
    new_run_id()
    # Skip the ASCII banner under cron / non-interactive shells. Otherwise
    # every cron firing logs ~30 lines of decorative ANSI escape codes.
    if sys.stdout.isatty():
        try:
            print_banner()
        except Exception as e:
            logger.warning(f"Banner failed (non-fatal): {e}")

    args = setup_argparse()
    validate_args(args, logger)
    config_path = _resolve_config_path(args)

    # --install runs BEFORE AppLogger writes anything to disk because the
    # installer is invoked via sudo and we don't want root-owned files
    # left behind in the project tree.
    if args.install:
        sys.exit(handle_install(logger, args, config_path))
    if args.status:
        sys.exit(handle_status(logger, config_path))
    if args.verify:
        sys.exit(handle_verify(logger, args, config_path))
    if args.snapshot:
        sys.exit(handle_snapshot(logger, args))
    if args.restore_snapshot:
        sys.exit(handle_restore_snapshot(logger, args))
    if args.snapshot_diff:
        sys.exit(handle_snapshot_diff(logger, args))
    if args.restore:
        sys.exit(handle_restore(logger, args, config_path))

    telegram_bot = _init_telegram_bot(logger) if args.notifications else None
    receiver_emails = args.receiver if args.notifications else None
    exclude_patterns = [p.strip() for p in args.exclude.split(",") if p.strip()] if args.exclude else None

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
        return

    # Fall back to config-defined source_dir / backup_dirs when CLI omits them.
    # Lets cron lines pass --config and skip --source-dir / --backup-dirs.
    cli_source_dir = args.source_dir
    cli_backup_dirs = args.backup_dirs
    if not cli_source_dir or not cli_backup_dirs:
        try:
            _cv = extract_config_values(logger, config_path, skip_validation=True)
        except Exception:
            _cv = {}
        cli_source_dir = cli_source_dir or _cv.get("source_dir")
        cli_backup_dirs = cli_backup_dirs or _cv.get("backup_dirs")
    if args.backup_mode and (not cli_source_dir or not cli_backup_dirs):
        logger.error("Source directory and backup directories must be specified when using --backup-mode.")
        sys.exit(1)

    rc = backup_operation(
        logger,
        source_dir=cli_source_dir,
        backup_dirs=cli_backup_dirs,
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


if __name__ == "__main__":
    main()
