import os
import re
import stat
import shutil
import tempfile
import zipfile
from pathlib import Path
from .utils import verify_backup
from .manifest import load_manifests_up_to
from .encryption import decrypt_directory


def _is_ssh_path(path):
    """Check if path looks like user@host:/path or ssh://user@host/path."""
    return bool(re.match(r'^[\w.-]+@[\w.-]+:.+', path) or path.startswith('ssh://'))


def _is_s3_path(path):
    """Check if path looks like s3://bucket/prefix."""
    return path.startswith('s3://')


def _parse_ssh_path(path):
    """Parse user@host:/remote/path into (user, host, remote_path)."""
    if path.startswith('ssh://'):
        # ssh://user@host/path
        path = path[6:]
        if '@' in path:
            user_host, remote = path.split('/', 1) if '/' in path else (path, '')
            user, host = user_host.split('@', 1)
        else:
            user = None
            host, remote = path.split('/', 1) if '/' in path else (path, '')
        return user, host, '/' + remote
    # user@host:/path
    user_host, remote_path = path.split(':', 1)
    if '@' in user_host:
        user, host = user_host.split('@', 1)
    else:
        user, host = None, user_host
    return user, host, remote_path


def _parse_s3_path(path):
    """Parse s3://bucket/prefix into (bucket, prefix)."""
    stripped = path[5:]  # remove s3://
    if '/' in stripped:
        bucket, prefix = stripped.split('/', 1)
    else:
        bucket, prefix = stripped, ''
    return bucket, prefix


def _download_from_ssh(logger, ssh_path, local_dir, ssh_password=None):
    """Download a remote directory via SFTP to a local directory."""
    try:
        import paramiko
    except ImportError:
        logger.error("paramiko is not installed. Install it with: pip install paramiko")
        return False

    user, host, remote_path = _parse_ssh_path(ssh_path)
    logger.info(f"Downloading from SSH: {user}@{host}:{remote_path} -> {local_dir}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.WarningPolicy())

    try:
        ssh.connect(hostname=host, username=user, password=ssh_password)
        sftp = ssh.open_sftp()

        try:
            _sftp_download_recursive(sftp, remote_path, local_dir, logger)
        finally:
            sftp.close()

        logger.info(f"SSH download complete: {remote_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to download from SSH {host}: {e}")
        return False
    finally:
        ssh.close()


def _sftp_download_recursive(sftp, remote_dir, local_dir, logger):
    """Recursively download a remote directory via SFTP."""
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    try:
        entries = sftp.listdir_attr(remote_dir)
    except Exception as e:
        logger.error(f"Cannot list remote directory {remote_dir}: {e}")
        return

    for entry in entries:
        remote_entry = f"{remote_dir}/{entry.filename}"
        local_entry = local_dir / entry.filename

        if stat.S_ISDIR(entry.st_mode):
            _sftp_download_recursive(sftp, remote_entry, str(local_entry), logger)
        else:
            try:
                sftp.get(remote_entry, str(local_entry))
                logger.debug(f"Downloaded: {remote_entry}")
            except Exception as e:
                logger.error(f"Failed to download {remote_entry}: {e}")


def _download_from_s3(logger, s3_path, local_dir, region=None,
                      access_key=None, secret_key=None):
    """Download files from an S3 prefix to a local directory."""
    try:
        import boto3
    except ImportError:
        logger.error("boto3 is not installed. Install it with: pip install boto3")
        return False

    bucket, prefix = _parse_s3_path(s3_path)
    logger.info(f"Downloading from S3: s3://{bucket}/{prefix} -> {local_dir}")

    session_kwargs = {}
    if region:
        session_kwargs['region_name'] = region
    if access_key and secret_key:
        session_kwargs['aws_access_key_id'] = access_key
        session_kwargs['aws_secret_access_key'] = secret_key

    s3 = boto3.client('s3', **session_kwargs)
    local_dir = Path(local_dir)

    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_kwargs = {'Bucket': bucket}
        if prefix:
            page_kwargs['Prefix'] = prefix

        downloaded = 0
        for page in paginator.paginate(**page_kwargs):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # Compute relative path from prefix
                if prefix:
                    relative = key[len(prefix):].lstrip('/')
                else:
                    relative = key

                if not relative:
                    continue

                local_file = local_dir / relative
                local_file.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, key, str(local_file))
                downloaded += 1

        logger.info(f"S3 download complete: {downloaded} files from s3://{bucket}/{prefix}")
        return True
    except Exception as e:
        logger.error(f"Failed to download from S3: {e}")
        return False


def restore_backup(logger, from_dir, to_dir, timestamp=None,
                   encryption_passphrase=None, encryption_key_file=None,
                   ssh_password=None, s3_region=None, s3_access_key=None,
                   s3_secret_key=None):
    """
    Restore files from a local, SSH, or S3 backup source.

    Parameters:
    - logger: Logger instance.
    - from_dir (str): Source — local path, user@host:/path, or s3://bucket/prefix.
    - to_dir (str): Destination directory to restore files to.
    - timestamp (str, optional): Restore to a specific point in time (YYYYMMDD_HHMMSS).
    - encryption_passphrase (str, optional): Passphrase for decrypting .enc files.
    - encryption_key_file (str, optional): Path to key file for decrypting .enc files.
    - ssh_password (str, optional): SSH password for remote restore.
    - s3_region (str, optional): AWS region for S3 restore.
    - s3_access_key (str, optional): AWS access key for S3 restore.
    - s3_secret_key (str, optional): AWS secret key for S3 restore.

    Returns:
    - bool: True if restore completed successfully, False otherwise.
    """
    to_path = Path(to_dir)
    to_path.mkdir(parents=True, exist_ok=True)

    # Remote SSH restore
    if _is_ssh_path(from_dir):
        with tempfile.TemporaryDirectory() as tmp_dir:
            if not _download_from_ssh(logger, from_dir, tmp_dir, ssh_password=ssh_password):
                return False
            return _restore_local(logger, Path(tmp_dir), to_path, timestamp,
                                  encryption_passphrase, encryption_key_file)

    # Remote S3 restore
    if _is_s3_path(from_dir):
        with tempfile.TemporaryDirectory() as tmp_dir:
            if not _download_from_s3(logger, from_dir, tmp_dir, region=s3_region,
                                     access_key=s3_access_key, secret_key=s3_secret_key):
                return False
            return _restore_local(logger, Path(tmp_dir), to_path, timestamp,
                                  encryption_passphrase, encryption_key_file)

    # Local restore
    from_path = Path(from_dir)
    if not from_path.exists():
        logger.error(f"Restore source does not exist: {from_dir}")
        return False

    to_path.mkdir(parents=True, exist_ok=True)

    # ZIP archive restore
    if from_path.is_file() and from_path.suffix == '.zip':
        return _restore_from_zip(logger, from_path, to_path)

    # Directory restore
    if from_path.is_dir():
        # Check if backup contains encrypted files
        has_enc_files = any(from_path.rglob('*.enc'))
        if has_enc_files and (encryption_passphrase or encryption_key_file):
            logger.info("Encrypted files detected. Decrypting to temporary directory before restore.")
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                # Copy backup to temp dir to avoid modifying original
                shutil.copytree(from_path, tmp_path / 'backup', dirs_exist_ok=True)
                decrypt_dir = tmp_path / 'backup'
                decrypt_directory(decrypt_dir, passphrase=encryption_passphrase,
                                  key_file=encryption_key_file, logger=logger)
                if timestamp:
                    return _restore_with_manifests(logger, decrypt_dir, to_path, timestamp)
                else:
                    return _restore_full_directory(logger, decrypt_dir, to_path)
        elif has_enc_files:
            logger.warning("Encrypted files detected but no encryption passphrase or key_file configured. "
                           "Encrypted files will be copied as-is.")

        if timestamp:
            return _restore_with_manifests(logger, from_path, to_path, timestamp)
        else:
            return _restore_full_directory(logger, from_path, to_path)

    logger.error(f"Unsupported restore source: {from_dir}")
    return False


def _restore_local(logger, from_path, to_path, timestamp, encryption_passphrase, encryption_key_file):
    """Handle local restore with encryption detection (used by SSH/S3 after download)."""
    has_enc_files = any(from_path.rglob('*.enc'))
    if has_enc_files and (encryption_passphrase or encryption_key_file):
        logger.info("Encrypted files detected in downloaded backup. Decrypting before restore.")
        decrypt_directory(from_path, passphrase=encryption_passphrase,
                          key_file=encryption_key_file, logger=logger)
    elif has_enc_files:
        logger.warning("Encrypted files detected but no encryption credentials. Files will be copied as-is.")

    if timestamp:
        return _restore_with_manifests(logger, from_path, to_path, timestamp)
    else:
        return _restore_full_directory(logger, from_path, to_path)


def _restore_from_zip(logger, zip_path, to_dir):
    """Extract a ZIP archive to the destination directory."""
    logger.info(f"Restoring from ZIP archive: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(to_dir)
        logger.info(f"Successfully restored {zip_path} to {to_dir}")
        return True
    except zipfile.BadZipFile:
        logger.error(f"Bad ZIP file: {zip_path}")
        return False
    except Exception as e:
        logger.error(f"Failed to restore from ZIP: {e}")
        return False


def _restore_full_directory(logger, from_dir, to_dir):
    """Full reverse copy from backup directory to destination with verification."""
    logger.info(f"Restoring full directory: {from_dir} -> {to_dir}")
    files = [f for f in from_dir.rglob('*') if f.is_file()
             and not (f.name.startswith('backup_manifest_') and f.suffix == '.json')]

    copied = 0
    failed = 0

    for file in files:
        relative = file.relative_to(from_dir)
        dest_file = to_dir / relative

        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            if file.is_symlink():
                link_target = os.readlink(file)
                if dest_file.exists() or dest_file.is_symlink():
                    dest_file.unlink()
                os.symlink(link_target, dest_file)
                copied += 1
                continue

            shutil.copy2(file, dest_file)

            if verify_backup(file, dest_file):
                logger.info(f"Restored: {relative}")
                copied += 1
            else:
                logger.error(f"Checksum verification failed during restore: {relative}")
                failed += 1
        except Exception as e:
            logger.error(f"Failed to restore {relative}: {e}")
            failed += 1

    logger.info(f"Restore complete: {copied} files restored, {failed} failures")
    return failed == 0


def _restore_with_manifests(logger, from_dir, to_dir, timestamp):
    """
    Restore using manifests up to a specific timestamp.
    Applies manifests in chronological order to reconstruct state at that point.
    """
    logger.info(f"Restoring with manifests up to timestamp: {timestamp}")
    manifests = load_manifests_up_to(from_dir, timestamp)

    if not manifests:
        logger.warning(f"No manifests found up to timestamp {timestamp}. Falling back to full directory restore.")
        return _restore_full_directory(logger, from_dir, to_dir)

    logger.info(f"Found {len(manifests)} manifest(s) to apply")

    # Collect all files that were copied (latest version wins)
    files_to_restore = {}
    for manifest in manifests:
        for entry in manifest.get('copied', []):
            files_to_restore[entry['path']] = entry

    copied = 0
    failed = 0

    for file_path, entry in files_to_restore.items():
        src = Path(file_path)
        # Try to find the file in the backup directory structure
        # The manifest records the original source path; the file is stored
        # relative to from_dir
        if src.is_absolute():
            # Try to find it relative to from_dir
            for candidate_base in [from_dir]:
                # Try matching by filename/relative path patterns
                matches = list(candidate_base.rglob(src.name))
                if matches:
                    src = matches[0]
                    break

        if not src.exists():
            logger.warning(f"Source file not found for restore: {file_path}")
            failed += 1
            continue

        # Determine destination
        try:
            relative = src.relative_to(from_dir)
        except ValueError:
            relative = Path(src.name)

        dest_file = to_dir / relative

        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_file)

            if verify_backup(src, dest_file):
                logger.info(f"Restored: {relative}")
                copied += 1
            else:
                logger.error(f"Checksum verification failed: {relative}")
                failed += 1
        except Exception as e:
            logger.error(f"Failed to restore {relative}: {e}")
            failed += 1

    logger.info(f"Manifest-based restore complete: {copied} files restored, {failed} failures")
    return failed == 0
