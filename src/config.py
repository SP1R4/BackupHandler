import os
import re
import sys
import configparser
from pathlib import Path
from src.utils import is_valid_email


def resolve_env_vars(value):
    """
    Replace ${ENV_VAR} placeholders in a string with their environment variable values.
    Raises ValueError if a referenced environment variable is not set.
    """
    def _replace(match):
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(
                f"Environment variable '{var_name}' is not set "
                f"(referenced as '${{{var_name}}}' in config)"
            )
        return env_value
    return re.sub(r'\$\{([^}]+)\}', _replace, value)


def _resolve_all_env_vars(config, logger):
    """
    Walk all sections (including DEFAULT) and resolve ${ENV_VAR} placeholders in-place.
    """
    sections = ['DEFAULT'] + config.sections()
    for section in sections:
        for key in config[section]:
            raw_value = config.get(section, key, raw=True)
            if '${' in raw_value:
                try:
                    resolved = resolve_env_vars(raw_value)
                    config.set(section, key, resolved)
                    logger.debug(f"Resolved env var in [{section}].{key}")
                except ValueError as e:
                    logger.error(str(e))
                    print(f"\n  Config error: {e}", file=sys.stderr)
                    sys.exit(1)


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

    # Resolve ${ENV_VAR} placeholders before validation
    _resolve_all_env_vars(config, logger)

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

        # Exclude patterns from config
        raw_exclude = normalize_none(config.get('DEFAULT', 'exclude_patterns', fallback=None))

        # Hooks
        pre_backup_hook = normalize_none(config.get('HOOKS', 'pre_backup', fallback=None))
        post_backup_hook = normalize_none(config.get('HOOKS', 'post_backup', fallback=None))

        # Retention
        max_age_days = config.getint('RETENTION', 'max_age_days', fallback=0)
        max_count = config.getint('RETENTION', 'max_count', fallback=0)

        # Parallel copies
        parallel_copies = config.getint('DEFAULT', 'parallel_copies', fallback=1)

        # SSH bandwidth limit
        bandwidth_limit = config.getint('SSH', 'bandwidth_limit', fallback=0)

        # S3 config
        s3_bucket = normalize_none(config.get('S3', 'bucket', fallback=None))
        s3_prefix = normalize_none(config.get('S3', 'prefix', fallback=None)) or ''
        s3_region = normalize_none(config.get('S3', 'region', fallback=None))
        s3_access_key = normalize_none(config.get('S3', 'access_key', fallback=None))
        s3_secret_key = normalize_none(config.get('S3', 'secret_key', fallback=None))

        # Encryption config
        encryption_enabled = config.getboolean('ENCRYPTION', 'enabled', fallback=False)
        encryption_key_file = normalize_none(config.get('ENCRYPTION', 'key_file', fallback=None))
        encryption_passphrase = normalize_none(config.get('ENCRYPTION', 'passphrase', fallback=None))

        # Database config
        db_user = normalize_none(config.get('DATABASE', 'user', fallback=None))
        db_password = normalize_none(config.get('DATABASE', 'password', fallback=None))
        db_database = normalize_none(config.get('DATABASE', 'database', fallback=None))
        db_host = normalize_none(config.get('DATABASE', 'host', fallback=None)) or 'localhost'
        db_port = config.getint('DATABASE', 'port', fallback=3306)

        # SMTP config
        smtp_host = normalize_none(config.get('SMTP', 'host', fallback=None))
        smtp_port = config.getint('SMTP', 'port', fallback=587)
        smtp_user = normalize_none(config.get('SMTP', 'user', fallback=None))
        smtp_password = normalize_none(config.get('SMTP', 'password', fallback=None))
        smtp_from = normalize_none(config.get('SMTP', 'from_addr', fallback=None))
        smtp_to = normalize_none(config.get('SMTP', 'to_addrs', fallback=None))
        smtp_tls = config.getboolean('SMTP', 'use_tls', fallback=True)

        # Dedup config
        dedup_enabled = config.getboolean('DEDUP', 'enabled', fallback=False)

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
            's3_mode': config.getboolean('MODES', 's3', fallback=False),
            'bot': config.getboolean('NOTIFICATIONS', 'bot', fallback=False),
            'receiver_emails': None,
            'exclude_patterns': [p.strip() for p in raw_exclude.split(',') if p.strip()] if raw_exclude else [],
            'pre_backup_hook': pre_backup_hook,
            'post_backup_hook': post_backup_hook,
            'max_age_days': max_age_days,
            'max_count': max_count,
            'parallel_copies': max(1, parallel_copies),
            'bandwidth_limit': max(0, bandwidth_limit),
            's3_bucket': s3_bucket,
            's3_prefix': s3_prefix,
            's3_region': s3_region,
            's3_access_key': s3_access_key,
            's3_secret_key': s3_secret_key,
            'encryption_enabled': encryption_enabled,
            'encryption_key_file': encryption_key_file,
            'encryption_passphrase': encryption_passphrase,
            'db_mode': config.getboolean('MODES', 'db', fallback=False),
            'db_user': db_user,
            'db_password': db_password,
            'db_database': db_database,
            'db_host': db_host,
            'db_port': db_port,
            'smtp_host': smtp_host,
            'smtp_port': smtp_port,
            'smtp_user': smtp_user,
            'smtp_password': smtp_password,
            'smtp_from': smtp_from,
            'smtp_to': [e.strip() for e in smtp_to.split(',') if e.strip()] if smtp_to else [],
            'smtp_tls': smtp_tls,
            'dedup_enabled': dedup_enabled,
        }

        # Resolve relative paths to absolute
        if config_vars['source_dir']:
            config_vars['source_dir'] = str(Path(config_vars['source_dir']).resolve())
        config_vars['backup_dirs'] = [str(Path(d).resolve()) for d in config_vars['backup_dirs']]

        # Parse receiver emails
        if raw_receiver_emails:
            config_vars['receiver_emails'] = [email.strip() for email in raw_receiver_emails.split(',') if email.strip()]

        if show:
            print("Current Configuration:\n")
            print("DEFAULT:")
            print(f"  Source Directory  : {config_vars['source_dir']}")
            print(f"  Mode             : {config_vars['mode']}")
            print(f"  Compress Type    : {config_vars['compress_type']}")
            print(f"  Exclude Patterns : {', '.join(config_vars['exclude_patterns']) if config_vars['exclude_patterns'] else 'None'}")
            print(f"  Parallel Copies  : {config_vars['parallel_copies']}\n")

            print("BACKUPS:")
            print(f"  Backup Directories: {', '.join(config_vars['backup_dirs'])}\n")

            print("SSH:")
            print(f"  SSH Servers      : {', '.join(config_vars['ssh_servers']) if config_vars['ssh_servers'] else 'Not Set'}")
            print(f"  SSH Username     : {config_vars['ssh_username'] or 'Not Set'}")
            print(f"  SSH Password     : {'*' * len(config_vars['ssh_password']) if config_vars['ssh_password'] else 'Not Set'}")
            print(f"  Bandwidth Limit  : {config_vars['bandwidth_limit']} KB/s\n" if config_vars['bandwidth_limit'] else "  Bandwidth Limit  : Unlimited\n")

            print("S3:")
            print(f"  Bucket  : {config_vars['s3_bucket'] or 'Not Set'}")
            print(f"  Prefix  : {config_vars['s3_prefix'] or '/'}")
            print(f"  Region  : {config_vars['s3_region'] or 'Not Set'}\n")

            print("SCHEDULE:")
            print(f"  Times          : {', '.join(config_vars['schedule_times']) if config_vars['schedule_times'] else 'Not Set'}")
            print(f"  Interval (min) : {config_vars['interval_minutes']}\n")

            print("MODES:")
            print(f"  Local Backup : {'Enabled' if config_vars['local_mode'] else 'Disabled'}")
            print(f"  SSH Backup   : {'Enabled' if config_vars['ssh_mode'] else 'Disabled'}")
            print(f"  S3 Backup    : {'Enabled' if config_vars['s3_mode'] else 'Disabled'}")
            print(f"  DB Backup    : {'Enabled' if config_vars['db_mode'] else 'Disabled'}\n")

            print("HOOKS:")
            print(f"  Pre-Backup  : {config_vars['pre_backup_hook'] or 'Not Set'}")
            print(f"  Post-Backup : {config_vars['post_backup_hook'] or 'Not Set'}\n")

            print("RETENTION:")
            print(f"  Max Age (days) : {config_vars['max_age_days'] or 'Disabled'}")
            print(f"  Max Count      : {config_vars['max_count'] or 'Unlimited'}\n")

            print("ENCRYPTION:")
            print(f"  Enabled    : {'Yes' if config_vars['encryption_enabled'] else 'No'}")
            print(f"  Key File   : {config_vars['encryption_key_file'] or 'Not Set'}")
            print(f"  Passphrase : {'*****' if config_vars['encryption_passphrase'] else 'Not Set'}\n")

            print("DATABASE:")
            print(f"  User     : {config_vars['db_user'] or 'Not Set'}")
            print(f"  Password : {'*****' if config_vars['db_password'] else 'Not Set'}")
            print(f"  Database : {config_vars['db_database'] or 'Not Set'}")
            print(f"  Host     : {config_vars['db_host']}")
            print(f"  Port     : {config_vars['db_port']}\n")

            print("SMTP:")
            print(f"  Host     : {config_vars['smtp_host'] or 'Not Set'}")
            print(f"  Port     : {config_vars['smtp_port']}")
            print(f"  User     : {config_vars['smtp_user'] or 'Not Set'}")
            print(f"  From     : {config_vars['smtp_from'] or 'Not Set'}")
            print(f"  To       : {', '.join(config_vars['smtp_to']) if config_vars['smtp_to'] else 'Not Set'}")
            print(f"  TLS      : {'Yes' if config_vars['smtp_tls'] else 'No'}\n")

            print("DEDUP:")
            print(f"  Enabled  : {'Yes' if config_vars['dedup_enabled'] else 'No'}\n")

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

    # Validate S3 fields only when MODES.s3 = True
    s3_enabled = False
    if 'MODES' in config:
        try:
            s3_enabled = config.getboolean('MODES', 's3', fallback=False)
        except ValueError:
            errors.append("Config error: 's3' in [MODES] must be True or False")

    if s3_enabled:
        if not normalize_none(config.get('S3', 'bucket', fallback=None)):
            errors.append("Config error: 'bucket' is not set in [S3]. Required when s3 mode is enabled")
        if not normalize_none(config.get('S3', 'region', fallback=None)):
            errors.append("Config error: 'region' is not set in [S3]. Required when s3 mode is enabled")

    # Validate encryption: when enabled, require either key_file or passphrase
    encryption_enabled = False
    try:
        encryption_enabled = config.getboolean('ENCRYPTION', 'enabled', fallback=False)
    except ValueError:
        errors.append("Config error: 'enabled' in [ENCRYPTION] must be True or False")

    if encryption_enabled:
        has_key_file = normalize_none(config.get('ENCRYPTION', 'key_file', fallback=None))
        has_passphrase = normalize_none(config.get('ENCRYPTION', 'passphrase', fallback=None))
        if not has_key_file and not has_passphrase:
            errors.append("Config error: [ENCRYPTION] is enabled but neither 'key_file' nor 'passphrase' is set")

    # Validate database fields only when MODES.db = True
    db_enabled = False
    if 'MODES' in config:
        try:
            db_enabled = config.getboolean('MODES', 'db', fallback=False)
        except ValueError:
            errors.append("Config error: 'db' in [MODES] must be True or False")

    if db_enabled:
        if not normalize_none(config.get('DATABASE', 'user', fallback=None)):
            errors.append("Config error: 'user' is not set in [DATABASE]. Required when db mode is enabled")
        if not normalize_none(config.get('DATABASE', 'password', fallback=None)):
            errors.append("Config error: 'password' is not set in [DATABASE]. Required when db mode is enabled")
        if not normalize_none(config.get('DATABASE', 'database', fallback=None)):
            errors.append("Config error: 'database' is not set in [DATABASE]. Required when db mode is enabled")

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
        for key in ('local', 'ssh', 's3', 'db'):
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
