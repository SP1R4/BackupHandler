## One-Shot System Install (`--install`)

Standing up the pre-flight self-heal needs five OS-level prerequisites:
the destination volume mounted and chowned, an fstab entry, a
`NOPASSWD` sudoers rule for the mount commands, a local MTA, and a live
smoke test that proves the chain works. Doing this by hand is exactly
the kind of step that gets skipped — and a self-heal you forgot to
provision is no self-heal at all.

`--install` is a single privileged invocation that does all of it,
idempotently:

```bash
sudo -E /path/to/venv/bin/python /path/to/main.py \
    --config /path/to/config.ini --install

# Preview without changing anything:
sudo -E /path/to/venv/bin/python /path/to/main.py \
    --config /path/to/config.ini --install --dry-run
```

What each step does:

| Step | Action |
|------|--------|
| **mount** | Mounts `expected_mount` if not already mounted (UUID first, LABEL fallback) |
| **destination** | Creates the backup tree under the mount and chowns it to `expected_owner` |
| **fstab** | Appends a `nofail,x-systemd.device-timeout=30` entry by `UUID=`. Backs up `/etc/fstab` to a timestamped file first; if `mount -a` fails afterwards, the backup is restored automatically |
| **sudoers** | Writes the rule into a `mkdtemp` staging file, validates with `visudo -cf`, and only then atomically moves it to `/etc/sudoers.d/backup-handler`. A broken sudoers file can lock you out of the machine — this path makes that impossible |
| **mta** | `apt-get install postfix bsd-mailx` with `debconf-set-selections "Local only"`. `apt-get update` failures (e.g. one broken third-party repo) are logged as warnings, not fatals — the main archive cache is enough for both packages |
| **smoke test** | Runs the full pre-flight pipeline against the live config and writes a `installer_smoke_test` sentinel |
| **handover** | Chowns the project's `Logs/` and `BackupTimestamp/` back to the unprivileged owner so the next normal cron run can write through |

The installer never touches `/etc/fstab` or `/etc/sudoers.d/` without
both a backup and validation in place. Re-running it is safe: each step
detects "already done" and returns `skipped`.

---

