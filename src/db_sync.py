import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


def perform_db_backup(logger, config_values, backup_dirs, manifest, dry_run=False):
    """
    Run mysqldump and save the dump to backup directories.

    Parameters:
    - logger: Logger instance.
    - config_values (dict): Configuration dictionary with db_user, db_password, db_database, db_host, db_port.
    - backup_dirs (list): List of backup directory paths.
    - manifest (BackupManifest): Manifest to record the dump file.
    - dry_run (bool): If True, log what would happen without executing.
    """
    db_user = config_values.get('db_user')
    db_password = config_values.get('db_password')
    db_database = config_values.get('db_database')
    db_host = config_values.get('db_host', 'localhost')
    db_port = config_values.get('db_port', 3306)

    if not db_user or not db_password or not db_database:
        logger.error("Database backup requires user, password, and database to be configured in [DATABASE].")
        return False

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

    # Dump to first backup directory
    primary_dir = Path(backup_dirs[0])
    primary_dir.mkdir(parents=True, exist_ok=True)
    dump_path = primary_dir / dump_filename

    cmd = [
        'mysqldump',
        '-u', db_user,
        '-h', db_host,
        '-P', str(db_port),
        '--result-file', str(dump_path),
        db_database,
    ]

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
    logger.info(f"Database dump saved: {dump_path} ({dump_size} bytes)")
    manifest.record_copy(str(dump_path), dump_size)

    # Copy dump to remaining backup directories
    for bdir in backup_dirs[1:]:
        dest_dir = Path(bdir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / dump_filename
        try:
            shutil.copy2(dump_path, dest_path)
            manifest.record_copy(str(dest_path), dump_size)
            logger.info(f"Database dump copied to {dest_path}")
        except Exception as e:
            logger.error(f"Failed to copy database dump to {dest_path}: {e}")

    return True
