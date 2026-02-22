import os
import re
import time
import json
import random
import string
import hashlib
import keyring
from pathlib import Path


# Define file paths for storing timestamps of backups (absolute, relative to project root)
_PROJECT_ROOT = Path(__file__).parent.parent
TIMESTAMP_FILE = _PROJECT_ROOT / "BackupTimestamp" / "backup_timestamp.json"
FULL_BACKUP_TIMESTAMP_FILE = _PROJECT_ROOT / "BackupTimestamp" / "full_backup_timestamp.json"

def generate_otp(length=8):
    """
    Generates a random one-time password (OTP) consisting of letters and digits.

    Parameters:
    - length (int): The length of the OTP. Defaults to 8 characters.

    Returns:
    - str: A randomly generated password.
    """
    characters = string.ascii_letters + string.digits
    otp = ''.join(random.choice(characters) for _ in range(length))
    return otp

def get_last_backup_time():
    """
    Retrieve the timestamp of the last incremental backup.

    Returns:
    - int: The timestamp of the last backup, or 0 if no backup has been performed.

    Reads the timestamp from a JSON file. Defaults to epoch time if the file does not exist.
    """
    if TIMESTAMP_FILE.exists():
        with open(TIMESTAMP_FILE, "r") as f:
            data = json.load(f)
        return data.get("last_backup_time", 0)
    else:
        return 0  # Default to epoch if no backup has been performed

def update_last_backup_time():
    """
    Update the timestamp of the last incremental backup.

    This function writes the current time (in seconds since epoch) to the JSON file.
    """
    TIMESTAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_backup_time": int(time.time())}
    with open(TIMESTAMP_FILE, "w") as f:
        json.dump(data, f)

def get_last_full_backup_time():
    """
    Retrieve the timestamp of the last full backup.

    Returns:
    - int: The timestamp of the last full backup, or 0 if no full backup has been performed.

    Reads the timestamp from a JSON file. Defaults to epoch time if the file does not exist.
    """
    if FULL_BACKUP_TIMESTAMP_FILE.exists():
        with open(FULL_BACKUP_TIMESTAMP_FILE, "r") as f:
            data = json.load(f)
        return data.get("last_full_backup_time", 0)
    else:
        return 0  # Default to epoch if no full backup has been performed

def update_last_full_backup_time():
    """
    Update the timestamp of the last full backup.

    This function writes the current time (in seconds since epoch) to the JSON file.
    """
    FULL_BACKUP_TIMESTAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_full_backup_time": int(time.time())}
    with open(FULL_BACKUP_TIMESTAMP_FILE, "w") as f:
        json.dump(data, f)

def calculate_checksum(file_path, logger=None):
    """
    Calculate the SHA-256 checksum of a file.

    Parameters:
    - file_path (str): The path to the file for which to calculate the checksum.
    - logger (logging.Logger, optional): Logger instance for logging errors.

    Returns:
    - str: The SHA-256 checksum of the file, or None if an error occurs.
    """
    hash_sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
    except Exception as e:
        if logger:
            logger.error(f"Failed to generate checksum for {file_path}: {e}")
        return None
    return hash_sha256.hexdigest()

def verify_backup(source_file, destination_file):
    """
    Verify that a backup file matches the source file by comparing checksums.

    Parameters:
    - source_file (str): The path to the source file.
    - destination_file (str): The path to the backup file.

    Returns:
    - bool: True if the checksums match, False otherwise.
    """
    return calculate_checksum(source_file) == calculate_checksum(destination_file)

def _get_backup_checksums(backup):
    """
    Generate a dictionary of file checksums for all files in the backup directory.

    Parameters:
    - backup (str): The path to the backup directory.

    Returns:
    - dict: A dictionary where keys are file paths and values are their SHA-256 checksums.
    """
    checksums = {}
    for root, dirs, files in os.walk(backup):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            checksum = calculate_checksum(file_path)
            if checksum:
                checksums[file_path] = checksum
    return checksums

def handle_symlink(logger, src_path, dst_path):
    """
    Handle symbolic links by copying the link itself rather than resolving it.

    Parameters:
    - src_path (str): The path to the source symbolic link.
    - dst_path (str): The path where the new symbolic link will be created.
    """
    try:
        os.symlink(os.readlink(src_path), dst_path)
        logger.info(f"Created symlink '{dst_path}' pointing to '{os.readlink(src_path)}'")
    except Exception as e:
        logger.error(f"Failed to create symlink '{dst_path}': {e}")

def validate_directories(logger, source_dir, backup_dir):
    """
    Validate the existence of the source and backup directories.

    Parameters:
    - source_dir (str): The path to the source directory.
    - backup_dir (str): The path to the backup directory.
    """
    try:
        if not os.path.exists(source_dir):
            raise FileNotFoundError(f"Source directory '{source_dir}' does not exist.")

        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            logger.info(f"Backup directory '{backup_dir}' created.")
        else:
            logger.info(f"Backup directory '{backup_dir}' exists.")
    except Exception as e:
        logger.error(f"Error validating directories: {e}")
        raise

def list_files_in_directory(directory):
    """
    List all files in a directory and its subdirectories.

    Parameters:
    - directory (str): The path to the directory.

    Returns:
    - List[Path]: A list of file paths in the directory.
    """
    return [f for f in Path(directory).rglob('*') if f.is_file()]

def is_valid_email(email):
    """
    Check if the provided email address is valid.

    Parameters:
    - email (str): The email address to validate.

    Returns:
    - bool: True if the email is valid, False otherwise.
    """
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_pattern, email) is not None

def get_password_by_timestamp(timestamp, logger):
    """
    Retrieves a password from keyring using a given timestamp as the username.

    Args:
        timestamp (str): The timestamp used as the key (username) to retrieve the password.
        logger (logging.Logger): Logger instance for logging.

    Returns:
        str: The password if found, or None if not found.
    """
    try:
        service_name = "compression_service"
        password = keyring.get_password(service_name, timestamp)

        if password:
            logger.info(f"Password retrieved successfully for timestamp: {timestamp}")
            return password
        else:
            logger.warning(f"No password found for timestamp: {timestamp}")
            return None
    except Exception as e:
        logger.error(f"Failed to retrieve password for timestamp '{timestamp}': {e}")
        return None

def clear_keyring(service_name='compression_service', logger=None):
    """
    Clears all stored credentials in the keyring for the specified service.

    Args:
        service_name (str): The name of the service for which to clear stored credentials.
        logger (logging.Logger, optional): Logger instance to log messages.
    """
    try:
        credentials = keyring.get_credential(service_name, None)

        if credentials is not None:
            keyring.delete_password(service_name, credentials.username)
            msg = f"Cleared credentials for service '{service_name}'."
        else:
            msg = f"No credentials found for service '{service_name}'."

        if logger:
            logger.info(msg)
        else:
            print(msg)

    except Exception as e:
        error_msg = f"Failed to clear keyring for service '{service_name}': {e}"
        if logger:
            logger.error(error_msg)
        else:
            print(error_msg)
