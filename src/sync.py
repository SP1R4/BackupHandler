import os
import stat
import shutil
import paramiko
from tqdm import tqdm
from pathlib import Path
from retrying import retry
from email_nots.email import send_email
from .compression import compress_directory
from .utils import verify_backup, generate_otp
from concurrent.futures import ThreadPoolExecutor


def sync_directories_with_progress(logger, source_dirs, backup_dirs, compress=None, bot=None, receiver_emails=None):
    """
    Sync files from source directories to backup directories with progress tracking.

    Parameters:
    - logger: Logger instance for logging output.
    - source_dirs (list of str): List of source directories.
    - backup_dirs (list of str): List of backup directories.
    - compress (str, optional): If 'zip', compress the final backup directory. If 'zip_pw', compress with a password.
    - bot (TelegramBot, optional): Telegram bot instance for sending notifications.
    - receiver_emails (list of str, optional): List of emails to notify after backup.
    """
    for src_dir in source_dirs:
        # List all files in the current source directory
        files = list(Path(src_dir).rglob('*'))

        for backup_dir in backup_dirs:
            for file in tqdm(files, desc=f"Syncing Files from {src_dir} to {backup_dir}", unit="files"):
                backup_file = Path(backup_dir) / file.relative_to(src_dir)

                # Skip directories
                if file.is_dir():
                    continue

                try:
                    # Ensure the destination directory exists
                    backup_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, backup_file)
                    if verify_backup(file, backup_file):
                        logger.info(f"Successfully backed up {file} to {backup_file}") if logger else print(f"Successfully backed up {file} to {backup_file}")
                    else:
                        logger.error(f"Checksum verification failed for {file}") if logger else print(f"Checksum verification failed for {file}")
                except Exception as e:
                    logger.error(f"Failed to backup {file} to {backup_file}: {e}")

    # Compress the backup directories if the compress flag is set
    if compress in ['zip', 'zip_pw']:
        password = generate_otp() if compress == 'zip_pw' else None
        compress_directory(logger,
                           src_dirs=source_dirs,
                           output_dirs=backup_dirs,
                           password=password,
                           bot_handler=bot,
                           receiver_emails=receiver_emails)

    # Send notification if bot is enabled
    if bot:
        try:
            bot.send_notification(f"Backup completed for {source_dirs}")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    # Send email notifications if email addresses are provided
    if receiver_emails:
        subject = "Backup Completed"
        body = f"Backup completed for {source_dirs}"
        try:
            send_email(receiver_emails, subject, body, logger=logger)
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")


def _sftp_upload_directory(sftp, local_path, remote_path, mode='full', logger=None):
    """
    Upload a local directory to a remote server via SFTP.

    Parameters:
    - sftp: paramiko SFTP client.
    - local_path (str): Local directory path.
    - remote_path (str): Remote directory path.
    - mode (str): Backup mode ('full', 'incremental', 'differential').
    - logger: Logger instance.
    """
    local_path = Path(local_path)
    files = [f for f in local_path.rglob('*') if f.is_file()]

    for local_file in tqdm(files, desc=f"Uploading to {remote_path}", unit="files"):
        relative = local_file.relative_to(local_path)
        remote_file = f"{remote_path}/{relative}"
        remote_dir = f"{remote_path}/{relative.parent}"

        # Ensure remote directory exists
        _sftp_mkdirs(sftp, remote_dir)

        should_upload = True
        if mode in ('incremental', 'differential'):
            try:
                remote_stat = sftp.stat(remote_file)
                local_mtime = local_file.stat().st_mtime
                remote_mtime = remote_stat.st_mtime
                if mode == 'incremental' and remote_stat:
                    # Only upload if local is newer
                    should_upload = local_mtime > remote_mtime
                elif mode == 'differential':
                    # Only upload if local is newer
                    should_upload = local_mtime > remote_mtime
            except FileNotFoundError:
                should_upload = True  # File doesn't exist remotely

        if should_upload:
            try:
                sftp.put(str(local_file), remote_file)
                if logger:
                    logger.info(f"Uploaded {local_file} -> {remote_file}")
            except Exception as e:
                if logger:
                    logger.error(f"Failed to upload {local_file}: {e}")

    # In full mode, remove remote files not present locally
    if mode == 'full':
        _sftp_cleanup_extra_files(sftp, local_path, remote_path, logger)


def _sftp_mkdirs(sftp, remote_dir):
    """Recursively create remote directories."""
    dirs_to_create = []
    current = remote_dir
    while current and current != '/':
        try:
            sftp.stat(current)
            break
        except FileNotFoundError:
            dirs_to_create.append(current)
            current = os.path.dirname(current)
    for d in reversed(dirs_to_create):
        try:
            sftp.mkdir(d)
        except OSError:
            pass  # Directory may already exist


def _sftp_cleanup_extra_files(sftp, local_path, remote_path, logger=None):
    """Remove remote files that don't exist in the local source (full sync)."""
    local_files = set()
    for f in local_path.rglob('*'):
        if f.is_file():
            local_files.add(str(f.relative_to(local_path)))

    def _walk_remote(path):
        try:
            entries = sftp.listdir_attr(path)
        except Exception:
            return
        for entry in entries:
            remote_entry = f"{path}/{entry.filename}"
            if stat.S_ISDIR(entry.st_mode):
                _walk_remote(remote_entry)
            else:
                relative = os.path.relpath(remote_entry, remote_path)
                if relative not in local_files:
                    try:
                        sftp.remove(remote_entry)
                        if logger:
                            logger.info(f"Removed extra remote file: {remote_entry}")
                    except Exception as e:
                        if logger:
                            logger.error(f"Failed to remove remote file {remote_entry}: {e}")

    _walk_remote(remote_path)


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def sync_ssh_server(source_dir, server, username, password=None, key_filepath=None, mode='full', logger=None, bot=None):
    """
    Sync a local directory to a remote server via SSH using SFTP, with retry logic.

    Parameters:
    - source_dir (str): Local directory to sync.
    - server (str): Remote SSH server address.
    - username (str): SSH username.
    - password (str, optional): SSH password (if not using a private key).
    - key_filepath (str, optional): Path to private key (if not using password).
    - mode (str, optional): Backup mode ('full', 'incremental', 'differential').
    - logger (logging.Logger, optional): Logger instance for logging.
    - bot (TelegramBot, optional): Bot instance for sending notifications.
    """
    if logger:
        logger.info(f"Syncing {source_dir} to SSH server: {server} in {mode} mode")

    # Initialize SSH client
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.WarningPolicy())

    try:
        # Connect to SSH server using password or private key
        ssh.connect(hostname=server, username=username, password=password, key_filename=key_filepath)

        if logger:
            logger.info(f"Connected to SSH server: {server}")

        # Use SFTP to upload files
        sftp = ssh.open_sftp()
        try:
            remote_path = source_dir  # Mirror the local path on the remote
            _sftp_upload_directory(sftp, source_dir, remote_path, mode=mode, logger=logger)
        finally:
            sftp.close()

        if logger:
            logger.info(f"Completed SSH sync to {server}")

    except Exception as e:
        if logger:
            logger.error(f"Failed to sync to SSH server {server}: {e}")
        raise

    finally:
        ssh.close()
        if logger:
            logger.info(f"Closed connection to SSH server: {server}")
        else:
            print(f"Closed connection to SSH server: {server}")

def sync_ssh_servers_concurrently(source_dir, ssh_servers, username, password=None, key_filepath=None, mode='full', logger=None, bot=None, receiver_emails=None):
    """
    Sync a local directory to multiple SSH servers concurrently.

    Parameters:
    - source_dir (str): Local directory to sync.
    - ssh_servers (list of str): List of SSH server addresses.
    - username (str): SSH username for all servers.
    - password (str, optional): SSH password (if not using a key).
    - key_filepath (str, optional): Path to private key.
    - mode (str, optional): Backup mode ('full', 'incremental', 'differential').
    - logger (logging.Logger, optional): Logger instance for logging.
    - bot (TelegramBot, optional): Bot instance for notifications.
    - receiver_emails (list of str, optional): List of email addresses for notifications.
    """

    def sync_ssh_server_task(server):
        try:
            sync_ssh_server(source_dir, server, username, password, key_filepath, mode, logger=logger, bot=bot)
        except Exception as e:
            if logger:
                logger.error(f"Failed to sync to SSH server {server}: {e}")
            else:
                print(e)

    # Execute SSH syncs concurrently with capped thread pool
    max_workers = min(len(ssh_servers), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(sync_ssh_server_task, server) for server in ssh_servers]
        for future in futures:
            try:
                future.result()
            except Exception as e:
                if logger:
                    logger.error(f"An error occurred during SSH backup: {e}")
                else:
                    print(e)

    # Notifications
    if bot:
        bot.send_notification("Completed concurrent SSH sync")
    if receiver_emails:
        send_email(receiver_emails, "Backup Completed", f"Backup completed for {source_dir}", logger=logger)


def perform_full_backup(logger, source_dir, backup_dirs, compress=None, bot=None, receiver_emails=None):
    """
    Perform a full backup of the source directory to the backup directories.
    """
    logger.info(f"Performing full backup from {source_dir}")
    if isinstance(source_dir, str):
        source_dir = [source_dir]
    sync_directories_with_progress(logger, source_dir, backup_dirs, compress=compress, bot=bot, receiver_emails=receiver_emails)

def perform_incremental_backup(logger, source_dir, backup_dirs, last_backup_time, bot=None, receiver_emails=None):
    """
    Perform an incremental backup of the source directory to the backup directories.
    """
    logger.info(f"Performing incremental backup from {source_dir} since last backup time: {last_backup_time}")
    files = list(Path(source_dir).rglob('*'))

    for file in tqdm(files, desc="Syncing Incremental Files", unit="files"):
        file_mtime = os.path.getmtime(file)
        for backup_dir in backup_dirs:
            backup_file = Path(backup_dir) / file.relative_to(source_dir)
            if file_mtime > last_backup_time or not backup_file.exists():
                logger.info(f"Backing up modified or new file: {file} (modified at {file_mtime})")
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, backup_file)
                if verify_backup(file, backup_file):
                    logger.info(f"Incremental backup of {file} to {backup_file}")
                else:
                    logger.error(f"Checksum verification failed for {file}")
            else:
                logger.info(f"Skipping unmodified file: {file} (modified at {file_mtime})")

    if bot:
        bot.send_notification(f"Completed incremental backup from {source_dir}")
    if receiver_emails:
        subject = "Backup Completed"
        body = f"Backup completed for {source_dir}"
        send_email(receiver_emails, subject, body, logger=logger)

def perform_differential_backup(logger, source_dir, backup_dirs, last_full_backup_time, bot=None, receiver_emails=None):
    """
    Perform a differential backup of the source directory to the backup directories.
    """
    logger.info(f"Performing differential backup from {source_dir}")
    files = list(Path(source_dir).rglob('*'))
    for file in tqdm(files, desc="Syncing Differential Files", unit="files"):
        if os.path.getmtime(file) > last_full_backup_time:
            for backup_dir in backup_dirs:
                backup_file = Path(backup_dir) / file.relative_to(source_dir)
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, backup_file)
                if verify_backup(file, backup_file):
                    logger.info(f"Differential backup of {file} to {backup_file}")
                else:
                    logger.error(f"Checksum verification failed for {file}")

    if bot:
        bot.send_notification(f"Completed differential backup from {source_dir}")
    if receiver_emails:
        subject = "Backup Completed"
        body = f"Backup completed for {source_dir}"
        send_email(receiver_emails, subject, body, logger=logger)
