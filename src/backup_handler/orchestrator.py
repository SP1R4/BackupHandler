"""
orchestrator.py - Multi-mode backup orchestration.

Holds the two top-level workflows that drive the backup pipeline:

  - ``backup_operation``  — one-shot invocation from the CLI.
  - ``scheduled_operation`` — long-running scheduler that fires
    ``backup_operation`` at configured times.

Plus the private notification + mode-runner helpers they share. Was
previously inlined into cli.py; lifted out so the entrypoint stays small
and the orchestration code is easier to test in isolation.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import qsafe_backend
from ._paths import CONFIG_DIR, PROJECT_ROOT
from .bot.BotHandler import TelegramBot
from .config import extract_config_values
from .db_sync import perform_db_backup
from .dedup import deduplicate_backup_dirs
from .email_notify import send_smtp_email
from .encryption import encrypt_directory
from .heartbeat import send_heartbeat
from .lock import acquire_lock
from .logger import AppLogger, current_run_id, new_run_id
from .manifest import BackupManifest, load_latest_manifest
from .preflight import (
    PreflightConfig,
    run_preflight,
    send_local_mail,
    write_status_sentinel,
)
from .restore import restore_backup
from .retention import cleanup_old_backups
from .s3_sync import sync_to_s3
from .sync import (
    perform_differential_backup,
    perform_full_backup,
    perform_incremental_backup,
    sync_ssh_servers_concurrently,
)
from .tailscale import tailscale_down, tailscale_up
from .utils import (
    assert_config_safe_for_hooks,
    get_last_backup_time,
    get_last_full_backup_time,
    run_hook,
    update_last_backup_time,
    update_last_full_backup_time,
)
from .verify import print_verify_report, verify_backup_integrity
from .webhook_notify import send_webhook

_PROJECT_ROOT = PROJECT_ROOT
CONFIG_PATH = str(CONFIG_DIR / "config.ini")


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
    acquire_lock(logger)

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


def _critical_alert(
    logger,
    config_values,
    telegram_bot,
    notifications,
    subject: str,
    body: str,
) -> None:
    """
    Emit a high-priority alert through every available channel.

    Used for failures that MUST reach an operator: preflight aborts, stale
    backups, lost destinations. Tries Telegram/SMTP/webhook (best-effort —
    these can fail silently when DNS is broken, which is exactly the
    scenario this guards), then always attempts a local-MTA mail to
    [PREFLIGHT] local_mail_to. Local mail does not need DNS or external
    network and survives the failure modes that disabled our other
    channels on 2026-04-16.
    """
    _notify(logger, telegram_bot, notifications, f"{subject}: {body}", config_values=config_values)
    if config_values:
        local_to = config_values.get("preflight_local_mail_to")
        if local_to:
            send_local_mail(logger, local_to, f"[backup-handler] {subject}", body)


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


def _check_qsafe_readiness(config_values, encrypt=False):
    """
    Return an error message if Qsafe is configured but cannot run, else None.

    Catches missing engine (no bindings, no CLI) and missing key files up
    front — encryption runs *after* files are copied, so a late failure
    would leave a plaintext backup on disk believing it was encrypted.
    """
    uses_qsafe_backend = (encrypt or config_values.get("encryption_enabled", False)) and config_values.get(
        "encryption_backend", "aes"
    ) == "qsafe"
    sign_key = config_values.get("encryption_qsafe_sign_key")

    if not uses_qsafe_backend and not sign_key:
        return None
    if not qsafe_backend.is_available():
        return (
            "Qsafe is configured but neither the qsafe Python bindings nor the "
            "qsafe CLI are available. Install Qsafe or update [ENCRYPTION]."
        )
    if uses_qsafe_backend:
        recipients = qsafe_backend.parse_recipients(config_values.get("encryption_qsafe_recipients"))
        if not recipients:
            return "Qsafe backend enabled but no qsafe_recipients configured in [ENCRYPTION]."
        missing = [r for r in recipients if not Path(r).exists()]
        if missing:
            return f"Qsafe recipient public key(s) not found: {', '.join(missing)}"
    if sign_key and not Path(sign_key).exists():
        return f"Qsafe manifest signing key not found: {sign_key}"
    return None


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

    # ─── Preflight self-check + sentinel ───────────────────────────────────
    # Resolve sentinel path (relative paths anchor at project root).
    pf_cfg = PreflightConfig(
        enabled=config_values.get("preflight_enabled", True),
        expected_mount=config_values.get("preflight_expected_mount"),
        expected_label=config_values.get("preflight_expected_label"),
        expected_uuid=config_values.get("preflight_expected_uuid"),
        expected_fs_type=config_values.get("preflight_expected_fs_type", "xfs"),
        expected_owner=config_values.get("preflight_expected_owner"),
        auto_mount=config_values.get("preflight_auto_mount", True),
        auto_fix_ownership=config_values.get("preflight_auto_fix_ownership", False),
        ensure_fstab=config_values.get("preflight_ensure_fstab", True),
        staleness_factor=config_values.get("preflight_staleness_factor", 2.0),
        local_mail_to=config_values.get("preflight_local_mail_to"),
        status_sentinel=config_values.get("preflight_status_sentinel", "Logs/last_run_status.json"),
    )
    sentinel_relpath = Path(pf_cfg.status_sentinel)
    sentinel_path = sentinel_relpath if sentinel_relpath.is_absolute() else _PROJECT_ROOT / sentinel_relpath

    write_status_sentinel(
        sentinel_path,
        status="started",
        run_id=current_run_id(),
        message="backup run started",
        extra={"backup_dirs": backup_dirs or [], "modes": operation_modes or []},
    )

    if not dry_run and not show_setup:
        pf_result = run_preflight(
            logger,
            pf_cfg,
            backup_dirs=backup_dirs or [],
            interval_minutes=config_values.get("interval_minutes", 60),
        )
        if not pf_result.ok:
            logger.error(f"Preflight FAILED: {pf_result.message}")
            _critical_alert(
                logger,
                config_values,
                telegram_bot,
                notifications,
                "Preflight FAILED",
                pf_result.message,
            )
            write_status_sentinel(
                sentinel_path,
                status="failure",
                run_id=current_run_id(),
                message=f"preflight: {pf_result.message}",
                extra={"phase": "preflight", "details": pf_result.details},
            )
            return 2
        if pf_result.healed:
            logger.info(f"Preflight self-healed: {pf_result.message}")
        stale_msg = pf_result.details.get("stale_alert") if pf_result.details else None
        if stale_msg:
            _critical_alert(
                logger,
                config_values,
                telegram_bot,
                notifications,
                "Backup STALE",
                stale_msg,
            )

        # Qsafe readiness: fail before any files are copied, not at encrypt time
        qsafe_error = _check_qsafe_readiness(config_values, encrypt)
        if qsafe_error:
            logger.error(f"Qsafe preflight FAILED: {qsafe_error}")
            _critical_alert(
                logger,
                config_values,
                telegram_bot,
                notifications,
                "Qsafe preflight FAILED",
                qsafe_error,
            )
            write_status_sentinel(
                sentinel_path,
                status="failure",
                run_id=current_run_id(),
                message=f"qsafe preflight: {qsafe_error}",
                extra={"phase": "preflight"},
            )
            return 2

    # Hooks
    pre_hook = config_values.get("pre_backup_hook")
    post_hook = config_values.get("post_backup_hook")

    if (pre_hook or post_hook) and config_path:
        try:
            assert_config_safe_for_hooks(logger, config_path)
        except (PermissionError, RuntimeError) as e:
            logger.error(str(e))
            return 1

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
        _critical_alert(
            logger,
            config_values,
            telegram_bot,
            notifications,
            "Backup aborted",
            "pre-backup hook failed.",
        )
        write_status_sentinel(
            sentinel_path,
            status="failure",
            run_id=current_run_id(),
            message="pre-backup hook failed",
            extra={"phase": "pre_hook"},
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
                        known_hosts_path=config_values.get("ssh_known_hosts"),
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
            if config_values.get("encryption_backend", "aes") == "qsafe":
                recipients = qsafe_backend.parse_recipients(config_values.get("encryption_qsafe_recipients"))
                print(
                    f"[DRY RUN] Would encrypt backup files using Qsafe post-quantum "
                    f"hybrid encryption (X25519 + ML-KEM-1024, {len(recipients)} recipient(s))"
                )
            else:
                enc_method = "key_file" if config_values.get("encryption_key_file") else "passphrase"
                print(f"[DRY RUN] Would encrypt backup files using AES-256-GCM ({enc_method})")
        if config_values.get("encryption_qsafe_sign_key"):
            print("[DRY RUN] Would sign backup manifests with ML-DSA-87 (Qsafe)")
        dry_dedup = dedup or config_values.get("dedup_enabled", False)
        if dry_dedup:
            print("[DRY RUN] Would deduplicate identical files using hardlinks")
        logger.info("[DRY RUN] Complete. No files were modified.")
        print("\n[DRY RUN] Complete. No files were modified.")
        return 0

    # Save manifest to each backup directory (and sign it if configured)
    sign_key = config_values.get("encryption_qsafe_sign_key")
    sign_passphrase = config_values.get("encryption_qsafe_sign_passphrase")
    if backup_dirs:
        for bdir in backup_dirs:
            try:
                manifest_path = manifest.save(bdir)
                logger.info(f"Backup manifest saved to {manifest_path}")
            except Exception as e:
                logger.error(f"Failed to save manifest to {bdir}: {e}")
                continue
            if sign_key:
                try:
                    sig_path = str(manifest_path) + ".sig"
                    qsafe_backend.sign_file(manifest_path, sig_path, sign_key, sign_passphrase)
                    logger.info(f"Manifest signed (ML-DSA-87): {sig_path}")
                except Exception as e:
                    logger.error(f"Failed to sign manifest {manifest_path}: {e}")

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
    enc_backend = config_values.get("encryption_backend", "aes")
    enc_passphrase = config_values.get("encryption_passphrase")
    enc_key_file = config_values.get("encryption_key_file")
    enc_recipients = qsafe_backend.parse_recipients(config_values.get("encryption_qsafe_recipients"))

    enc_workers = config_values.get("encryption_workers", 1)

    if encryption_enabled and backup_dirs:
        if enc_backend == "qsafe" and not enc_recipients:
            logger.error("Qsafe encryption enabled but no qsafe_recipients configured in [ENCRYPTION].")
        elif enc_backend != "qsafe" and not enc_passphrase and not enc_key_file:
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
                        kdf=config_values.get("encryption_kdf", "pbkdf2"),
                        backend=enc_backend,
                        qsafe_recipients=enc_recipients,
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
        _critical_alert(
            logger,
            config_values,
            telegram_bot,
            notifications,
            "Backup partial failure",
            f"failures in: {failed_str}",
        )
        write_status_sentinel(
            sentinel_path,
            status="failure",
            run_id=current_run_id(),
            message=f"failures in: {failed_str}",
            extra={"phase": "modes", "failed_modes": mode_failures},
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

    write_status_sentinel(
        sentinel_path,
        status="success",
        run_id=current_run_id(),
        message="all modes completed",
        extra={"modes": operation_modes or []},
    )
    return 0


if __name__ == "__main__":
    main()
