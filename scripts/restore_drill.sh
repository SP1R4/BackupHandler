#!/usr/bin/env bash
#
# restore_drill.sh — prove the backups are actually restorable.
#
# Picks the most recent backup manifest, restores it to a scratch directory,
# and verifies the restored tree against the manifest. Exit code:
#
#   0  drill passed
#   1  configuration problem (manifest/backup dir missing)
#   2  restore failed
#   3  verification failed
#   4  notification failed (drill itself passed)
#
# Designed to run under systemd (backup-handler-drill.service).
# Every run writes an audit line via backup-handler to Logs/audit.log.

set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/opt/backup-handler}"
SCRATCH_DIR="${SCRATCH_DIR:-/tmp/backup-drill}"
CONFIG_FILE="${CONFIG_FILE:-${PROJECT_ROOT}/config/config.ini}"
WEBHOOK_URL="${DRILL_WEBHOOK_URL:-}"

BACKUP_HANDLER="${BACKUP_HANDLER:-${PROJECT_ROOT}/venv/bin/backup-handler}"
if [[ ! -x "$BACKUP_HANDLER" ]]; then
    BACKUP_HANDLER="python3 ${PROJECT_ROOT}/main.py"
fi

log() { printf '[drill %s] %s\n' "$(date -Iseconds)" "$*" >&2; }

notify() {
    local status="$1"
    local message="$2"
    if [[ -z "$WEBHOOK_URL" ]]; then
        return 0
    fi
    curl --silent --show-error --fail --max-time 30 \
        -H 'Content-Type: application/json' \
        -d "$(printf '{"text":"[backup-drill %s] %s","content":"[backup-drill %s] %s"}' \
              "$status" "$message" "$status" "$message")" \
        "$WEBHOOK_URL" || log "webhook notify failed (non-fatal)"
}

cleanup() {
    local rc=$?
    rm -rf "$SCRATCH_DIR" || true
    exit "$rc"
}
trap cleanup EXIT

if [[ ! -f "$CONFIG_FILE" ]]; then
    log "config not found: $CONFIG_FILE"
    notify FAIL "config not found at $CONFIG_FILE"
    exit 1
fi

BACKUP_DIR="$(awk -F= '/^backup_dirs/ {gsub(/ /,""); split($2,a,","); print a[1]; exit}' "$CONFIG_FILE")"
if [[ -z "$BACKUP_DIR" || ! -d "$BACKUP_DIR" ]]; then
    log "backup directory not resolvable from config: '$BACKUP_DIR'"
    notify FAIL "backup directory missing or unreadable"
    exit 1
fi

LATEST_MANIFEST="$(find "$BACKUP_DIR" -maxdepth 1 -name 'backup_manifest_*.json' -printf '%T@ %p\n' \
    | sort -n | tail -1 | awk '{print $2}')"
if [[ -z "$LATEST_MANIFEST" ]]; then
    log "no manifests found in $BACKUP_DIR"
    notify FAIL "no manifests in $BACKUP_DIR"
    exit 1
fi

TIMESTAMP="$(basename "$LATEST_MANIFEST" | sed -E 's/^backup_manifest_(.*)\.json$/\1/')"
log "drill target manifest: $LATEST_MANIFEST (timestamp $TIMESTAMP)"

rm -rf "$SCRATCH_DIR"
mkdir -p "$SCRATCH_DIR"

log "running dry-run restore first"
$BACKUP_HANDLER --restore \
    --from-dir "$BACKUP_DIR" \
    --to-dir "$SCRATCH_DIR" \
    --restore-timestamp "$TIMESTAMP" \
    --dry-run \
    >"$SCRATCH_DIR/.dry-run.log" 2>&1 || {
        log "dry-run restore failed; aborting drill"
        notify FAIL "dry-run restore failed (see journalctl -u backup-handler-drill.service)"
        exit 2
    }

log "running real restore"
$BACKUP_HANDLER --restore \
    --from-dir "$BACKUP_DIR" \
    --to-dir "$SCRATCH_DIR" \
    --restore-timestamp "$TIMESTAMP" \
    >"$SCRATCH_DIR/.restore.log" 2>&1 || {
        log "restore failed"
        notify FAIL "restore failed for manifest $TIMESTAMP"
        exit 2
    }

log "running checksum verification"
$BACKUP_HANDLER --verify \
    >"$SCRATCH_DIR/.verify.log" 2>&1 || {
        log "verification failed"
        notify FAIL "verify reported corruption/missing files"
        exit 3
    }

RESTORED_COUNT="$(find "$SCRATCH_DIR" -type f ! -name '.*.log' | wc -l)"
if [[ "$RESTORED_COUNT" -eq 0 ]]; then
    log "restore produced zero files — drill failure"
    notify FAIL "restore produced zero files"
    exit 3
fi

log "drill OK — restored $RESTORED_COUNT files"
notify OK "drill passed — $RESTORED_COUNT files restored from $TIMESTAMP"
exit 0
