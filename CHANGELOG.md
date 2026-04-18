# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Hardened systemd unit + timer pair in `contrib/systemd/`
  (`backup-handler.service` / `.timer`). Runs under an unprivileged
  `backup` user with `ProtectSystem=strict`, `MemoryDenyWriteExecute`,
  and a `SystemCallFilter` allowlist.
- Weekly restore-drill systemd pair
  (`backup-handler-drill.service` / `.timer`) that exercises the full
  restore path and verifies every file's checksum. Failed drills are a
  higher-severity incident than failed backups.
- `scripts/restore_drill.sh` — picks the latest manifest, dry-runs,
  restores to a scratch dir, verifies, and optionally pings a webhook.
  Exit codes `0`/`1`/`2`/`3`/`4` distinguish pass, config, restore,
  verify, and notification failures.
- `RUNBOOK.md` — step-by-step procedures for full-host, single-file,
  MySQL PITR, encrypted archive, and system-snapshot restores, plus
  failure triage by exit code.
- `[HEARTBEAT]` config section and `src/heartbeat.py`: optional
  dead-man's-switch ping on successful runs
  (healthchecks.io / Dead Man's Snitch / Uptime Kuma compatible).
  Scheme is restricted to `http`/`https`.
- `pyproject.toml` with full tooling configuration (ruff, black, mypy,
  pytest, coverage, bandit).
- `SECURITY.md` with disclosure policy and hardening guidance.
- `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`.
- GitHub Actions CI workflow: lint, type-check, test matrix
  (Python 3.10/3.11/3.12), security scan (bandit, pip-audit), secrets scan
  (gitleaks), build smoke test.
- GitHub Actions release workflow triggered on `v*.*.*` tags.
- `.pre-commit-config.yaml` with ruff, black, gitleaks, bandit.
- Structured / JSON-capable logger with correlation IDs, separate
  `Logs/audit.log` audit stream, and syslog handler option.
- `src/__version__.py` as the single source of truth for the version string.
- Dockerfile (multi-stage, non-root user) and `.dockerignore`.
- Issue and PR templates, CODEOWNERS, dependabot config.
- Type hints on pure-logic modules (`utils`, `manifest`, `webhook_notify`,
  `logger`).

### Changed

- `main.backup_operation` now returns a process exit code
  (`0` / `2` / `3`) and per-mode failures (local / SSH / S3 / DB) are
  tracked individually. A partial-success run exits non-zero so systemd
  and Prometheus alerting can page an operator. Silent pre-flight and
  pre-hook failure paths now propagate non-zero.
- `update_last_backup_time()` only advances on full success — a partial
  failure no longer skews future incremental-window calculations.
- OTP / zip-password generation (`utils.generate_otp`) now uses `secrets`
  (CSPRNG) instead of `random`, and defaults to 16 characters.
- Webhook URLs are validated against an `http`/`https` allowlist before
  sending.
- Bare `except Exception` handlers were narrowed to specific exception
  classes across `utils`, `db_sync`, `sync`, and `webhook_notify`.
- Lock-file PID verification now also checks the process command name to
  avoid false-positives from PID recycling.
- Test suite split from a single file into per-module suites with a shared
  `conftest.py`.

### Removed

- Dead code: `src/test.py`, `src/backup.py` (neither was imported anywhere).

### Security

- Replaced `random.choice` with `secrets.choice` in OTP/password generation —
  the OTP is used as a zip-archive password in `zip_pw` mode, so the prior
  use of `random` was a real weakness.
- Webhook URL scheme allowlist prevents accidental SSRF via `file://`,
  `gopher://`, or similar schemes.

---

## [2.5.0] — 2026-04-04

### Added

- System snapshot and restore feature for OS rebuild after format
  (`--snapshot`, `--restore-snapshot`, `--snapshot-diff`).

## [2.4.0] — 2026-03-15

### Added

- Tailscale VPN integration for SSH backups (`--tailscale`,
  `--tailscale-authkey`, `[TAILSCALE]` config section).
- Pre-flight mount check to prevent silent backup failures when a backup
  target is an unmounted mountpoint.

## [2.3.0] — 2026-03-10

### Added

- Config schema versioning via `[META] schema_version`.
- SHA-256 checksum verification in manifests.
- Parallel encryption / decryption with `ThreadPoolExecutor`.
- Incremental database backup support (`--single-transaction`,
  `--master-data=2`).
- HTML email notifications with status-colour header.
- Bandwidth-aware S3 uploads via `boto3.s3.transfer.TransferConfig`.
- Restore dry-run preview mode.
- Webhook notifications (Slack, Discord, Teams compatible).
- Progress bars (tqdm) for encryption, decryption, and deduplication.
- Compression + encryption compatibility warning.

## [2.2.0] — 2026-02-28

### Added

- Seven new features including verification, deduplication, manifests,
  email notifications (SMTP), and remote restore.

### Fixed

- SMTP notification connection leaks.
- File-pattern matching bugs.

## [2.0.0] — 2026-02-27

### Added

- Ten new features: encryption at rest, S3 sync, dry-run, retention
  policies, environment-variable config resolution, and more.

## [1.2.0] — 2026-02-22

### Added

- `--version` flag.
- SMTP retry with exponential backoff.
- POSIX signal handling for graceful shutdown.

### Removed

- Dead code paths identified during audit.

## [1.1.0] — 2026-02-21

### Added

- Dry-run mode, PID locking, hardening fixes.
- Config validation and graceful error handling.
- systemd service support for startup.

## [1.0.0] — 2026-02-21

### Added

- Initial release: backup_handler with full code-review fixes,
  local/SSH modes, Telegram bot notifications.
