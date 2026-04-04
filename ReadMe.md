<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey?style=for-the-badge&logo=linux&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/version-2.4.0-orange?style=for-the-badge" alt="Version">
</p>

<h1 align="center">Backup Handler</h1>

<p align="center">
  A robust, security-hardened backup solution supporting local, SSH/SFTP, S3, and MySQL backups<br>
  with Tailscale VPN integration, encryption at rest, deduplication, scheduling, compression, and multi-channel notifications.
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
- [Encryption at Rest](#encryption-at-rest)
- [Deduplication](#deduplication)
- [Tailscale VPN Integration](#tailscale-vpn-integration)
- [Pre-flight Checks](#pre-flight-checks)
- [Backup Verification](#backup-verification)
- [Restore](#restore)
- [Retention Policies](#retention-policies)
- [Running as a Startup Service](#running-as-a-startup-service)
- [Notifications](#notifications)
- [Security](#security)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)

---

## Overview

Backup Handler is a command-line backup management tool built in Python. It provides automated, verifiable backups with support for multiple backup strategies, remote server synchronization via SFTP, S3 cloud storage, MySQL database dumps, AES-256-GCM encryption at rest, file-level deduplication, optional password-protected compression, and real-time notifications through Telegram, email (SMTP), webhooks, and CLI receivers.

Designed for sysadmins and power users who need a reliable, scriptable backup solution without the overhead of enterprise tooling.

---

## Features

| Category | Details |
|----------|---------|
| **Backup Modes** | Full, incremental, and differential backups with SHA-256 integrity verification |
| **Local Backups** | Copy files to one or more local backup directories with progress tracking and parallel copies |
| **Remote Backups (SSH)** | Sync to multiple SSH servers concurrently via SFTP with configurable bandwidth throttling |
| **Tailscale VPN** | Automatic Tailscale VPN connection with pre-auth keys for secure SSH backups over private tailnets |
| **Cloud Backups (S3)** | Upload backups to AWS S3 with bandwidth throttling, multipart uploads, and concurrency control |
| **Database Backups** | MySQL dumps via `mysqldump` with `--single-transaction` support and binary log position tracking |
| **Encryption at Rest** | AES-256-GCM encryption with parallel processing via ThreadPoolExecutor and progress bars |
| **Deduplication** | File-level deduplication using hardlinks within and across backup directories with progress bars |
| **Compression** | ZIP compression with optional password protection (AES encryption via pyminizip) |
| **Backup Verification** | Verify backup integrity against manifest SHA-256 checksums with encrypted file support |
| **Restore** | Restore from local directories, ZIP archives, SSH remotes, or S3 with point-in-time and dry-run support |
| **Retention Policies** | Auto-cleanup by age (days) and count (N most recent), configurable per run |
| **Scheduling** | Built-in scheduler with configurable times and tolerance-based matching |
| **Notifications** | Telegram bot, SMTP email (HTML + plain text), webhooks (Slack/Discord/Teams), and CLI receivers |
| **Config Profiles** | Load named profiles (`--profile staging` resolves to `config/config.staging.ini`) |
| **Config Schema Versioning** | `[META] schema_version` warns when config file is outdated after upgrades |
| **Env Var Secrets** | Config values support `${ENV_VAR}` syntax for secrets (passwords, keys, passphrases) |
| **Exclude Patterns** | Glob-based exclude patterns via config or `--exclude` flag |
| **Pre/Post Hooks** | Shell commands before/after backup (pre-hook failure aborts the backup) |
| **Manifests** | JSON manifests tracking every copied/skipped/failed file with SHA-256 checksums per backup run |
| **Dry Run** | Preview all operations without copying, syncing, or modifying anything (including restore) |
| **Status Dashboard** | View last backup times, directory sizes, and manifest summaries |
| **Symlink Support** | Symbolic links preserved as links during backup (not dereferenced) |
| **Pre-flight Checks** | Verifies backup destination mount points are accessible before starting, with notifications on failure |
| **Instance Locking** | PID lock file prevents duplicate scheduled instances |
| **Startup Service** | Cross-platform service installation (systemd, launchd, Task Scheduler) |
| **Integrity** | SHA-256 checksum verification on every copied file, recorded in manifest for later validation |

---

## Architecture

```
                    +==============+
                    |   main.py    |  <- CLI entry point & scheduler
                    +======+=======+
                           |
                    +------+-------+
                    | Pre-flight   |  <- Mount & directory checks
                    | Checks       |
                    +------+-------+
                           |
        +----------+-------+--------+----------+
        |          |       |        |          |
  +-----+-----+ +--+--+ +--+---+ +--+--+ +----+----+
  | src/sync  | | src/ | | src/ | | src/ | |  src/   |
  | (backup   | |s3_   | |db_   | |enc- | | config  |
  |  engine)  | |sync  | |sync  | |rypt | |         |
  +-----+-----+ +--+--+ +--+---+ +--+--+ +----+----+
        |          |       |        |          |
   +----+----+     |       |        |     +----+----+
   |    |    |     |       |        |     |         |
+--+-++--+--+|  +--+--+ +--+---+ +--+--+ |  +------+--+
|Copy||SFTP ||  | S3  | |MySQL | |AES- | |  |Retention|
|    ||     ||  |     | |Dump  | |256  | |  |& Dedup  |
+----++--+--+|  +-----+ +------+ |GCM  | |  +---------+
         |   |                   +-----+ |
    +----+---+                           |
    |Tailscale|                          |
    |(VPN)    |                          |
    +----+----+                          |
         |                               |
    +--------+--------+           +-----+-----+
    |        |        |           |           |
+---+---+ +--+--+ +---+----+  +--+---+ +-----+--+
|  Tg   | |SMTP | | Logger |  |Verify| |Restore |
|  Bot  | |Email| |(rotate)|  |      | |(local/ |
|       | |     | |        |  |      | |SSH/S3) |
+---+---+ +-----+ +--------+  +------+ +--------+
    |
+---+----+
|Webhook |
|(Slack/ |
|Discord)|
+--------+
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
│   ├── config.py                    # INI config loader, env var resolution, schema versioning
│   ├── db_sync.py                   # MySQL database dump with --single-transaction
│   ├── dedup.py                     # File-level deduplication via hardlinks with progress bars
│   ├── email_notify.py              # SMTP email notifications (HTML + plain text) with retry
│   ├── encryption.py                # AES-256-GCM encryption/decryption with parallel workers
│   ├── logger.py                    # Rotating file + console logger (AppLogger)
│   ├── manifest.py                  # Backup manifest creation with SHA-256 checksums
│   ├── restore.py                   # Restore from local, ZIP, SSH, S3 with dry-run support
│   ├── retention.py                 # Age-based and count-based backup cleanup
│   ├── s3_sync.py                   # AWS S3 upload with bandwidth throttling and multipart
│   ├── sync.py                      # Local sync, SFTP upload, backup operations
│   ├── tailscale.py                 # Tailscale VPN management (up/down/status/resolve)
│   ├── utils.py                     # Checksums, OTP, timestamps, validation
│   ├── verify.py                    # Backup integrity verification with checksum validation
│   └── webhook_notify.py            # Webhook notifications (Slack, Discord, Teams, custom)
│
├── bot/
│   └── BotHandler.py                # Telegram bot (notifications, documents, polling)
│
├── email_nots/
│   └── email.py                     # Legacy SMTP email with attachments
│
├── db_backup/
│   └── mysql_backup.py              # Standalone MySQL dump + SFTP transfer
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
├── tests/
│   ├── __init__.py
│   └── test_features.py             # 46 unit tests for all major features
│
└── Logs/                            # Log output directory (auto-created)
```

---

## Requirements

- **Python** 3.8+
- **OS**: Linux, macOS, or Windows
- **mysqldump** (only if using `--operation-modes db`)

### Python Dependencies

All dependencies are pinned in `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `paramiko` | SSH/SFTP connections |
| `boto3` | AWS S3 uploads and downloads |
| `cryptography` | AES-256-GCM encryption at rest |
| `tqdm` | Progress bars for encryption, dedup, and file sync |
| `requests` | Webhook notifications |
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

Copy the example config and fill in your values:

```bash
cp config/config.ini.example config/config.ini
cp config/bot_config.ini.example config/bot_config.ini       # Only if using Telegram
```

> **Important:** The real `.ini` files are gitignored to prevent accidental secret exposure.

---

## Configuration

### `config/config.ini` — Main Application Config

All configuration is consolidated into a single `config/config.ini` file. Sensitive values support environment variable syntax: `password = ${MY_SECRET}`.

| Section | Field | Required | Description |
|---------|-------|----------|-------------|
| `[META]` | `schema_version` | No | Config schema version (current: `3`). Warns on mismatch after upgrades |
| `[DEFAULT]` | `source_dir` | **Yes** | Absolute path to the directory to back up |
| `[DEFAULT]` | `mode` | **Yes** | Backup mode: `full`, `incremental`, or `differential` |
| `[DEFAULT]` | `compress_type` | No | Compression: `none`, `zip`, or `zip_pw` (default: `none`) |
| `[DEFAULT]` | `exclude_patterns` | No | Comma-separated glob patterns to exclude (e.g., `*.log,*.tmp`) |
| `[DEFAULT]` | `parallel_copies` | No | Number of parallel file copy threads (default: `1`) |
| `[BACKUPS]` | `backup_dirs` | **Yes** | Comma-separated backup destination directories |
| `[SSH]` | `ssh_servers` | When ssh=True | Comma-separated SSH server hostnames |
| `[SSH]` | `username` | When ssh=True | SSH username |
| `[SSH]` | `password` | When ssh=True | SSH password (supports `${SSH_PASSWORD}`) |
| `[SSH]` | `bandwidth_limit` | No | SFTP bandwidth limit in KB/s (`0` = unlimited) |
| `[S3]` | `bucket` | When s3=True | S3 bucket name |
| `[S3]` | `prefix` | No | S3 key prefix (folder path in bucket) |
| `[S3]` | `region` | When s3=True | AWS region |
| `[S3]` | `access_key` | No | AWS access key (supports `${AWS_ACCESS_KEY_ID}`) |
| `[S3]` | `secret_key` | No | AWS secret key (supports `${AWS_SECRET_ACCESS_KEY}`) |
| `[S3]` | `max_bandwidth` | No | Maximum upload bandwidth in KB/s (`0` = unlimited) |
| `[S3]` | `multipart_threshold` | No | Multipart upload threshold in MB (default: `8`) |
| `[S3]` | `max_concurrency` | No | Maximum concurrent upload threads (default: `10`) |
| `[ENCRYPTION]` | `enabled` | No | Enable AES-256-GCM encryption: `True` / `False` |
| `[ENCRYPTION]` | `key_file` | No | Path to 32-byte raw key file (takes priority over passphrase) |
| `[ENCRYPTION]` | `passphrase` | No | Passphrase for PBKDF2 key derivation (supports `${BACKUP_ENCRYPTION_PASSPHRASE}`) |
| `[ENCRYPTION]` | `workers` | No | Number of parallel encryption/decryption threads (default: `1`) |
| `[DATABASE]` | `user` | When db=True | MySQL username |
| `[DATABASE]` | `password` | When db=True | MySQL password (supports `${DB_PASSWORD}`) |
| `[DATABASE]` | `database` | When db=True | Database name |
| `[DATABASE]` | `host` | No | MySQL host (default: `localhost`) |
| `[DATABASE]` | `port` | No | MySQL port (default: `3306`) |
| `[DATABASE]` | `single_transaction` | No | Use `--single-transaction` for InnoDB consistent snapshots (default: `True`) |
| `[DATABASE]` | `binlog_position` | No | Record binary log position in dump for point-in-time recovery (default: `False`) |
| `[SMTP]` | `host` | No | SMTP server hostname |
| `[SMTP]` | `port` | No | SMTP port (default: `587`) |
| `[SMTP]` | `user` | No | SMTP username (supports `${SMTP_USER}`) |
| `[SMTP]` | `password` | No | SMTP password (supports `${SMTP_PASSWORD}`) |
| `[SMTP]` | `from_addr` | No | Sender email address (defaults to SMTP user) |
| `[SMTP]` | `to_addrs` | No | Comma-separated recipient emails |
| `[SMTP]` | `use_tls` | No | Use STARTTLS: `True` / `False` (default: `True`) |
| `[WEBHOOK]` | `url` | No | Webhook URL for notifications (Slack, Discord, Teams, or custom) |
| `[WEBHOOK]` | `auth_header` | No | Authorization header value (supports `${WEBHOOK_AUTH_TOKEN}`) |
| `[DEDUP]` | `enabled` | No | Enable file-level deduplication: `True` / `False` |
| `[TAILSCALE]` | `enabled` | No | Enable Tailscale VPN for SSH backups: `True` / `False` |
| `[TAILSCALE]` | `auth_key` | When enabled | Pre-authentication key (supports `${TAILSCALE_AUTH_KEY}`) |
| `[TAILSCALE]` | `hostname` | No | Override machine hostname on the tailnet |
| `[TAILSCALE]` | `advertise_tags` | No | ACL tags to advertise (e.g. `tag:backup`) |
| `[TAILSCALE]` | `accept_routes` | No | Accept routes from other Tailscale nodes: `True` / `False` |
| `[TAILSCALE]` | `disconnect_after` | No | Disconnect Tailscale after SSH backup completes: `True` / `False` |
| `[SCHEDULE]` | `times` | For `--scheduled` | Comma-separated times in HH:MM format |
| `[SCHEDULE]` | `interval_minutes` | No | Scheduler check interval in minutes (default: `60`) |
| `[MODES]` | `local` | **Yes** | Enable local backups: `True` / `False` |
| `[MODES]` | `ssh` | **Yes** | Enable SSH backups: `True` / `False` |
| `[MODES]` | `s3` | No | Enable S3 cloud backups: `True` / `False` |
| `[MODES]` | `db` | No | Enable MySQL database dumps: `True` / `False` |
| `[HOOKS]` | `pre_backup` | No | Shell command to run before backup (non-zero exit aborts) |
| `[HOOKS]` | `post_backup` | No | Shell command to run after backup |
| `[RETENTION]` | `max_age_days` | No | Remove backups older than N days (`0` = disabled) |
| `[RETENTION]` | `max_count` | No | Keep only N most recent backups per directory (`0` = unlimited) |
| `[NOTIFICATIONS]` | `bot` | No | Enable Telegram notifications: `True` / `False` |
| `[NOTIFICATIONS]` | `receiver_emails` | No | Comma-separated emails, or `None` to disable |

### Environment Variable Resolution

Any config value can reference environment variables using `${VAR_NAME}` syntax:

```ini
[SSH]
password = ${SSH_PASSWORD}

[ENCRYPTION]
passphrase = ${BACKUP_ENCRYPTION_PASSPHRASE}

[DATABASE]
password = ${DB_PASSWORD}

[WEBHOOK]
auth_header = ${WEBHOOK_AUTH_TOKEN}
```

The variable is resolved at startup. If the referenced variable is not set, the application exits with a clear error message.

### Config Schema Versioning

The `[META] schema_version` field tracks config file compatibility. When you upgrade backup_handler and new config options are added, you'll see a warning if your config file's schema version is outdated:

```
WARNING: Config schema version mismatch: file has v2, expected v3.
Review config/config.ini.example for new options.
```

Update your config with the new options and set `schema_version = 3` to dismiss the warning.

### Config Profiles

Use `--profile NAME` to load `config/config.NAME.ini`:

```bash
# Loads config/config.staging.ini
python main.py --profile staging --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups
```

### `config/bot_config.ini` — Telegram Bot

| Section | Field | Required | Description |
|---------|-------|----------|-------------|
| `[TELEGRAM]` | `api_token` | **Yes** | Bot API token from @BotFather |
| `[USERS]` | `interacted_users` | **Yes** | Comma-separated Telegram user/chat IDs |

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

# S3 cloud backup
python main.py --operation-modes s3 --backup-mode full \
  --source-dir /data --backup-dirs /backups

# MySQL database backup
python main.py --operation-modes db --backup-mode full \
  --source-dir /data --backup-dirs /backups

# SSH backup via Tailscale VPN (connect with pre-auth key)
python main.py --operation-modes ssh --backup-mode full \
  --source-dir /data --ssh-servers my-server \
  --tailscale --tailscale-authkey tskey-auth-xxxxx

# SSH backup via Tailscale using config (set [TAILSCALE] in config.ini)
python main.py --operation-modes ssh --backup-mode full \
  --source-dir /data --ssh-servers my-tailscale-host

# Combined local + SSH + S3 with notifications
python main.py --operation-modes local ssh s3 --backup-mode full \
  --source-dir /data --backup-dirs /backups \
  --ssh-servers server1.com \
  --notifications --receiver admin@example.com

# Encrypt backups at rest
python main.py --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups --encrypt

# Deduplicate across backup directories
python main.py --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups --dedup

# Verify backup integrity (checks SHA-256 checksums from manifest)
python main.py --verify

# Restore from local backup
python main.py --restore --from-dir /backups/daily --to-dir /data/restored

# Restore from SSH remote
python main.py --restore --from-dir user@server:/backups/daily --to-dir /data/restored

# Restore from S3
python main.py --restore --from-dir s3://my-bucket/backups/daily --to-dir /data/restored

# Point-in-time restore
python main.py --restore --from-dir /backups --to-dir /data/restored \
  --restore-timestamp 20260228_030000

# Restore dry-run — preview what would be restored
python main.py --restore --from-dir /backups/daily --to-dir /data/restored --dry-run

# Retain only the 5 most recent backups
python main.py --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups --retain 5

# Exclude patterns
python main.py --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups \
  --exclude "*.log,*.tmp,__pycache__/*"

# Scheduled mode (reads times from config, runs continuously)
python main.py --scheduled

# Dry run — preview what would happen without copying anything
python main.py --dry-run --operation-modes local ssh --backup-mode full \
  --source-dir /data --backup-dirs /backups --ssh-servers server1.com

# Show current configuration
python main.py --show-setup

# View backup status dashboard
python main.py --status

# Use a config profile
python main.py --profile production --operation-modes local --backup-mode full \
  --source-dir /data --backup-dirs /backups
```

### All CLI Options

| Option | Description |
|--------|-------------|
| `--config PATH` | Path to configuration file (default: `config/config.ini`) |
| `--profile NAME` | Load config profile by name (resolves to `config/config.NAME.ini`) |
| `--operation-modes {local,ssh,s3,db}` | Backup targets (space-separated, default: `local`) |
| `--source-dir PATH` | Source directory to back up |
| `--backup-dirs PATH [PATH ...]` | Local backup destinations |
| `--ssh-servers HOST [HOST ...]` | Remote SSH servers |
| `--backup-mode {full,incremental,differential}` | Backup strategy |
| `--compress {zip,zip_pw}` | Enable compression (`zip_pw` = password-protected) |
| `--encrypt` | Encrypt backup files at rest using AES-256-GCM |
| `--dedup` | Enable file-level deduplication via hardlinks |
| `--exclude PATTERNS` | Comma-separated glob patterns to exclude |
| `--retain N` | Keep only N most recent backups per directory |
| `--tailscale` | Enable Tailscale VPN for SSH backups (connects using pre-auth key) |
| `--tailscale-authkey KEY` | Tailscale pre-auth key (overrides config `[TAILSCALE] auth_key`) |
| `--scheduled` | Run in scheduled mode using config times |
| `--notifications` | Enable Telegram & email notifications |
| `--receiver EMAIL [EMAIL ...]` | Email recipients for notifications |
| `--verify` | Verify backup integrity against manifest checksums |
| `--status` | Display backup status dashboard |
| `--restore` | Restore files from a backup source |
| `--from-dir PATH` | Source backup directory, ZIP, SSH path, or S3 URI to restore from |
| `--to-dir PATH` | Destination directory to restore files to |
| `--restore-timestamp TIMESTAMP` | Point-in-time restore (YYYYMMDD_HHMMSS format) |
| `--dry-run` | Preview without copying or syncing files (works with backup and restore) |
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
Full --------------------------------------------------->
       |                    |                |
       v                    v                v
   Differential         Differential     Differential
   (changes since       (changes since   (cumulative)
    last full)           last full)

       |         |         |
       v         v         v
   Incremental Incremental Incremental
   (changes    (changes    (changes
    since last  since last  since last
    backup)     backup)     backup)
```

---

## Encryption at Rest

Backup Handler supports AES-256-GCM encryption for backup files at rest. Encryption can be enabled via config or the `--encrypt` CLI flag.

### How it works

- Each file is encrypted individually with the format: `[16B salt][12B nonce][ciphertext + GCM tag]`
- Encrypted files get a `.enc` extension; originals are deleted
- Manifest files (`backup_manifest_*.json`) are **not** encrypted (needed for status and restore lookups)
- Encryption runs after the manifest is saved and before retention cleanup
- Parallel encryption is supported via `[ENCRYPTION] workers` for faster processing of large backups
- Progress bars show encryption/decryption progress

### Key management

Two key sources are supported (key file takes priority):

1. **Key file** — a 32-byte raw key file specified in `[ENCRYPTION] key_file`
2. **Passphrase** — a passphrase derived via PBKDF2-HMAC-SHA256 with 600,000 iterations

```ini
[ENCRYPTION]
enabled = True
passphrase = ${BACKUP_ENCRYPTION_PASSPHRASE}
workers = 4    # Parallel encryption threads
# Or use a key file:
# key_file = /path/to/32byte.key
```

### Compression + Encryption

When both compression and encryption are enabled, compression runs first, then encryption is applied to the compressed archive. A warning is logged since encrypted data does not compress well — this ordering ensures maximum compression efficiency.

### Restoring encrypted backups

Restore automatically detects `.enc` files, decrypts to a temporary directory, then restores from the decrypted copy — the original encrypted backup is never modified.

---

## Deduplication

File-level deduplication uses SHA-256 hashing and hardlinks to eliminate duplicate files:

- **Within-directory**: Identical files in the same backup directory are hardlinked
- **Cross-directory**: Files matching across multiple backup directories on the same filesystem are hardlinked
- Manifests and `.enc` files are excluded from deduplication
- Progress bars show deduplication progress
- Runs after encryption in the backup pipeline

Enable via config (`[DEDUP] enabled = True`) or CLI (`--dedup`).

---

## Tailscale VPN Integration

Backup Handler can automatically connect to a Tailscale tailnet before SSH backups, allowing secure remote backups over a private WireGuard-based VPN without exposing SSH ports to the public internet.

### How it works

1. Before SSH backup starts, Backup Handler checks Tailscale status
2. If not already connected, it brings Tailscale up using your pre-auth key
3. SSH servers are reached via their Tailscale hostnames or IPs on the tailnet
4. After backup completes, Tailscale can optionally disconnect (`disconnect_after = True`)

### Configuration

```ini
[TAILSCALE]
enabled = True
auth_key = ${TAILSCALE_AUTH_KEY}
hostname = backup-machine
advertise_tags = tag:backup
accept_routes = False
disconnect_after = False
```

### CLI Usage

```bash
# Enable Tailscale with auth key from CLI
python main.py --operation-modes ssh --backup-mode full \
  --source-dir /data --ssh-servers my-tailscale-host \
  --tailscale --tailscale-authkey tskey-auth-xxxxx

# Or use config — just pass --tailscale to override config enabled=False
python main.py --operation-modes ssh --backup-mode full \
  --source-dir /data --ssh-servers my-tailscale-host --tailscale
```

### Pre-auth Keys

Generate pre-auth keys at [Tailscale Admin Console](https://login.tailscale.com/admin/settings/keys). For automated backups, use **reusable** keys with an appropriate expiration. Store the key securely using environment variable syntax (`${TAILSCALE_AUTH_KEY}`).

### Requirements

- Tailscale must be installed on the machine (`tailscale` CLI available in PATH)
- `sudo` access is required for `tailscale up` / `tailscale down` commands
- The SSH servers must be reachable on the tailnet (either by Tailscale hostname or IP)

---

## Pre-flight Checks

Before any backup operation starts, Backup Handler verifies that all backup destinations are accessible:

- **Mount point detection**: For paths under `/mnt/` (e.g., `/mnt/data/backups`), checks that the mount point is actually mounted using `os.path.ismount()`
- **Directory creation**: If the mount is available but the backup directory doesn't exist, it's created automatically
- **Failure notification**: If destinations are inaccessible, the backup aborts immediately with a clear error and sends notifications (Telegram, SMTP, webhook) so you know right away

This prevents the common scenario where an external disk becomes unmounted and backups silently fail for days.

---

## Backup Verification

Verify backup integrity by checking files against the latest manifest in each backup directory:

```bash
python main.py --verify
```

Verification checks:
- File existence in backup directories
- File size matches manifest records
- **SHA-256 checksum validation** against checksums recorded in the manifest (v2.3.0+)
- Encrypted file handling (decrypts to temp for verification if passphrase/key available)
- Falls back to file-existence-only check if no manifest is found

---

## Restore

Restore supports multiple source types:

| Source | Syntax |
|--------|--------|
| Local directory | `--from-dir /backups/daily` |
| ZIP archive | `--from-dir /backups/archive.zip` |
| SSH remote | `--from-dir user@host:/backups/daily` or `--from-dir ssh://user@host/backups/daily` |
| S3 bucket | `--from-dir s3://bucket/prefix/path` |

### Restore dry-run

Preview what a restore would do without modifying any files:

```bash
python main.py --restore --from-dir /backups/daily --to-dir /data/restored --dry-run
```

### Point-in-time restore

Use `--restore-timestamp YYYYMMDD_HHMMSS` to restore files to a specific point in time using manifest history:

```bash
python main.py --restore --from-dir /backups --to-dir /restored \
  --restore-timestamp 20260228_030000
```

### Encrypted backup restore

If the backup contains `.enc` files, provide encryption credentials in `config.ini`. The restore process decrypts files to a temporary directory before restoring — original encrypted backups are not modified.

---

## Retention Policies

Automatically clean up old backups with two complementary strategies:

| Policy | Config | CLI Override | Description |
|--------|--------|-------------|-------------|
| **Age-based** | `[RETENTION] max_age_days = 30` | — | Remove backups older than N days |
| **Count-based** | `[RETENTION] max_count = 5` | `--retain 5` | Keep only N most recent backups per directory |

Both policies can be active simultaneously. Retention runs after encryption and deduplication in the backup pipeline.

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

### SMTP Email
- Sends both **HTML** (styled) and **plain text** versions (multipart/alternative)
- Configurable SMTP server with STARTTLS support (default port: 587)
- Automatic retry (3 attempts) on connection errors — no retry on authentication failures
- Configure in `[SMTP]` section of `config/config.ini`
- Recipients can be set via config (`[SMTP] to_addrs`) or CLI (`--receiver`)

### Webhooks
- Send notifications to any webhook endpoint (Slack, Discord, Microsoft Teams, or custom)
- Supports optional authorization headers for authenticated endpoints
- Configure in `[WEBHOOK]` section of `config/config.ini`

```ini
[WEBHOOK]
url = https://your-webhook-endpoint.example.com/webhook
auth_header = ${WEBHOOK_AUTH_TOKEN}
```

### CLI Receiver Emails
- Pass `--receiver email1@example.com email2@example.com` to send one-off notifications
- Validates email format before sending

---

## Security

This project follows security best practices:

| Measure | Details |
|---------|---------|
| **Env var secrets** | Config values support `${VAR}` syntax — secrets never need to be in config files |
| **AES-256-GCM encryption** | Backup files encrypted at rest with authenticated encryption |
| **PBKDF2 key derivation** | 600,000 iterations of HMAC-SHA256 for passphrase-based keys |
| **SHA-256 integrity** | Every backed-up file's checksum is recorded in the manifest and verified on restore |
| **No plaintext secrets on disk** | Passwords delivered via in-memory `BytesIO` buffers, temp files cleaned up immediately |
| **Secure credential storage** | Archive passwords stored in OS keyring (via `keyring` library) |
| **SSH host key policy** | Uses `paramiko.WarningPolicy()` instead of auto-accepting unknown hosts |
| **MySQL password handling** | Passed via `MYSQL_PWD` environment variable, never on command line |
| **Config file protection** | All `.ini` files with secrets are gitignored; `.example` templates provided |
| **Config validation** | Fail-fast on startup with clear error messages; no silent fallbacks to None |
| **Config schema versioning** | Warns when config file is outdated, helping users adopt new security options |
| **Path resolution** | Relative paths in config automatically resolved to absolute |
| **Instance locking** | PID lock file prevents duplicate scheduled instances |
| **Fault tolerance** | Per-file error handling — single file failures don't stop the job |

---

## Logging

Logs are written to `Logs/application.log` with automatic rotation:
- **Max file size:** 5 MB per log file
- **Backup count:** 5 rotated log files retained
- **Console output:** All log messages also printed to stdout
- **Log levels:** Configurable (default: `DEBUG`)

```
2026-02-28 03:00:01 - INFO - Configuration loaded successfully from config/config.ini
2026-02-28 03:00:01 - INFO - Performing full backup from /data
2026-02-28 03:00:01 - INFO - Successfully backed up /data/file.txt to /backups/file.txt
2026-02-28 03:00:02 - INFO - Encrypting backup files in /backups...
2026-02-28 03:00:02 - INFO - Encrypted 150 files in /backups (4 workers)
2026-02-28 03:00:03 - INFO - Deduplication saved 150 MB across 3 directories
2026-02-28 03:00:03 - INFO - SMTP email sent to admin@example.com: Backup Handler: Full backup completed
2026-02-28 03:00:03 - INFO - Webhook notification sent to https://hooks.slack.com/...
2026-02-28 03:00:03 - INFO - Notification sent to user: Backup completed
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Config error: 'source_dir' is not set` | Set `source_dir` in `[DEFAULT]` section of `config/config.ini` |
| `Config schema version mismatch` | Review `config/config.ini.example` for new options and update `schema_version` |
| `Config error: 'ssh_servers' is not set` | Set SSH fields in `[SSH]` section, or set `ssh = False` in `[MODES]` |
| `Config error: Invalid time format` | Use HH:MM 24-hour format (e.g., `03:00`, `14:30`) |
| `Environment variable 'X' is not set` | Set the referenced env var before running, or replace `${X}` with the actual value in config |
| `Encryption requires key_file or passphrase` | Set either `key_file` or `passphrase` in `[ENCRYPTION]` when `enabled = True` |
| `Error: config/bot_config.ini not found` | Copy `config/bot_config.ini.example` to `config/bot_config.ini` and fill in your bot token |
| `SMTP authentication failed` | Verify `[SMTP]` credentials. For Gmail, use an App Password |
| `ModuleNotFoundError` | Ensure venv is activated and `pip install -r requirements.txt` was run |
| Telegram notifications not sending | Verify bot token and chat ID. Send a message to the bot first to register |
| Webhook returning non-2xx | Verify the webhook URL and auth header. Check the endpoint's expected payload format |
| SSH connection refused | Check server address, port, and credentials. Verify the remote host key |
| Scheduled backup not triggering | Ensure schedule times in config match HH:MM format and the process is running |
| `--scheduled and --dry-run cannot be used together` | Dry-run is for one-off previews; remove `--dry-run` when running in scheduled mode |
| `Another backup-handler instance is already running` | A scheduled instance is already active. Kill it first, or remove `.backup-handler.lock` if stale |
| `mysqldump: command not found` | Install MySQL client tools (`apt install mysql-client` or equivalent) |
| Deduplication not saving space | Hardlinks only work within the same filesystem — ensure backup dirs share a mount |
| Verification shows all files missing | Ensure the backup was made with manifests enabled (v2.0.0+) |
| Checksum mismatches during verification | Files may have been modified after backup. Re-run a full backup |
| `Tailscale is not installed` | Install Tailscale: `curl -fsSL https://tailscale.com/install.sh \| sh` |
| `Failed to bring Tailscale up` | Verify auth key is valid and not expired. Check `sudo tailscale up` works manually |
| `Tailscale auth key missing` | Set `--tailscale-authkey` or `[TAILSCALE] auth_key` in config |
| `Backup aborted: destination(s) inaccessible` | The backup disk is not mounted. Mount it and retry |
| `Mount point /mnt/data is not mounted` | Mount the disk: `sudo mount /dev/sdX /mnt/data`. Consider adding to `/etc/fstab` |

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
