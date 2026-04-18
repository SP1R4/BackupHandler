"""
db_sync.py - MySQL Database Backup Integration

Executes ``mysqldump`` to create a point-in-time SQL dump of a MySQL database,
saves it to the primary backup directory, and distributes copies to any
additional backup destinations. The dump file is recorded in the backup
manifest for verification and restore tracking.

Security:
    The MySQL password is passed via the ``MYSQL_PWD`` environment variable
    to avoid exposing it on the command line or in ``/proc``.
"""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from .utils import calculate_checksum


def perform_db_backup(logger, config_values, backup_dirs, manifest, dry_run=False):
    """
    Run ``mysqldump`` and distribute the SQL dump to all backup directories.

    The dump is first written to the primary (first) backup directory, then
    copied to any remaining directories. Each successful write is recorded
    in the manifest.

    Parameters:
        logger: Logger instance.
        config_values (dict): Must contain ``db_user``, ``db_password``,
            ``db_database``; optionally ``db_host`` (default: localhost)
            and ``db_port`` (default: 3306).
        backup_dirs (list): Backup directory paths.
        manifest (BackupManifest): Manifest to record the dump file.
        dry_run (bool): If True, log the planned operation without executing.

    Returns:
        bool: True if the dump was created and distributed successfully.
    """
    db_user = config_values.get('db_user')
    db_password = config_values.get('db_password')
    db_database = config_values.get('db_database')
    db_host = config_values.get('db_host', 'localhost')
    db_port = config_values.get('db_port', 3306)
    single_transaction = config_values.get('db_single_transaction', True)
    binlog_position = config_values.get('db_binlog_position', False)

    if not db_user or not db_password or not db_database:
        logger.error("Database backup requires user, password, and database to be configured in [DATABASE].")
        return False

    # Generate timestamped filename for the SQL dump
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dump_filename = f"{db_database}_backup_{timestamp}.sql"

    if dry_run:
        logger.info(f"[DRY RUN] Would run mysqldump for database '{db_database}' as user '{db_user}' on {db_host}:{db_port}")
        print(f"[DRY RUN] Would dump MySQL database '{db_database}' to {dump_filename}")
        print(f"  Host: {db_host}:{db_port}")
        print(f"  User: {db_user}")
        for bdir in backup_dirs:
            print(f"  Destination: {bdir}/{dump_filename}")
        return True

    if not backup_dirs:
        logger.error("No backup directories configured for database dump.")
        return False

    # Write the dump to the primary (first) backup directory
    primary_dir = Path(backup_dirs[0])
    primary_dir.mkdir(parents=True, exist_ok=True)
    dump_path = primary_dir / dump_filename

    # Build mysqldump command — password is passed via MYSQL_PWD env var
    cmd = [
        'mysqldump',
        '-u', db_user,
        '-h', db_host,
        '-P', str(db_port),
        '--result-file', str(dump_path),
    ]
    if single_transaction:
        cmd.append('--single-transaction')
    if binlog_position:
        cmd.append('--master-data=2')
    cmd.append(db_database)

    # Inject password via environment to avoid CLI exposure
    env = os.environ.copy()
    env['MYSQL_PWD'] = db_password

    logger.info(f"Running mysqldump for database '{db_database}' on {db_host}:{db_port}")

    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            logger.error(f"mysqldump failed (exit code {result.returncode}): {result.stderr.strip()}")
            return False
    except FileNotFoundError:
        logger.error("mysqldump command not found. Ensure MySQL client tools are installed.")
        return False
    except subprocess.TimeoutExpired:
        logger.error("mysqldump timed out after 1 hour.")
        return False

    dump_size = dump_path.stat().st_size
    dump_checksum = calculate_checksum(str(dump_path))
    logger.info(f"Database dump saved: {dump_path} ({dump_size} bytes)")
    manifest.record_copy(str(dump_path), dump_size, checksum=dump_checksum)

    # Copy dump to remaining backup directories
    for bdir in backup_dirs[1:]:
        dest_dir = Path(bdir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / dump_filename
        try:
            shutil.copy2(dump_path, dest_path)
            manifest.record_copy(str(dest_path), dump_size, checksum=dump_checksum)
            logger.info(f"Database dump copied to {dest_path}")
        except (OSError, shutil.Error) as e:
            logger.error(f"Failed to copy database dump to {dest_path}: {e}")

    return True
