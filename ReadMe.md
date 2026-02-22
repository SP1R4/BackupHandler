<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-linux-lightgrey?style=for-the-badge&logo=linux&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-1.1.0-orange?style=for-the-badge" alt="Version">
</p>

<h1 align="center">Backup Handler</h1>

<p align="center">
  A robust, security-hardened backup solution supporting local and remote (SSH/SFTP) backups<br>
  with scheduling, compression, Telegram notifications, and email alerts.
</p>

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Backup Modes](#backup-modes)
- [Notifications](#notifications)
- [Security](#security)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

---

## Overview

Backup Handler is a command-line backup management tool built in Python. It provides automated, verifiable backups with support for multiple backup strategies, remote server synchronization via SFTP, optional password-protected compression, and real-time notifications through Telegram and email.

Designed for sysadmins and power users who need a reliable, scriptable backup solution without the overhead of enterprise tooling.

---

## Features

| Category | Details |
|----------|---------|
| **Backup Modes** | Full, incremental, and differential backups with SHA-256 integrity verification |
| **Local Backups** | Copy files to one or more local backup directories with progress tracking |
| **Remote Backups** | Sync to multiple SSH servers concurrently via SFTP (no rsync dependency) |
| **Compression** | ZIP compression with optional password protection (AES encryption via pyminizip) |
| **Scheduling** | Built-in scheduler with configurable times and tolerance-based matching |
| **Notifications** | Real-time alerts via Telegram bot and/or email with configurable SMTP |
| **Integrity** | SHA-256 checksum verification on every copied file |
| **Security** | No plaintext secrets on disk, passwords delivered via in-memory buffers, secure SSH policies |
| **MySQL Backup** | Database dump with SFTP transfer to remote servers (separate module) |

---

## Architecture

```
                    ┌─────────────┐
                    │   main.py   │  ← CLI entry point & scheduler
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴───┐ ┌─────┴──────┐
        │  src/sync  │ │ src/  │ │ src/       │
        │  (backup   │ │config │ │compression │
        │  engine)   │ │       │ │            │
        └─────┬──────┘ └───────┘ └─────┬──────┘
              │                        │
     ┌────────┼────────┐          ┌────┴────┐
     │        │        │          │ keyring  │
  ┌──┴──┐ ┌──┴──┐ ┌───┴───┐     │ (secure  │
  │Local│ │SFTP │ │Verify │     │ storage) │
  │Copy │ │Upload│ │(SHA256)│    └─────────┘
  └─────┘ └─────┘ └───────┘
              │
    ┌─────────┼─────────┐
    │         │         │
┌───┴───┐ ┌──┴──┐ ┌────┴────┐
│Telegram│ │Email│ │  Logger │
│  Bot   │ │     │ │(rotating│
│        │ │     │ │  files) │
└────────┘ └─────┘ └─────────┘
```

---

## Project Structure

```
backup_handler/
├── main.py                     # Entry point, CLI handling, scheduler
├── requirements.txt            # Python dependencies
├── .gitignore
│
├── src/
│   ├── argparse_setup.py       # CLI argument parsing and validation
│   ├── backup.py               # File copy with checksum verification
│   ├── compression.py          # ZIP compression, password-protected archives
│   ├── config.py               # INI configuration loader and validator
│   ├── logger.py               # Rotating file + console logger (AppLogger)
│   ├── scheduler.py            # Interval-based backup scheduling
│   ├── sync.py                 # Local sync, SFTP upload, backup operations
│   ├── utils.py                # Checksums, OTP, timestamps, validation
│   └── test.py                 # Email integration test
│
├── bot/
│   └── BotHandler.py           # Telegram bot (notifications, documents, polling)
│
├── email_nots/
│   └── email.py                # SMTP email with attachments
│
├── db_backup/
│   └── mysql_backup.py         # MySQL dump + SFTP transfer
│
├── banner/
│   └── banner_show.py          # CLI banner display
│
├── config/
│   ├── config.ini.example      # Main app config template
│   ├── bot_config.ini.example  # Telegram bot config template
│   ├── email_config.ini.example# Email SMTP config template
│   └── db_config.ini.example   # MySQL backup config template
│
├── scripts/
│   └── setup.sh                # Setup helper script
│
└── Logs/                       # Log output directory (auto-created)
```

---

## Requirements

- **Python** 3.8+
- **OS**: Linux (tested on Ubuntu/Debian)

### Python Dependencies

All dependencies are pinned in `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `paramiko` | SSH/SFTP connections |
| `tqdm` | Progress bars |
| `pyminizip` | Password-protected ZIP |
| `keyring` | Secure credential storage |
| `pyTelegramBotAPI` | Telegram notifications |
| `colorama` | Colored terminal output |
| `retrying` | Retry logic for SSH |
| `schedule` | Task scheduling |

---

## Installation

```bash
# Clone the repository
git clone https://github.com/SP1R4/BackupHandler.git
cd BackupHandler

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration Setup

Copy the example config files and fill in your values:

```bash
cp config/config.ini.example config/config.ini
cp config/bot_config.ini.example config/bot_config.ini
cp config/email_config.ini.example config/email_config.ini
cp config/db_config.ini.example config/db_config.ini
```

> **Important:** The real `.ini` files are gitignored to prevent accidental secret exposure.

---

## Configuration

### `config/config.ini` — Main Application Config

```ini
[DEFAULT]
source_dir = /path/to/source       # Directory to back up
mode = full                         # Backup mode: full | incremental | differential
compress_type = zip                 # Compression: none | zip | zip_pw

[BACKUPS]
backup_dirs = /backup1, /backup2   # Comma-separated backup destinations

[SSH]
ssh_servers = server1.com, server2.com
username = ssh_user
password = ssh_password

[SCHEDULE]
times = 03:00, 12:00               # HH:MM format, comma-separated
interval_minutes = 60               # Check interval for scheduler

[MODES]
local = True                        # Enable local backups
ssh = False                         # Enable SSH/SFTP backups

[NOTIFICATIONS]
bot = True                          # Enable Telegram notifications
receiver_emails = None              # Comma-separated emails, or None
```

### `config/bot_config.ini` — Telegram Bot

```ini
[TELEGRAM]
api_token = YOUR_BOT_TOKEN

[USERS]
interacted_users = YOUR_CHAT_ID
```

### `config/email_config.ini` — Email (SMTP)

```ini
[EMAIL]
sender_email = you@gmail.com
app_password = YOUR_APP_PASSWORD
smtp_host = smtp.gmail.com          # Configurable SMTP host
smtp_port = 465                     # Configurable SMTP port
```

---

## Usage

```bash
python main.py [OPTIONS]
```

### Quick Examples

```bash
# Full local backup
python main.py --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups/daily

# Incremental backup (only changed files since last backup)
python main.py --operation-modes local --backup-mode incremental \
  --source-dir /data --backup-dirs /backups/incremental

# Full backup with password-protected ZIP compression
python main.py --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups --compress zip_pw

# Remote SFTP backup to multiple servers
python main.py --operation-modes ssh --backup-mode full \
  --source-dir /data --ssh-servers server1.com server2.com

# Both local and SSH with Telegram + email notifications
python main.py --operation-modes local ssh --backup-mode full \
  --source-dir /data --backup-dirs /backups \
  --ssh-servers server1.com \
  --notifications --receiver admin@example.com

# Scheduled mode (reads times from config, runs continuously)
python main.py --scheduled

# Show current configuration
python main.py --show-setup
```

### All CLI Options

| Option | Description |
|--------|-------------|
| `--config PATH` | Path to configuration file (default: `config/config.ini`) |
| `--operation-modes {local,ssh}` | Backup targets — can specify both |
| `--source-dir PATH [PATH ...]` | Source directories to back up |
| `--backup-dirs PATH [PATH ...]` | Local backup destinations |
| `--ssh-servers HOST [HOST ...]` | Remote SSH servers |
| `--backup-mode {full,incremental,differential}` | Backup strategy |
| `--compress {zip,zip_pw}` | Enable compression (`zip_pw` = password-protected) |
| `--scheduled` | Run in scheduled mode using config times |
| `--notifications` | Enable Telegram & email notifications |
| `--receiver EMAIL [EMAIL ...]` | Email recipients for notifications |
| `--show-setup` | Display current configuration and exit |

---

## Backup Modes

### Full Backup
Creates a complete copy of the source directory. All files are copied and verified with SHA-256 checksums. Use this for initial backups or periodic complete snapshots.

### Incremental Backup
Only copies files that have been **modified or created since the last backup** (any type). This is the fastest and most storage-efficient option for frequent backups.

### Differential Backup
Copies all files that have changed **since the last full backup**. Provides a middle ground — faster restores than incremental (only need the last full + last differential), but uses more storage.

```
Full ─────────────────────────────────────────────►
       │                    │                │
       ▼                    ▼                ▼
   Differential         Differential     Differential
   (changes since       (changes since   (cumulative)
    last full)           last full)

       │         │         │
       ▼         ▼         ▼
   Incremental Incremental Incremental
   (changes    (changes    (changes
    since last  since last  since last
    backup)     backup)     backup)
```

---

## Notifications

### Telegram Bot
1. Create a bot via [BotFather](https://t.me/BotFather) and get your API token
2. Send any message to your bot to register your chat ID
3. Configure `config/bot_config.ini` with your token and user ID

The bot sends notifications for:
- Backup start/completion/failure events
- Password-protected archive passwords (delivered as in-memory documents, never written to disk)

### Email
- Configurable SMTP server (defaults to Gmail SMTP on port 465)
- Supports file attachments
- Sends backup status updates and archive passwords

---

## Security

This project follows security best practices:

| Measure | Details |
|---------|---------|
| **No plaintext secrets on disk** | Passwords delivered via in-memory `BytesIO` buffers, temp files cleaned up immediately |
| **Secure credential storage** | Archive passwords stored in OS keyring (via `keyring` library) |
| **SSH host key policy** | Uses `paramiko.WarningPolicy()` instead of auto-accepting unknown hosts |
| **MySQL password handling** | Passed via `MYSQL_PWD` environment variable, never on command line |
| **Config file protection** | All `.ini` files with secrets are gitignored; `.example` templates provided |
| **No OTP file leakage** | Generated OTPs returned in memory only, never written to `otp.json` |

---

## Logging

Logs are written to `Logs/application.log` with automatic rotation:
- **Max file size:** 5 MB per log file
- **Backup count:** 5 rotated log files retained
- **Console output:** All log messages also printed to stdout
- **Log levels:** Configurable (default: `DEBUG`)

```
2026-02-22 12:00:01 - INFO - Performing full backup from /data
2026-02-22 12:00:01 - INFO - Successfully backed up /data/file.txt to /backups/file.txt
2026-02-22 12:00:02 - INFO - Compressed directory '/data' to 'backup_20260222_120002.zip'
2026-02-22 12:00:03 - INFO - Notification sent to user: Backup completed
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Ensure venv is activated and `pip install -r requirements.txt` was run |
| Telegram notifications not sending | Verify bot token and chat ID in `config/bot_config.ini`. Send a message to the bot first to register |
| SSH connection refused | Check server address, port, and credentials. Verify the remote host key is in `~/.ssh/known_hosts` |
| `FileNotFoundError` for config | Copy `.example` files to `.ini` — see [Installation](#installation) |
| Scheduled backup not triggering | Ensure schedule times in config match `HH:MM` format and the process is running continuously |
| Compression fails | Ensure `pyminizip` is installed. For password-protected ZIPs, verify the source directory is not empty |

---

## Roadmap

- [ ] Cloud storage integration (AWS S3, Google Drive)
- [ ] Backup encryption at rest (beyond ZIP password protection)
- [ ] Web dashboard for monitoring backup status
- [ ] Cron expression support for advanced scheduling
- [ ] Slack/Discord notification channels
- [ ] Backup retention policies (auto-cleanup of old backups)

---

## Contributing

Contributions are welcome. Please open an issue first to discuss proposed changes.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  Built by <a href="https://github.com/SP1R4">SP1R4</a>
</p>
