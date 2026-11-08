#!/usr/bin/env bash
# Encrypted off-site copies of the SQLite backup in a private Git repository.
#
#   offsite-git.sh <backup.db.gz>   encrypt (age) -> verify the round trip -> commit -> push
#   offsite-git.sh push-latest      the same for the newest published local backup
#                                   (the manifest of game-backup.sh), used by game-offsite-push
#   offsite-git.sh verify           fetch the newest copy, decrypt, gunzip, integrity_check
#   offsite-git.sh restore <db>     the same, then install it as <db> (refuses to overwrite)
#
# Only ciphertext leaves the host: the repository (and its host) never sees the data.
# GitHub's host keys are pinned. The SSH key should be a deploy key for that one
# repository; if a broader key is used, run this as a different user than the
# application (game-offsite-push.service runs it as root, out of the bots' reach).
#
# Configuration (environment):
#   GAME_OFFSITE_REPO       git@github.com:<owner>/<private-repo>.git   (required)
#   GAME_OFFSITE_BRANCH     branch holding the copies                   (db-backups)
#   GAME_OFFSITE_DIR        state: age.key, work tree                   (/var/lib/game-of-the-day/offsite)
#   GAME_OFFSITE_SSH_KEY    SSH key for the repository                  ($GAME_OFFSITE_DIR/deploy_key)
#   GAME_BACKUP_DIR         local backups, for push-latest              (/var/backups/game-of-the-day)
#   GAME_OFFSITE_RECIPIENT  age public key; default: derived from age.key
#   GAME_OFFSITE_KEEP       copies kept in the branch tip                (30; history keeps older ones)
set -euo pipefail
umask 077

REPO=${GAME_OFFSITE_REPO:-}
BRANCH=${GAME_OFFSITE_BRANCH:-db-backups}
STATE=${GAME_OFFSITE_DIR:-/var/lib/game-of-the-day/offsite}
KEEP=${GAME_OFFSITE_KEEP:-30}
IDENTITY="$STATE/age.key"
SSH_KEY=${GAME_OFFSITE_SSH_KEY:-$STATE/deploy_key}
LOCAL=${GAME_BACKUP_DIR:-/var/backups/game-of-the-day}
KNOWN_HOSTS="$(cd "$(dirname "$0")" && pwd)/github_known_hosts"
WORK=""

log() { printf '%s offsite: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
cleanup() { [ -n "$WORK" ] && rm -rf -- "$WORK"; return 0; }
trap cleanup EXIT

[ -n "$REPO" ] || die "GAME_OFFSITE_REPO is not set"
case "$KEEP" in ''|*[!0-9]*|0) die "GAME_OFFSITE_KEEP must be a positive integer" ;; esac
for tool in age git gzip sqlite3; do command -v "$tool" > /dev/null || die "$tool is not installed"; done
[ -r "$SSH_KEY" ] || die "SSH key missing: $SSH_KEY"
[ -r "$KNOWN_HOSTS" ] || die "pinned host keys missing: $KNOWN_HOSTS"

export GIT_SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS"
export GIT_TERMINAL_PROMPT=0
git_() { git -c user.name=gotd-backup -c user.email=gotd-backup@localhost -c init.defaultBranch="$BRANCH" "$@"; }

recipient() {
    if [ -n "${GAME_OFFSITE_RECIPIENT:-}" ]; then
        printf '%s\n' "$GAME_OFFSITE_RECIPIENT"
    else
        [ -r "$IDENTITY" ] || die "no GAME_OFFSITE_RECIPIENT and no identity at $IDENTITY"
        age-keygen -y "$IDENTITY"
    fi
}

check_db() { # plain sqlite file -> prints a summary or dies
    local ic games
    ic=$(sqlite3 "$1" "PRAGMA integrity_check;") || die "integrity_check could not run"
    [ "$ic" = "ok" ] || die "integrity_check failed: $(printf '%s' "$ic" | head -c 200)"
    games=$(sqlite3 "$1" "SELECT count(*) FROM games;") || die "no games table"
    printf 'integrity ok, %s games' "$games"
}

push() {
    local src=$1 name enc remote_has
    [ -s "$src" ] || die "backup file missing or empty: $src"
    gzip -t "$src" || die "not a valid gzip: $src"
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/offsite.XXXXXX")
    name="$(basename "$src").age"
    enc="$WORK/$name"

    age -r "$(recipient)" -o "$enc" "$src" || die "encryption failed"
    # Prove the copy is restorable before it replaces anything remote.
    if [ -r "$IDENTITY" ]; then
        age -d -i "$IDENTITY" "$enc" | cmp -s - "$src" || die "decryption round trip does not match the backup"
    fi

    git_ init -q "$WORK/repo"
    git_ -C "$WORK/repo" remote add origin "$REPO"
    remote_has=$(git_ -C "$WORK/repo" ls-remote --heads origin "$BRANCH") || die "cannot reach $REPO"
    if [ -n "$remote_has" ]; then
        git_ -C "$WORK/repo" fetch -q --depth=1 origin "$BRANCH"
        git_ -C "$WORK/repo" checkout -q -B "$BRANCH" FETCH_HEAD
    else
        git_ -C "$WORK/repo" checkout -q --orphan "$BRANCH"
        printf '%s\n' "Encrypted SQLite backups of Game of the Day (age). Written by ops/backup/offsite-git.sh." > "$WORK/repo/README"
        git_ -C "$WORK/repo" add README
    fi
    mkdir -p "$WORK/repo/backups"
    mv "$enc" "$WORK/repo/backups/$name"
    git_ -C "$WORK/repo" add "backups/$name"
    # Keep the newest KEEP copies in the tip; names sort chronologically.
    ls -1 "$WORK/repo/backups" | sort -r | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
        git_ -C "$WORK/repo" rm -q "backups/$old"
    done
    git_ -C "$WORK/repo" commit -q -m "backup $name"
    git_ -C "$WORK/repo" push -q origin "$BRANCH" || die "push failed"
    log "pushed $name"
}

fetch_latest() { # -> path of the decrypted, verified plain database inside WORK
    [ -r "$IDENTITY" ] || die "identity missing: $IDENTITY (restore it from your offline copy)"
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/offsite.XXXXXX")
    git_ clone -q --depth=1 --branch "$BRANCH" "$REPO" "$WORK/repo" || die "cannot clone $REPO ($BRANCH)"
    local latest
    latest=$(ls -1 "$WORK/repo/backups" 2>/dev/null | sort -r | head -n 1)
    [ -n "$latest" ] || die "no backups in $BRANCH"
    age -d -i "$IDENTITY" "$WORK/repo/backups/$latest" | gzip -dc > "$WORK/restored.db" || die "cannot decrypt $latest"
    log "$latest: $(check_db "$WORK/restored.db")" >&2
    printf '%s\n' "$WORK/restored.db"
}

case "${1:-}" in
    verify)
        fetch_latest > /dev/null
        ;;
    push-latest)
        [ -r "$LOCAL/.last-local-ok" ] || die "no successful local backup recorded in $LOCAL"
        read -r _ latest_name < "$LOCAL/.last-local-ok" || true
        [ -n "${latest_name:-}" ] || die "local manifest names no backup"
        push "$LOCAL/$latest_name"
        ;;
    restore)
        target=${2:-}
        [ -n "$target" ] || die "usage: $0 restore <target.db>"
        [ ! -e "$target" ] || die "$target exists; move it away first (nothing was overwritten)"
        plain=$(fetch_latest)
        install -m 640 "$plain" "$target"
        log "restored to $target"
        ;;
    ''|-h|--help)
        die "usage: $0 <backup.db.gz> | push-latest | verify | restore <target.db>"
        ;;
    *)
        push "$1"
        ;;
esac
