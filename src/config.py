import sys
import configparser
from src.utils import is_valid_email


def normalize_none(value):
    """
    Return None if value is 'None', empty, or whitespace-only.
    Otherwise return the stripped string.
    """
    if value is None:
        return None
    stripped = str(value).strip()
    if stripped.lower() == 'none' or stripped == '':
        return None
    return stripped


def extract_config_values(logger, config_file_path, show=False, require_schedule=False,
                          skip_validation=False):
    """
    Extract configuration values from the specified INI file and return them as a dictionary.

    Parameters:
    - config_file_path (str): The path to the configuration file.
    - show (bool): If True, print the configuration dictionary. If False, return the dictionary.
    - require_schedule (bool): If True, validate that schedule times are present.
    - skip_validation (bool): If True, skip config validation (for --show-setup debugging).

    Returns:
    - dict: A dictionary containing the extracted configuration values if show=False.
    """
    config = load_config(logger, config_file_path)

    # Run validation before extracting values (skip for --show-setup)
    if not skip_validation:
        validate_config(logger, config, require_schedule=require_schedule)

    try:
        # Extract and clean values with defaults
        schedule_times = config.get('SCHEDULE', 'times', fallback=None)

        # Normalize optional fields to avoid "None" strings
        raw_ssh_servers = normalize_none(config.get('SSH', 'ssh_servers', fallback=None))
        raw_username = normalize_none(config.get('SSH', 'username', fallback=None))
        raw_password = normalize_none(config.get('SSH', 'password', fallback=None))
        raw_receiver_emails = normalize_none(config.get('NOTIFICATIONS', 'receiver_emails', fallback=None))

        config_vars = {
            'source_dir': config.get('DEFAULT', 'source_dir', fallback=None),
            'mode': config.get('DEFAULT', 'mode', fallback='full'),
            'compress_type': config.get('DEFAULT', 'compress_type', fallback='none'),
            'backup_dirs': [dir.strip() for dir in config.get('BACKUPS', 'backup_dirs', fallback='').split(',') if dir.strip()],
            'ssh_servers': [s.strip() for s in raw_ssh_servers.split(',') if s.strip()] if raw_ssh_servers else [],
            'ssh_username': raw_username,
            'ssh_password': raw_password,
            'schedule_times': [time.strip() for time in schedule_times.split(',') if time.strip()] if schedule_times else [],
            'interval_minutes': config.getint('SCHEDULE', 'interval_minutes', fallback=1),
            'local_mode': config.getboolean('MODES', 'local', fallback=False),
            'ssh_mode': config.getboolean('MODES', 'ssh', fallback=False),
            'bot': config.getboolean('NOTIFICATIONS', 'bot', fallback=False),
            'receiver_emails': None,
        }

        # Parse receiver emails
        if raw_receiver_emails:
            config_vars['receiver_emails'] = [email.strip() for email in raw_receiver_emails.split(',') if email.strip()]

        if show:
            print("Current Configuration:\n")
            print("DEFAULT:")
            print(f"  Source Directory : {config_vars['source_dir']}")
            print(f"  Mode             : {config_vars['mode']}")
            print(f"  Compress Type    : {config_vars['compress_type']}\n")

            print("BACKUPS:")
            print(f"  Backup Directories: {', '.join(config_vars['backup_dirs'])}\n")

            print("SSH:")
            print(f"  SSH Servers  : {', '.join(config_vars['ssh_servers']) if config_vars['ssh_servers'] else 'Not Set'}")
            print(f"  SSH Username : {config_vars['ssh_username'] or 'Not Set'}")
            print(f"  SSH Password : {'*' * len(config_vars['ssh_password']) if config_vars['ssh_password'] else 'Not Set'}\n")

            print("SCHEDULE:")
            print(f"  Times          : {', '.join(config_vars['schedule_times']) if config_vars['schedule_times'] else 'Not Set'}")
            print(f"  Interval (min) : {config_vars['interval_minutes']}\n")

            print("MODES:")
            print(f"  Local Backup : {'Enabled' if config_vars['local_mode'] else 'Disabled'}")
            print(f"  SSH Backup   : {'Enabled' if config_vars['ssh_mode'] else 'Disabled'}\n")

            print("NOTIFICATIONS:")
            print(f"  Bot             : {'Enabled' if config_vars['bot'] else 'Disabled'}")
            print(f"  Receiver Emails : {', '.join(config_vars['receiver_emails']) if config_vars['receiver_emails'] else 'Disabled'}\n")
        else:
            return config_vars

    except Exception as e:
        logger.error(f"Error extracting config values: {e}")
        raise

def validate_config(logger, config, require_schedule=False):
    """
    Validate that required configurations are set.
    Exits with clear error messages pointing users to the exact config field.

    Parameters:
    - logger (logging.Logger): Logger instance for logging errors.
    - config (configparser.ConfigParser): Loaded configuration object.
    - require_schedule (bool): If True, require schedule times to be set.
    """
    errors = []

    # Always required: source_dir, mode, backup_dirs
    if not normalize_none(config.get('DEFAULT', 'source_dir', fallback=None)):
        errors.append("Config error: 'source_dir' is not set in [DEFAULT]. Set it in config/config.ini")

    mode = normalize_none(config.get('DEFAULT', 'mode', fallback=None))
    if not mode:
        errors.append("Config error: 'mode' is not set in [DEFAULT]. Set it in config/config.ini")
    elif mode not in ('full', 'incremental', 'differential'):
        errors.append(f"Config error: 'mode' in [DEFAULT] must be full, incremental, or differential, got '{mode}'")

    if not config.get('BACKUPS', 'backup_dirs', fallback='').strip():
        errors.append("Config error: 'backup_dirs' is not set in [BACKUPS]. Set it in config/config.ini")

    # Validate compress_type if set
    compress_type = normalize_none(config.get('DEFAULT', 'compress_type', fallback=None))
    valid_compress = ('none', 'zip', 'zip_pw')
    if compress_type and compress_type not in valid_compress:
        errors.append(f"Config error: 'compress_type' in [DEFAULT] must be one of {valid_compress}, got '{compress_type}'")

    # Validate SSH fields only when MODES.ssh = True
    ssh_enabled = False
    if 'MODES' in config:
        try:
            ssh_enabled = config.getboolean('MODES', 'ssh', fallback=False)
        except ValueError:
            errors.append("Config error: 'ssh' in [MODES] must be True or False")

    if ssh_enabled:
        if not normalize_none(config.get('SSH', 'ssh_servers', fallback=None)):
            errors.append("Config error: 'ssh_servers' is not set in [SSH]. Required when ssh mode is enabled")
        if not normalize_none(config.get('SSH', 'username', fallback=None)):
            errors.append("Config error: 'username' is not set in [SSH]. Required when ssh mode is enabled")
        if not normalize_none(config.get('SSH', 'password', fallback=None)):
            errors.append("Config error: 'password' is not set in [SSH]. Required when ssh mode is enabled")

    # Validate schedule only when --scheduled is used
    if require_schedule:
        schedule_times = normalize_none(config.get('SCHEDULE', 'times', fallback=None))
        if not schedule_times:
            errors.append("Config error: 'times' is not set in [SCHEDULE]. Required for --scheduled mode")
        else:
            for t in schedule_times.split(','):
                t = t.strip()
                if t and not is_valid_time_format(t):
                    errors.append(f"Config error: Invalid time format '{t}' in [SCHEDULE]. Use HH:MM (24-hour)")

        if config.has_option('SCHEDULE', 'interval_minutes'):
            try:
                interval = config.getint('SCHEDULE', 'interval_minutes')
                if interval <= 0:
                    errors.append("Config error: 'interval_minutes' in [SCHEDULE] must be a positive integer")
            except ValueError:
                errors.append("Config error: 'interval_minutes' in [SCHEDULE] must be a valid integer")

    # Validate MODES values
    if 'MODES' in config:
        for key in ('local', 'ssh'):
            val = config.get('MODES', key, fallback=None)
            if val is not None and val not in ('True', 'False', 'true', 'false'):
                errors.append(f"Config error: '{key}' in [MODES] must be True or False, got '{val}'")

    # Validate email format when receiver_emails is set
    raw_emails = normalize_none(config.get('NOTIFICATIONS', 'receiver_emails', fallback=None))
    if raw_emails:
        for email in raw_emails.split(','):
            email = email.strip()
            if email and not is_valid_email(email):
                errors.append(f"Config error: Invalid email address '{email}' in [NOTIFICATIONS].receiver_emails")

    if errors:
        for err in errors:
            logger.error(err)
        print("\nConfiguration errors found:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

def is_valid_time_format(time_string):
    """
    Check if the time string is in the format HH:MM (24-hour format).
    """
    from datetime import datetime
    try:
        datetime.strptime(time_string, "%H:%M")
        return True
    except ValueError:
        return False

def load_config(logger, config_path):
    """
    Load configuration from the specified INI file.
    """
    config = configparser.ConfigParser()

    try:
        config.read(config_path)

        if not config.sections():
            raise configparser.Error(f"Config file '{config_path}' is empty or not correctly formatted.")

        logger.info(f"Configuration loaded successfully from {config_path}")
        return config

    except configparser.Error as e:
        logger.error(f"ConfigParser error while reading file '{config_path}': {e}")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error while loading config file '{config_path}': {e}")
        sys.exit(1)

