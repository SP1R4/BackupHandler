## System Snapshot & Restore

Never lose your system setup to a format again. The snapshot feature captures your entire machine state and generates a restore script that rebuilds everything on a fresh OS install.

### What it captures

| Category | Linux/Ubuntu | Windows |
|----------|-------------|---------|
| **Packages** | APT (manually installed), Snap, Flatpak, pipx, pip user, npm global, Cargo, Go | Winget, Chocolatey, pip, npm, Cargo |
| **Repositories** | APT sources lists, PPAs, GPG keyrings | — |
| **Configs** | Dotfiles (`.bashrc`, `.gitconfig`, `.ssh/config`, etc.), cron jobs, systemd user services, dconf/GNOME settings, `/etc/fstab`, `/etc/hosts` | Environment variables, dotfiles, scheduled tasks |
| **Apps** | VS Code extensions + settings, Sublime Text settings, browser profile paths (Firefox, Brave, Chrome), Docker images + compose files | VS Code extensions + settings, WSL distros, Docker |
| **Security** | SSH key metadata (public only), GPG key IDs | SSH key metadata |
| **Network** | NetworkManager connections (WiFi/VPN names), WireGuard config names | — |
| **Shell** | Shell history (last 5000 entries), custom scripts in `~/bin` and `~/.local/bin` | — |
| **Fonts** | User-installed fonts (`~/.local/share/fonts`) | — |

### Creating a snapshot

```bash
# Snapshot to default directory (snapshots/)
python main.py --snapshot

# Snapshot to backup disk
python main.py --snapshot --snapshot-output /mnt/data/backups/snapshots
```

Output: `snapshot_<hostname>_<timestamp>.json`

### Generating a restore script

```bash
python main.py --restore-snapshot snapshots/snapshot_myhost_20260404_135413.json
```

This generates an executable bash script (Linux) or PowerShell script (Windows) with:

- **14 phased sections** in correct install order (repos → APT → Snap → pip → npm → Cargo → VS Code → dotfiles → cron → dconf → fstab → hosts)
- **Error-tolerant** — each package install uses `|| warn` so one failure doesn't stop the script
- **Base64-encoded content** �� dotfiles, VS Code settings, dconf dumps are safely embedded
- **Correct ownership** — `run_as_user` helper ensures files belong to your user, not root
- **Manual step reminders** — SSH keys, GPG keys, browser profiles, WiFi passwords, fstab merging

### Running the restore

After a fresh OS install:

```bash
# Mount your backup disk
sudo mount /dev/sdb1 /mnt/data

# Review the script first!
less /mnt/data/backups/snapshots/restore_myhost.sh

# Run it
chmod +x restore_myhost.sh
sudo ./restore_myhost.sh
```

### Comparing snapshots

Track what changed on your system over time:

```bash
python main.py --snapshot-diff snapshots/march.json snapshots/april.json
```

Output:
```
=== Snapshot Diff ===

  apt:
    + newpackage
    - removedpackage

  vscode_extensions:
    + ms-python.python

  snap:
    + signal-desktop
```

### Security notes

- **SSH private keys are NOT captured** — only public key metadata (filenames, types, comments) for reference
- **GPG private keys are NOT captured** — only key IDs and UIDs
- **WiFi passwords are NOT captured** — only connection names with a flag indicating if a PSK exists
- **WireGuard configs are NOT captured** — only config file names
- All sensitive content must be restored manually from your backup

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

### Linux (systemd) — recommended for production

Hardened oneshot service + timer live in `contrib/systemd/`. The service
runs under an unprivileged `backup` user with `ProtectSystem=strict`,
`MemoryDenyWriteExecute`, and a `SystemCallFilter` allowlist. The timer
fires daily at 03:00 with 15-minute jitter and catches up on missed runs
after a reboot.

```bash
# 1. Create the unprivileged operator account and its state dir:
sudo useradd --system --home /var/lib/backup-handler \
     --shell /usr/sbin/nologin backup
sudo install -d -o backup -g backup -m 0750 /var/lib/backup-handler/Logs

# 2. Install and enable the unit + timer (edit override.conf for local paths):
sudo install -m 0644 contrib/systemd/backup-handler.service /etc/systemd/system/
sudo install -m 0644 contrib/systemd/backup-handler.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now backup-handler.timer

# 3. Observe:
systemctl list-timers backup-handler.timer
journalctl -u backup-handler.service -f
```

**Do not `systemctl enable backup-handler.service` directly** — the timer
owns the schedule. To change the time or environment without editing the
shipped unit, use `systemctl edit backup-handler.{service,timer}`.

Exit codes from a scheduled run propagate to systemd: `0` success, `2`
pre-flight failure (mount missing / pre-hook rejected), `3` one or more
backup modes failed. Non-zero exits leave the unit in `failed` state so
your monitoring (Prometheus, journal-based alerting, or a heartbeat —
see below) can page an operator.

The legacy `scripts/backup-handler.service` helper is still present for
the `install_service.sh` wrapper, but new deployments should use the
hardened `contrib/systemd/` units.

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

