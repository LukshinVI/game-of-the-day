#!/usr/bin/env bash
# Online SQLite backup for Game of the Day.
#
#   game-backup.sh [run]  snapshot -> integrity_check -> checkpoint -> gzip -> verify
#                         -> atomic publish -> manifest -> optional off-site step -> retention
#   game-backup.sh check  fail unless the last *successfully published* backup (named in the
#                         manifest) exists, is a valid gzip and is younger than
#                         GAME_BACKUP_MAX_AGE_HOURS
#
# Never copies the live file: the SQLite online backup API gives a consistent snapshot
# while writers keep running. Retention runs last, only after every earlier step
# (including the optional off-site step) has succeeded.
#
# The manifest `.last-local-ok` holds "<epoch> <file name>" and is replaced atomically, so
# touching or renaming files cannot make an old backup look fresh.
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
TMPMARK=""

log() { printf '%s game-backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

require_uint() { # name value minimum
    case "$2" in ''|*[!0-9]*) die "$1 must be a non-negative integer, got '$2'" ;; esac
    [ "$2" -ge "$3" ] || die "$1 must be at least $3, got $2"
}
require_uint GAME_BACKUP_RETAIN_DAYS "$RETAIN_DAYS" 1
require_uint GAME_BACKUP_MIN_KEEP "$MIN_KEEP" 1
require_uint GAME_BACKUP_MAX_AGE_HOURS "$MAX_AGE_HOURS" 1
require_uint GAME_BACKUP_MIN_FREE_MB "$MIN_FREE_MB" 1

write_marker() { # file, content: atomic replace so a crash never leaves a truncated marker
    TMPMARK="$1.tmp.$$"
    printf '%s\n' "$2" > "$TMPMARK"
    mv -f "$TMPMARK" "$1"
    TMPMARK=""
}

check() {
    local marker="$DEST/.last-local-ok" epoch name f now
    [ -f "$marker" ] || die "no successful backup recorded in $marker"
    read -r epoch name < "$marker" || true
    case "$epoch" in ''|*[!0-9]*) die "manifest is malformed: $marker" ;; esac
    [ -n "$name" ] || die "manifest names no backup file: $marker"
    f="$DEST/$name"
    [ -s "$f" ] || die "backup named in the manifest is missing or empty: $name"
    gzip -t "$f" || die "backup named in the manifest is not a valid gzip: $name"
    now=$(date -u +%s)
    [ $((now - epoch)) -le $((MAX_AGE_HOURS * 3600)) ] || die "last successful backup is older than ${MAX_AGE_HOURS}h ($name)"
    if [ -n "$OFFSITE_CMD" ]; then
        [ -f "$DEST/.last-offsite-ok" ] || die "off-site step is configured but has never succeeded"
        read -r epoch _ < "$DEST/.last-offsite-ok" || true
        case "$epoch" in ''|*[!0-9]*) die "off-site marker is malformed" ;; esac
        [ $((now - epoch)) -le $((MAX_AGE_HOURS * 3600)) ] || die "last off-site success is older than ${MAX_AGE_HOURS}h"
    fi
    log "OK $name"
}

run() {
    [ -r "$DB" ] || die "database not readable: $DB"
    mkdir -p "$DEST"
    [ -w "$DEST" ] || die "backup directory not writable: $DEST"

    if command -v flock > /dev/null 2>&1; then
        exec 9> "$DEST/.backup.lock"
        flock -n 9 || die "another backup run is in progress"
    fi

    local db_kb need_kb avail_kb
    db_kb=$(du -k "$DB" | cut -f1)
    need_kb=$((db_kb * 3))
    [ "$need_kb" -ge $((MIN_FREE_MB * 1024)) ] || need_kb=$((MIN_FREE_MB * 1024))
    avail_kb=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
    [ "$avail_kb" -ge "$need_kb" ] || die "not enough free space in $DEST (${avail_kb}KB free, ${need_kb}KB needed)"

    find "$DEST" -maxdepth 1 \( -name '.tmp-*' -o -name '.last-*.tmp.*' \) -mmin +1440 -delete

    local stamp final
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    TMP="$DEST/.tmp-$stamp-$$.db"
    TMPGZ="$TMP.gz"
    final="$DEST/game-$stamp.db.gz"
    case "$TMP$DB" in *\'*) die "single quote in a path is not supported" ;; esac
    trap 'rm -f "$TMP" "$TMP-wal" "$TMP-shm" "$TMPGZ" "$TMPMARK"' EXIT

    sqlite3 -cmd ".timeout 10000" "$DB" ".backup '$TMP'" || die "sqlite online backup failed"

    local ic
    ic=$(sqlite3 "$TMP" "PRAGMA integrity_check;") || die "integrity_check could not run"
    [ "$ic" = "ok" ] || die "integrity_check failed: $(printf '%s' "$ic" | head -c 200)"
    if [ -n "$(sqlite3 "$TMP" "PRAGMA foreign_key_check;" | head -n 1)" ]; then
        log "WARN foreign_key_check reports violations in the snapshot (not fatal: the copy is still the best data we have)"
    fi

    # Make the snapshot a single self-contained file (nothing left in a -wal file) before compressing.
    sqlite3 "$TMP" "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null || die "could not checkpoint the snapshot"

    gzip -9 -c "$TMP" > "$TMPGZ"
    gzip -t "$TMPGZ" || die "compressed snapshot failed verification"
    mv -f "$TMPGZ" "$final"
    write_marker "$DEST/.last-local-ok" "$(date -u +%s) $(basename "$final")"
    log "local backup ok: $(basename "$final") ($(du -k "$final" | cut -f1)KB)"

    if [ -n "$OFFSITE_CMD" ]; then
        "$OFFSITE_CMD" "$final" || die "off-site step failed; retention skipped"
        write_marker "$DEST/.last-offsite-ok" "$(date -u +%s) $(basename "$final")"
        log "off-site ok"
    fi

    # File names sort chronologically, so retention does not depend on mtimes.
    local old
    ls -1 "$DEST"/game-*.db.gz | sort -r | tail -n +$((MIN_KEEP + 1)) | while IFS= read -r old; do
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
