import sys
import configparser
from src.utils import is_valid_email


def extract_config_values(logger, config_file_path, show=False):
    """
    Extract configuration values from the specified INI file and return them as a dictionary.

    Parameters:
    - config_file_path (str): The path to the configuration file.
    - show (bool): If True, print the configuration dictionary. If False, return the dictionary.

    Returns:
    - dict: A dictionary containing the extracted configuration values if show=False.
    """
    config = load_config(logger, config_file_path)
    
    try:
        # Check if 'SCHEDULE' section exists
        if 'SCHEDULE' not in config:
            logger.error("SCHEDULE section not found in the configuration.")
            raise KeyError("SCHEDULE section not found.")

        # Extract and clean values with defaults
        schedule_times = config.get('SCHEDULE', 'times', fallback=None)
        if schedule_times is None:
            logger.error("Schedule times not found in the configuration.")
            raise KeyError("Schedule times not found.")
        
        config_vars = {
            'source_dir': config.get('DEFAULT', 'source_dir', fallback=None),
            'mode': config.get('DEFAULT', 'mode', fallback='full'),
            'compress_type': config.get('DEFAULT', 'compress_type', fallback='none'),
            'backup_dirs': [dir.strip() for dir in config.get('BACKUPS', 'backup_dirs', fallback='').split(',') if dir.strip()],
            'ssh_servers': [server.strip() for server in config.get('SSH', 'ssh_servers', fallback='').split(',') if server.strip()],
            'ssh_username': config.get('SSH', 'username', fallback=None),
            'ssh_password': config.get('SSH', 'password', fallback=None),
            'schedule_times': [time.strip() for time in schedule_times.split(',') if time.strip()],
            'interval_minutes': config.getint('SCHEDULE', 'interval_minutes', fallback=1),
            'local_mode': config.getboolean('MODES', 'local', fallback=False),
            'ssh_mode': config.getboolean('MODES', 'ssh', fallback=False),
            'bot': config.getboolean('NOTIFICATIONS', 'bot', fallback=False),
            'receiver_emails': config.get('NOTIFICATIONS', 'receiver_emails', fallback='None').strip()
        }
        
        # Check if 'times' key exists
        if not config_vars['schedule_times']:
            logger.error("Schedule times not found in the configuration.")
            raise KeyError("Schedule times not found.")

        # Convert 'None' string to actual None type
        if config_vars['receiver_emails'].lower() == 'none':
            config_vars['receiver_emails'] = None
        else:
            config_vars['receiver_emails'] = [email.strip() for email in config_vars['receiver_emails'].split(',') if email.strip()]
        
        if show:
            print("Current Configuration:\n")
            print("DEFAULT:")
            print(f"  Source Directory : {config_vars['source_dir']}")
            print(f"  Mode             : {config_vars['mode']}")
            print(f"  Compress Type    : {config_vars['compress_type']}\n")
            
            print("BACKUPS:")
            print(f"  Backup Directories: {', '.join(config_vars['backup_dirs'])}\n")
            
            print("SSH:")
            print(f"  SSH Servers  : {', '.join(config_vars['ssh_servers'])}")
            print(f"  SSH Username : {config_vars['ssh_username']}")
            print(f"  SSH Password : {'*' * len(config_vars['ssh_password']) if config_vars['ssh_password'] else 'Not Set'}\n")
            
            print("SCHEDULE:")
            print(f"  Times          : {', '.join(config_vars['schedule_times'])}")
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

def validate_config(logger, config):
    """
    Validate that required configurations are set.

    Parameters:
    - logger (logging.Logger): Logger instance for logging errors and information.
    - config (configparser.ConfigParser): Loaded configuration object.

    Raises:
    - ValueError: If required configuration parameters are missing or invalid.
    """
    try:
        # Validate DEFAULT section
        if not config.get('DEFAULT', 'source_dir', fallback=None):
            logger.error("Source directory not set in the configuration.")
            raise ValueError("Source directory not set.")
        if not config.get('DEFAULT', 'mode', fallback=None):
            logger.error("Backup mode not set in the configuration.")
            raise ValueError("Backup mode not set.")
        if not config.get('DEFAULT', 'compress_type', fallback=None):
            logger.error("Compression type not set in the configuration.")
            raise ValueError("Compression type not set.")

        # Validate BACKUPS section
        if not config.get('BACKUPS', 'backup_dirs', fallback=None):
            logger.error("Backup directories not set in the configuration.")
            raise ValueError("Backup directories not set.")

        # Validate SSH section if it exists
        if 'SSH' in config:
            if not config.get('SSH', 'ssh_servers', fallback=None):
                logger.error("SSH servers not set in the configuration.")
                raise ValueError("SSH servers not set.")
            if not config.get('SSH', 'username', fallback=None):
                logger.error("SSH username not set in the configuration.")
                raise ValueError("SSH username not set.")
            if not config.get('SSH', 'password', fallback=None):
                logger.error("SSH password not set in the configuration.")
                raise ValueError("SSH password not set.")

        # Validate SCHEDULE section if it exists
        if 'SCHEDULE' in config:
            schedule_times = config.get('SCHEDULE', 'times', fallback=None)
            if not schedule_times:
                logger.error("Schedule times not set in the configuration.")
                raise ValueError("Schedule times not set.")
            # Validate the format of each schedule time
            schedule_times_list = [time.strip() for time in config.get('SCHEDULE', 'times', fallback='03:00').split(',') if time.strip()]
            #schedule_times_list = [time.strip() for time in schedule_times.split(',')]
            for time in schedule_times_list:
                if not is_valid_time_format(time):
                    logger.error(f"Invalid time format in schedule: {time}")
                    raise ValueError(f"Invalid time format: {time}")
            
            # Validate interval_minutes if present
            if config.has_option('SCHEDULE', 'interval_minutes'):
                interval_minutes = config.getint('SCHEDULE', 'interval_minutes', fallback=None)
                if interval_minutes is None or interval_minutes <= 0:
                    logger.error("Interval minutes must be a positive integer.")
                    raise ValueError("Interval minutes must be a positive integer.")

        # Validate MODES section
        if 'MODES' in config:
            if config.get('MODES', 'local', fallback=None) not in ['True', 'False']:
                logger.error("Invalid value for 'local' in MODES section.")
                raise ValueError("Invalid value for 'local' in MODES section.")
            if config.get('MODES', 'ssh', fallback=None) not in ['True', 'False']:
                logger.error("Invalid value for 'ssh' in MODES section.")
                raise ValueError("Invalid value for 'ssh' in MODES section.")
        
        # Validate NOTIFICATIONS section
        if 'NOTIFICATIONS' in config:
            receiver_emails = config.get('NOTIFICATIONS', 'receiver_emails', fallback=None)
            if receiver_emails is not None and receiver_emails.lower() != 'none':
                # Validate email addresses
                email_list = [email.strip() for email in receiver_emails.split(',')]
                for email in email_list:
                    if not is_valid_email(email):
                        logger.error(f"Invalid email address: {email}")
            else:
                logger.info("Email notifications are disabled.")

    except configparser.Error as e:
        logger.error(f"Error reading the configuration file: {e}")
        raise
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise

def is_valid_time_format(time_string):
    """
    Check if the time string is in the format HH:MM (24-hour format).

    Parameters:
    - time_string (str): The time string to validate.

    Returns:
    - bool: True if the time format is valid, False otherwise.
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

    Parameters:
    - logger (logging.Logger): Logger instance for logging errors and information.
    - config_path (str): The path to the INI configuration file.

    Returns:
    - config (configparser.ConfigParser): The loaded configuration object.
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

def get_backup_directories(config):
    
    """
    Retrieves a list of backup directories from the configuration settings.

    Parameters:
    - config (ConfigParser): A ConfigParser object containing the configuration data.

    Returns:
    - List[str]: A list of backup directory paths as strings.

    This function retrieves the value associated with the 'backup_directories' key
    in the 'backup' section of the configuration file. It then splits this value by commas,
    trims any extra whitespace from each directory path, and returns a list of these paths.
    
    Raises:
    - KeyError: If the 'backup' section or 'backup_directories' key is missing from the configuration.
    - ValueError: If the 'backup_directories' value is empty or improperly formatted.
    """
    try:
        # Get the comma-separated list of backup directories from the 'backup' section
        backup_dirs = config.get('BACKUPS', 'backup_dirs')
        
        # Split the string by commas, strip whitespace from each directory path
        return [dir.strip() for dir in backup_dirs.split(',')]
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        # Log or handle errors related to missing sections or options
        raise KeyError(f"Configuration section or option missing: {e}")
    except ValueError as e:
        # Handle empty or improperly formatted backup_directories value
        raise ValueError(f"Invalid format for 'backup_directories': {e}")