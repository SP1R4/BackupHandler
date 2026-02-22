# Backup Handler

![Build Status](https://img.shields.io/badge/build-passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Version](https://img.shields.io/badge/version-1.0.0-orange)

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Command-Line Options](#command-line-options)
- [Logging](#logging)
- [Notifications](#notifications)
- [Backup Modes Explained](#backup-modes-explained)
- [Troubleshooting](#troubleshooting)
- [Changelog](#changelog)
- [Future Work](#future-work)
- [Acknowledgments](#acknowledgments)
- [Contact](#contact)

## Overview
The Backup Handler is a Python application designed to manage backup operations, including local and remote (SSH) backups. It supports various backup modes (full, incremental, differential) and provides notification features via Telegram and email. This tool is ideal for users who need a reliable and automated way to back up their data.

## Features
- **Backup Modes**: Supports full, incremental, and differential backups to optimize storage and time.
- **Local and Remote Backups**: Can back up files to local directories or remote SSH servers, providing flexibility in backup strategies.
- **Scheduling**: Schedule backups to run at specified intervals, ensuring regular data protection without manual intervention.
- **Notifications**: Sends notifications about backup operations via Telegram and email, keeping users informed of the status of their backups.
- **Configuration Management**: Loads configuration settings from an INI file for easy customization.
- **Logging**: Utilizes an asynchronous logger for tracking operations, making it easier to debug and monitor the application.

## Requirements
- Python 3.x
- Required Python packages:
  - `colorama`
  - `telebot`
  - `paramiko`
  - `tqdm`
  - `retrying`
  - `pyminizip`
  - `keyring`
  - `schedule`


## Installation
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```
2. Install the required packages:
   ```bash
   pip3 install -r requirements.txt
   ```

3. Configure the application:
   - Edit the `config/config.ini` file to set your source directory, backup directories, SSH server details, and notification settings.

## Configuration
The application requires a configuration file (`config/config.ini`) with the following sections:

### DEFAULT
- `source_dir`: Path to the source directory to back up.
- `mode`: Backup mode (full, incremental, differential).
- `dry_run`: Boolean to simulate the backup process without copying files.
- `compress_type`: Type of compression (none, zip, zip_pw).

### BACKUPS
- `backup_dirs`: Comma-separated list of backup directories.

### SSH
- `ssh_servers`: Comma-separated list of SSH server addresses.
- `username`: SSH username.
- `password`: SSH password.

### SCHEDULE
- `times`: Comma-separated list of scheduled times for backups.
- `interval_minutes`: Interval in minutes for scheduled backups.

### NOTIFICATIONS
- `bot`: Boolean to enable Telegram bot notifications.
- `receiver_emails`: Comma-separated list of email addresses for notifications.

## Usage
To run the application, execute the following command:
```bash
python3 main.py [options]
```

### Example Usage Scenarios

#### 1. Full Local Backup
To perform a full backup of a local directory:
```bash
python3 main.py --operation-modes local --backup-mode full --source-dir /path/to/source --backup-dirs /path/to/backup
```

#### 2. Incremental Backup
To perform an incremental backup (only new or modified files since the last backup):
```bash
python3 main.py --operation-modes local --backup-mode incremental --source-dir /path/to/source --backup-dirs /path/to/backup
```

#### 3. Differential Backup
To perform a differential backup (files changed since the last full backup):
```bash
python3 main.py --operation-modes local --backup-mode differential --source-dir /path/to/source --backup-dirs /path/to/backup
```

#### 4. Remote SSH Backup
To back up files to a remote SSH server:
```bash
python3 main.py --operation-modes ssh --backup-mode full --source-dir /path/to/source --backup-dirs /path/to/backup --ssh-servers server.example.com --username your_username --password your_password
```

#### 5. Scheduled Backup
To execute a backup as a scheduled task:
```bash
python3 main.py --scheduled --operation-modes local --backup-mode full --source-dir /path/to/source --backup-dirs /path/to/backup
```

#### 6. Dry Run
To simulate a backup without making any changes:
```bash
python3 main.py --operation-modes local --backup-mode full --source-dir /path/to/source --backup-dirs /path/to/backup --dry-run
```

#### 7. Enable Notifications
To enable notifications via Telegram and email:
```bash
python3 main.py --operation-modes local --backup-mode full --source-dir /path/to/source --backup-dirs /path/to/backup --notifications --receiver your_email@example.com
```

## Command-Line Options
- `--config`: Path to the configuration file (default: `config/config.ini`).
- `--operation-modes`: Specify operation modes (local, ssh).
- `--source-dir`: Override the source directory from the configuration.
- `--backup-dirs`: Override the backup directories.
- `--ssh-servers`: Override the SSH servers for remote backups.
- `--backup-mode`: Specify the type of backup (full, incremental, differential).
- `--dry-run`: Simulate the backup process without copying files.
- `--show-setup`: Display the current backup configuration and settings.
- `--compress`: Compress the source directory.
- `--scheduled`: Execute the backup as a scheduled task.
- `--notifications`: Enable notifications for backup operations.
- `--receiver`: List of email addresses to receive notifications.

## Logging
Logs are stored in `Logs/application.log`. The logging level can be adjusted in the configuration. The log file contains detailed information about backup operations, errors, and notifications sent.

## Notifications
The application can send notifications via Telegram and email. To enable notifications:
1. Set the `bot` option to `True` in the configuration file.
2. Provide a valid Telegram bot API token and user ID in `config/bot_config.ini`.
3. Specify email addresses in the `receiver_emails` field.

## Backup Modes Explained
- **Full Backup**: A complete copy of the source directory. Recommended for initial backups.
- **Incremental Backup**: Only backs up files that have changed since the last backup. Saves time and storage.
- **Differential Backup**: Backs up files that have changed since the last full backup. Provides a balance between full and incremental backups.

## Troubleshooting
- **Issue**: Application fails to start.
  - **Solution**: Ensure all required packages are installed and the configuration file is correctly set up.
  
- **Issue**: Notifications are not sent.
  - **Solution**: Check the Telegram bot configuration and ensure the bot is running.

## Future Work
- **Cloud Backup Integration**: Implement support for backing up to popular cloud storage services (e.g., AWS S3, Google Drive).
- **Multi-Threaded Backups**: Improve performance by allowing multiple backup operations to run concurrently.
- **Backup Verification**: Add functionality to verify the integrity of backups after completion.
- **Advanced Scheduling Options**: Introduce more flexible scheduling options, including cron-like expressions.
- **Backup Encryption**: Implement encryption options for sensitive data during backup.

## Changelog
- **1.0.0**: Initial release with full, incremental, and differential backup support.

## Future Work
- Implement additional backup modes (e.g., cloud storage).
- Enhance the user interface for configuration management.
- Add support for more notification channels (e.g., Slack, SMS).

## Acknowledgments
- Thanks to the contributors and the open-source community for their support and resources.
- Special thanks to the maintainers of the libraries used in this project.

## Contact
For questions or support, please contact [Your Name] at [Your Email].
