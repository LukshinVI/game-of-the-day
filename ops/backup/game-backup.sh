#!/usr/bin/env bash
# Online SQLite backup for Game of the Day.
#
#   game-backup.sh [run]  snapshot -> integrity_check -> gzip -> verify -> atomic publish
#                         -> markers -> optional off-site step -> retention
#   game-backup.sh check  fail unless the newest backup exists, is non-empty, is a valid
#                         gzip and is younger than GAME_BACKUP_MAX_AGE_HOURS
#
# Never copies the live file: the SQLite online backup API gives a consistent snapshot
# while writers keep running. Retention runs last, only after every earlier step
# (including the optional off-site step) has succeeded.
set -euo pipefail
umask 077

DB=${GAME_DB:-/srv/game-of-the-day/data/game-of-the-day.db}
DEST=${GAME_BACKUP_DIR:-/var/backups/game-of-the-day}
RETAIN_DAYS=${GAME_BACKUP_RETAIN_DAYS:-14}
MIN_KEEP=${GAME_BACKUP_MIN_KEEP:-7}
MAX_AGE_HOURS=${GAME_BACKUP_MAX_AGE_HOURS:-26}
MIN_FREE_MB=${GAME_BACKUP_MIN_FREE_MB:-200}
OFFSITE_CMD=${GAME_BACKUP_OFFSITE_CMD:-}

# Global on purpose: the EXIT trap runs after run() has returned.
TMP=""
TMPGZ=""

log() { printf '%s game-backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

newest_backup() { ls -1t "$DEST"/game-*.db.gz 2>/dev/null | head -n 1 || true; }

check() {
    local f
    f=$(newest_backup)
    [ -n "$f" ] || die "no backup found in $DEST"
    [ -s "$f" ] || die "newest backup is empty: $f"
    gzip -t "$f" || die "newest backup is not a valid gzip: $f"
    [ -z "$(find "$f" -mmin +$((MAX_AGE_HOURS * 60)))" ] || die "newest backup is older than ${MAX_AGE_HOURS}h: $f"
    if [ -n "$OFFSITE_CMD" ]; then
        [ -f "$DEST/.last-offsite-ok" ] || die "off-site step is configured but has never succeeded"
        [ -z "$(find "$DEST/.last-offsite-ok" -mmin +$((MAX_AGE_HOURS * 60)))" ] || die "last off-site success is older than ${MAX_AGE_HOURS}h"
    fi
    log "OK newest=$(basename "$f")"
}

run() {
    [ -r "$DB" ] || die "database not readable: $DB"
    mkdir -p "$DEST"
    [ -w "$DEST" ] || die "backup directory not writable: $DEST"

    local db_kb need_kb avail_kb
    db_kb=$(du -k "$DB" | cut -f1)
    need_kb=$((db_kb * 3))
    [ "$need_kb" -ge $((MIN_FREE_MB * 1024)) ] || need_kb=$((MIN_FREE_MB * 1024))
    avail_kb=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
    [ "$avail_kb" -ge "$need_kb" ] || die "not enough free space in $DEST (${avail_kb}KB free, ${need_kb}KB needed)"

    find "$DEST" -maxdepth 1 -name '.tmp-*' -mmin +1440 -delete

    local stamp final
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    TMP="$DEST/.tmp-$stamp.db"
    TMPGZ="$TMP.gz"
    final="$DEST/game-$stamp.db.gz"
    case "$TMP$DB" in *\'*) die "single quote in a path is not supported" ;; esac
    trap 'rm -f "$TMP" "$TMP-wal" "$TMP-shm" "$TMPGZ"' EXIT

    sqlite3 -cmd ".timeout 10000" "$DB" ".backup '$TMP'" || die "sqlite online backup failed"

    local ic
    ic=$(sqlite3 "$TMP" "PRAGMA integrity_check;") || die "integrity_check could not run"
    [ "$ic" = "ok" ] || die "integrity_check failed: $(printf '%s' "$ic" | head -c 200)"
    if [ -n "$(sqlite3 "$TMP" "PRAGMA foreign_key_check;" | head -n 1)" ]; then
        log "WARN foreign_key_check reports violations in the snapshot (not fatal)"
    fi

    # Make the snapshot self-contained (nothing left in a -wal file) before compressing it.
    sqlite3 "$TMP" "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null || die "could not checkpoint the snapshot"

    gzip -9 -c "$TMP" > "$TMPGZ"
    gzip -t "$TMPGZ" || die "compressed snapshot failed verification"
    mv -f "$TMPGZ" "$final"
    date -u +%s > "$DEST/.last-local-ok"
    log "local backup ok: $(basename "$final") ($(du -k "$final" | cut -f1)KB)"

    if [ -n "$OFFSITE_CMD" ]; then
        "$OFFSITE_CMD" "$final" || die "off-site step failed; retention skipped"
        date -u +%s > "$DEST/.last-offsite-ok"
        log "off-site ok"
    fi

    local old
    ls -1t "$DEST"/game-*.db.gz | tail -n +$((MIN_KEEP + 1)) | while IFS= read -r old; do
        if [ -n "$(find "$old" -mtime +"$RETAIN_DAYS")" ]; then
            rm -f -- "$old"
            log "pruned $(basename "$old")"
        fi
    done
}

case "${1:-run}" in
    run) run ;;
    check) check ;;
    *) die "usage: $0 [run|check]" ;;
esac
