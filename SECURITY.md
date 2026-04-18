# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 2.5.x   | ✅        |
| 2.4.x   | ⚠️ Security fixes only |
| < 2.4   | ❌        |

Upgrade to the latest minor release to receive security patches.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Report suspected vulnerabilities privately via one of the following channels:

- **Preferred**: GitHub Security Advisories — open a draft advisory at
  <https://github.com/SP1R4/BackupHandler/security/advisories/new>.
- **Email**: `sp1r4.work@gmail.com` with the subject prefix
  `[backup-handler security]`.

Please include:

1. A clear description of the vulnerability and its impact.
2. Step-by-step reproduction instructions (or a proof-of-concept).
3. Affected versions and platforms.
4. Any mitigations or suggested fixes.

### Response timeline

| Stage                  | Target           |
| ---------------------- | ---------------- |
| Acknowledgement        | ≤ 3 business days |
| Triage & severity      | ≤ 7 business days |
| Fix released (critical)| ≤ 30 days         |
| Fix released (high)    | ≤ 60 days         |
| Coordinated disclosure | Agreed per case  |

We will keep you informed throughout triage and release and will credit you
in the release notes unless you request otherwise.

## Scope

In scope:

- All code under `src/`, `main.py`, and the default `config/*.example`
  templates.
- Shipped Docker images and GitHub Actions workflows.
- Default configuration and documented usage paths.

Out of scope:

- Vulnerabilities in third-party dependencies (report upstream first).
- Misconfiguration by end users (e.g. storing a passphrase in plaintext).
- Social-engineering or physical-access attacks.
- Denial-of-service via expected resource usage on very large backups.

## Hardening Expectations for Operators

Because this tool handles data at rest and in transit, operators should:

- Store `passphrase`, `auth_key`, and credential values via environment
  variables (`${VAR}` syntax) — never hard-code in committed config files.
- Run with the least-privileged OS user that can read the source and
  write the destination.
- Pin dependency versions via hash-checking mode in production.
- Enable encryption (`[ENCRYPTION] enabled = True`) for off-site backups.
- Verify restores on an isolated host before treating them as authoritative.
- Audit `Logs/audit.log` for restore, config-load, and key-load events.

## Security Controls in This Repository

- MySQL passwords pass through the `MYSQL_PWD` environment variable to
  `mysqldump`, never the command line.
- AES-256-GCM encryption with PBKDF2-HMAC-SHA256 (600,000 iterations).
- OTPs and generated zip passwords use `secrets` (CSPRNG), not `random`.
- Webhook URLs are validated against an `http`/`https` scheme allowlist.
- Pre-commit and CI scans: `gitleaks`, `bandit`, `pip-audit`.
- Branch protection, required reviews, and signed commits are recommended
  for downstream forks.
