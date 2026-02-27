<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey?style=for-the-badge&logo=linux&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-1.5.0-orange?style=for-the-badge" alt="Version">
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
- [Running as a Startup Service](#running-as-a-startup-service)
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
| **Notifications** | Real-time alerts via Telegram bot and/or email with configurable SMTP and retry |
| **Integrity** | SHA-256 checksum verification on every copied file |
| **Symlink Support** | Symbolic links preserved as links during backup (not dereferenced) |
| **Security** | No plaintext secrets on disk, passwords delivered via in-memory buffers, secure SSH policies |
| **Config Validation** | Fail-fast validation with clear error messages; relative paths auto-resolved to absolute |
| **Startup Service** | Cross-platform service installation (systemd, launchd, Task Scheduler) |
| **MySQL Backup** | Database dump with SFTP transfer to remote servers (separate module) |

---

## Architecture

```
                    ┌─────────────┐
                    │   main.py   │  ← CLI entry point & scheduler
                    └──────┬──────┘
                           │
              ┌────────────┼───────────┐
              │            │           │
        ┌─────┴─────┐  ┌───┴───┐ ┌─────┴──────┐
        │  src/sync │  │ src/  │ │ src/       │
        │  (backup  │  │config │ │compression │
        │  engine)  │  │       │ │            │
        └─────┬─────┘  └───────┘ └─────┬──────┘
              │                        │
     ┌────────┼─────────┐         ┌────┴────┐
     │        │         │         │ keyring │
  ┌──┴──┐  ┌──┴───┐ ┌───┴───┐     │ (secure │
  │Local│  │ SFTP │ │Verify │     │ storage)│
  │Copy │  │Upload│ │SHA256 │     └─────────┘
  └─────┘  └──────┘ └───────┘
              │
    ┌─────────┼─────────┐
    │         │         │
┌───┴───┐  ┌──┴──┐ ┌────┴────┐
│  Tg   │  │Email│ │  Logger │
│  Bot  │  │     │ │(rotating│
│       │  │     │ │  files) │
└───────┘  └─────┘ └─────────┘
```

---

## Project Structure

```
backup_handler/
├── main.py                          # Entry point, CLI handling, scheduler
├── requirements.txt                 # Python dependencies
├── .gitignore
│
├── src/
│   ├── argparse_setup.py            # CLI argument parsing and validation
│   ├── backup.py                    # File copy with checksum verification
│   ├── compression.py               # ZIP compression, password-protected archives
│   ├── config.py                    # INI config loader, validator, normalize_none()
│   ├── logger.py                    # Rotating file + console logger (AppLogger)
│   ├── sync.py                      # Local sync, SFTP upload, backup operations
│   ├── utils.py                     # Checksums, OTP, timestamps, validation
│   └── test.py                      # Email integration test
│
├── bot/
│   └── BotHandler.py                # Telegram bot (notifications, documents, polling)
│
├── email_nots/
│   └── email.py                     # SMTP email with attachments
│
├── db_backup/
│   └── mysql_backup.py              # MySQL dump + SFTP transfer
│
├── banner/
│   └── banner_show.py               # CLI banner display
│
├── config/
│   ├── config.ini.example           # Main app config template
│   ├── bot_config.ini.example       # Telegram bot config template
│   ├── email_config.ini.example     # Email SMTP config template
│   └── db_config.ini.example        # MySQL backup config template
│
├── scripts/
│   ├── setup.sh                     # Setup helper (venv, deps, config copies)
│   ├── install_service.sh           # Auto-detect OS and install startup service
│   ├── backup-handler.service       # systemd unit file (Linux)
│   ├── com.backup-handler.plist     # launchd plist (macOS)
│   └── install_windows_task.ps1     # Windows Task Scheduler registration
│
└── Logs/                            # Log output directory (auto-created)
```

---

## Requirements

- **Python** 3.8+
- **OS**: Linux, macOS, or Windows

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

---

## Installation

```bash
# Clone the repository
git clone https://github.com/SP1R4/BackupHandler.git
cd BackupHandler

# Run the setup script (creates venv, installs deps, copies config templates)
bash scripts/setup.sh

# Or manually:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration Setup

Copy the example config files and fill in your values:

```bash
cp config/config.ini.example config/config.ini
cp config/bot_config.ini.example config/bot_config.ini       # Only if using Telegram
cp config/email_config.ini.example config/email_config.ini   # Only if using email
cp config/db_config.ini.example config/db_config.ini         # Only if using MySQL backup
```

> **Important:** The real `.ini` files are gitignored to prevent accidental secret exposure.

---

## Configuration

### `config/config.ini` — Main Application Config

| Section | Field | Required | Description |
|---------|-------|----------|-------------|
| `[DEFAULT]` | `source_dir` | **Yes** | Absolute path to the directory to back up |
| `[DEFAULT]` | `mode` | **Yes** | Backup mode: `full`, `incremental`, or `differential` |
| `[DEFAULT]` | `compress_type` | No | Compression: `none`, `zip`, or `zip_pw` (default: `none`) |
| `[BACKUPS]` | `backup_dirs` | **Yes** | Comma-separated backup destination directories |
| `[SSH]` | `ssh_servers` | When ssh=True | Comma-separated SSH server hostnames |
| `[SSH]` | `username` | When ssh=True | SSH username |
| `[SSH]` | `password` | When ssh=True | SSH password |
| `[SCHEDULE]` | `times` | For `--scheduled` | Comma-separated times in HH:MM format |
| `[SCHEDULE]` | `interval_minutes` | No | Scheduler check interval in minutes (default: 60) |
| `[MODES]` | `local` | **Yes** | Enable local backups: `True` / `False` |
| `[MODES]` | `ssh` | **Yes** | Enable SSH backups: `True` / `False` |
| `[NOTIFICATIONS]` | `bot` | No | Enable Telegram notifications: `True` / `False` |
| `[NOTIFICATIONS]` | `receiver_emails` | No | Comma-separated emails, or `None` to disable |

### `config/bot_config.ini` — Telegram Bot

| Section | Field | Required | Description |
|---------|-------|----------|-------------|
| `[TELEGRAM]` | `api_token` | **Yes** | Bot API token from @BotFather |
| `[USERS]` | `interacted_users` | **Yes** | Comma-separated Telegram user/chat IDs |

### `config/email_config.ini` — Email (SMTP)

| Section | Field | Required | Description |
|---------|-------|----------|-------------|
| `[EMAIL]` | `sender_email` | **Yes** | Sender email address |
| `[EMAIL]` | `app_password` | **Yes** | App-specific password (not your regular password) |
| `[EMAIL]` | `smtp_host` | No | SMTP server hostname (default: `smtp.gmail.com`) |
| `[EMAIL]` | `smtp_port` | No | SMTP port (default: `465`) |

### `config/db_config.ini` — MySQL Backup

| Section | Field | Required | Description |
|---------|-------|----------|-------------|
| `[mysql]` | `user` | **Yes** | MySQL username |
| `[mysql]` | `password` | **Yes** | MySQL password |
| `[mysql]` | `database` | **Yes** | Database name |
| `[backup]` | `local_backup_dir` | **Yes** | Local directory for dumps |
| `[ssh]` | `host`, `port`, `user`, `password`, `remote_backup_dir` | **Yes** | Remote transfer settings |

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

# Dry run — preview what would happen without copying anything
python main.py --dry-run --operation-modes local ssh --backup-mode full \
  --source-dir /data --backup-dirs /backups --ssh-servers server1.com

# Show current configuration
python main.py --show-setup
```

### All CLI Options

| Option | Description |
|--------|-------------|
| `--config PATH` | Path to configuration file (default: `config/config.ini`) |
| `--operation-modes {local,ssh}` | Backup targets — can specify both |
| `--source-dir PATH` | Source directory to back up |
| `--backup-dirs PATH [PATH ...]` | Local backup destinations |
| `--ssh-servers HOST [HOST ...]` | Remote SSH servers |
| `--backup-mode {full,incremental,differential}` | Backup strategy |
| `--compress {zip,zip_pw}` | Enable compression (`zip_pw` = password-protected) |
| `--scheduled` | Run in scheduled mode using config times |
| `--notifications` | Enable Telegram & email notifications |
| `--receiver EMAIL [EMAIL ...]` | Email recipients for notifications |
| `--dry-run` | Preview what would be done without copying or syncing files |
| `--show-setup` | Display current configuration and exit |
| `--version` | Show program version and exit |

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

## Running as a Startup Service

Backup Handler can run as a system service so backups start automatically on boot.

### Linux (systemd)

```bash
# Automatic installation
bash scripts/install_service.sh

# Or manually:
# 1. Edit scripts/backup-handler.service — replace __PROJECT_DIR__ and __USER__
# 2. Copy and enable:
sudo cp scripts/backup-handler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable backup-handler
sudo systemctl start backup-handler

# Check status
sudo systemctl status backup-handler
sudo journalctl -u backup-handler -f
```

### macOS (launchd)

```bash
# Automatic installation
bash scripts/install_service.sh

# Or manually:
# 1. Edit scripts/com.backup-handler.plist — replace __PROJECT_DIR__
# 2. Copy and load:
cp scripts/com.backup-handler.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.backup-handler.plist

# Check status
launchctl list | grep backup-handler
```

### Windows (Task Scheduler)

```powershell
# Run in PowerShell as Administrator
.\scripts\install_windows_task.ps1

# Check status
Get-ScheduledTask -TaskName "BackupHandler"
Start-ScheduledTask -TaskName "BackupHandler"
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
| **Config file permissions** | Setup script sets `chmod 600` on all `.ini` files containing credentials |
| **Config validation** | Fail-fast on startup with clear error messages; no silent fallbacks to None |
| **Path resolution** | Relative paths in config automatically resolved to absolute to prevent working-directory issues |
| **Instance locking** | PID lock file prevents duplicate scheduled instances from running simultaneously |
| **Fault tolerance** | Per-file error handling in incremental/differential backups — single file failures don't stop the job |

---

## Logging

Logs are written to `Logs/application.log` with automatic rotation:
- **Max file size:** 5 MB per log file
- **Backup count:** 5 rotated log files retained
- **Console output:** All log messages also printed to stdout
- **Log levels:** Configurable (default: `DEBUG`)

```
2026-02-22 12:00:01 - INFO - Configuration loaded successfully from config/config.ini
2026-02-22 12:00:01 - INFO - Performing full backup from /data
2026-02-22 12:00:01 - INFO - Successfully backed up /data/file.txt to /backups/file.txt
2026-02-22 12:00:02 - INFO - Compressed directory '/data' to 'backup_20260222_120002.zip'
2026-02-22 12:00:03 - INFO - Notification sent to user: Backup completed
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Config error: 'source_dir' is not set` | Set `source_dir` in `[DEFAULT]` section of `config/config.ini` |
| `Config error: 'ssh_servers' is not set` | Set SSH fields in `[SSH]` section, or set `ssh = False` in `[MODES]` |
| `Config error: Invalid time format` | Use HH:MM 24-hour format (e.g., `03:00`, `14:30`) |
| `Error: config/bot_config.ini not found` | Copy `config/bot_config.ini.example` to `config/bot_config.ini` and fill in your bot token |
| `Error: Missing key in bot_config.ini` | Ensure `[TELEGRAM] api_token` and `[USERS] interacted_users` are set |
| `Config error: 'sender_email' is not set` | Fill in `sender_email` and `app_password` in `config/email_config.ini` |
| `ModuleNotFoundError` | Ensure venv is activated and `pip install -r requirements.txt` was run |
| Telegram notifications not sending | Verify bot token and chat ID. Send a message to the bot first to register |
| SSH connection refused | Check server address, port, and credentials. Verify the remote host key |
| Scheduled backup not triggering | Ensure schedule times in config match HH:MM format and the process is running |
| `Config error: 'compress_type' must be one of...` | Use `none`, `zip`, or `zip_pw` in `[DEFAULT] compress_type` |
| `Config error: 'mode' must be full, incremental...` | Use `full`, `incremental`, or `differential` in `[DEFAULT] mode` |
| `--scheduled and --dry-run cannot be used together` | Dry-run is for one-off previews; remove `--dry-run` when running in scheduled mode |
| `Another backup-handler instance is already running` | A scheduled instance is already active. Kill it first, or remove `.backup-handler.lock` if stale |
| Compression fails | Ensure `pyminizip` is installed. For `zip_pw`, verify the source directory is not empty |

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
