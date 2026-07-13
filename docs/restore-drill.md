## Restore Drill

A backup you have never restored is a backup you do not have. The shipped
drill proves restorability on a schedule:

```bash
sudo install -m 0644 contrib/systemd/backup-handler-drill.service /etc/systemd/system/
sudo install -m 0644 contrib/systemd/backup-handler-drill.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now backup-handler-drill.timer
```

The drill runs weekly (`Sun 04:30` by default, override with
`systemctl edit backup-handler-drill.timer`). Each run:

1. Picks the most recent `backup_manifest_*.json` from the first
   configured `backup_dirs` entry.
2. Performs a dry-run restore into `/tmp/backup-drill` and bails if that
   fails.
3. Performs a real restore, then `--verify` checks every file's SHA-256
   against the manifest.
4. Optionally pings a webhook with pass/fail (set `DRILL_WEBHOOK_URL` in
   a drop-in).

Exit codes: `0` pass, `1` config problem, `2` restore failed, `3` verify
failed, `4` drill passed but notification failed. **A failed drill is a
higher-severity incident than a failed backup** — the backups are
untrusted until a drill passes.

For Qsafe-encrypted backups, the drill host needs `qsafe_secret_key` (and
its passphrase) configured, and `qsafe_sign_pub` if manifests are signed.
At least quarterly, run the drill with the **escrow** key instead of the
ops key — see RUNBOOK section 6.4.

---

