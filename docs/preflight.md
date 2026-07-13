## Pre-flight Self-Heal

The pre-flight stage is the answer to a real production incident: on
2026-04-16 a backup target's external disk got unmounted, the kernel
exposed the mountpoint as a regular root-owned directory on the system
disk, the script crashed in a pre-logger code path, Telegram failed
because DNS was down, and **16 days of cron firings produced zero
backups and zero alerts**. Every defense we had assumed at least one
channel would still work; in that incident none did.

The redesigned pre-flight runs before every backup and assumes nothing.
It is configured under `[PREFLIGHT]` in `config/config.ini`:

```ini
[PREFLIGHT]
enabled = True
expected_mount = /mnt/data
expected_label = DATA                                # XFS / ext4 LABEL
expected_uuid = 5a719803-02d0-4834-81af-8175d1ec5ef1
expected_fs_type = xfs
expected_owner = sp1r4-r
auto_mount = True
auto_fix_ownership = False                           # safer default
ensure_fstab = True
staleness_factor = 2.0                               # x interval_minutes
local_mail_to = root                                 # DNS-independent alerts
```

What runs, in order:

1. **Logger first.** `AppLogger` is initialized before `print_banner`,
   `setup_argparse`, or any filesystem operation, so a crash in any
   pre-flight step is *always* logged.
2. **Mountpoint identity.** `os.path.ismount` proves a real mount.
   `findmnt` + `blkid` confirm the source device matches
   `expected_label` / `expected_uuid`. A wrong-volume mount is **fatal**
   — pre-flight refuses to write a backup onto an impostor disk.
3. **Auto-mount.** When the mount is missing and `auto_mount = True`,
   pre-flight tries (in order) `sudo -n mount <mountpoint>`,
   `sudo -n mount UUID=…`, `sudo -n mount LABEL=…`. Each `sudo` call is
   non-interactive (`-n`) and relies on the `NOPASSWD` rule the
   installer drops in `/etc/sudoers.d/backup-handler`.
4. **fstab maintenance.** With `ensure_fstab = True`, a missing
   `UUID=…` entry is appended with `nofail,x-systemd.device-timeout=30`,
   so the disk being absent never blocks boot.
5. **Writability probe.** A 4-byte file is created and unlinked under
   the destination root. Mode/ownership mismatches that would surface
   later as a 5,000-line wave of `Permission denied` errors are caught
   here.
6. **JSON status sentinel.** Every run writes
   `Logs/last_run_status.json` atomically (tmp + rename) at three
   points: `started` / `success` / `failure`. The sentinel survives
   even when log rotation drops old `application.log.N` files, and is
   the source of truth for staleness checks.
7. **DNS-independent alerting.** On fatal failure pre-flight pipes a
   short summary to `mail(1)` (or `sendmail`), addressed to
   `local_mail_to`. The local MTA queues it on the host and delivers
   when the network returns — no Telegram, no SMTP, no DNS required.
8. **Staleness check.** If the last sentinel timestamp is older than
   `staleness_factor x interval_minutes`, a non-fatal `STALE` alert is
   emitted via every available channel — even if today's run succeeds,
   you'll still hear that yesterday's didn't.

**Exit codes** propagate the pre-flight outcome to systemd / cron /
Prometheus: `0` success, `1` config error, `2` pre-flight failure
(mount, identity, writability), `3` one or more backup modes failed.

The full triage flow lives in [RUNBOOK.md §2.4](RUNBOOK.md).

---

