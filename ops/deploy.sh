#!/usr/bin/env bash
# One path to production for Game of the Day.
#
#   ops/deploy.sh           backup -> fast-forward to origin/main -> tests -> migrate
#                           -> install changed units -> restart long-running services
#   ops/deploy.sh --check   show what would be deployed, change nothing
#
# Safe by construction:
# - fast-forward only: a diverged checkout stops the deploy;
# - the database is backed up before anything changes;
# - the test suite runs on the new code BEFORE the migration; a failure rolls the code
#   back to the previous commit and leaves the database untouched;
# - migrations are additive, so the previous code keeps working on the migrated schema;
# - new units are installed but never enabled automatically: that stays a decision.
set -euo pipefail

APP=${GAME_APP_DIR:-/srv/game-of-the-day}
DB="$APP/data/game-of-the-day.db"
BACKUPS=${GAME_BACKUP_DIR:-/var/backups/game-of-the-day}
USER_NAME=${GAME_USER:-gotd}
SERVICES=(game-bot.service game-product-bot.service game-feedback-consumer.service)
G=(git -c "safe.directory=$APP" -C "$APP")

log() { printf '%s deploy: %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"
exec 9> /run/game-deploy.lock
flock -n 9 || die "another deploy is running"

"${G[@]}" fetch -q origin
old=$("${G[@]}" rev-parse HEAD)
new=$("${G[@]}" rev-parse origin/main)
if [ "$old" = "$new" ]; then
    log "already at $(git -c "safe.directory=$APP" -C "$APP" log --oneline -1)"
    exit 0
fi
"${G[@]}" merge-base --is-ancestor "$old" "$new" || die "origin/main is not a fast-forward of the checkout"
log "pending:"
"${G[@]}" log --oneline "$old..$new"
[ "${1:-}" = "--check" ] && exit 0

stamp=$(date +%Y%m%d%H%M%S)
sqlite3 "$DB" ".backup '$BACKUPS/pre-deploy-$stamp.db'"
log "database backed up: pre-deploy-$stamp.db"

"${G[@]}" merge -q --ff-only origin/main
chown -R "$USER_NAME" "$APP"   # owner only: data/ keeps its shared group

# The log is written by root on purpose; the tests run as the application user.
# shellcheck disable=SC2024
if ! sudo -u "$USER_NAME" env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$APP/tests" > "/tmp/deploy-tests-$stamp.log" 2>&1; then
    "${G[@]}" reset -q --hard "$old"
    chown -R "$USER_NAME" "$APP"
    tail -n 20 "/tmp/deploy-tests-$stamp.log" >&2
    die "tests failed on $new; rolled back to $old, database untouched"
fi
log "tests passed: $(grep -Eo 'Ran [0-9]+ tests' "/tmp/deploy-tests-$stamp.log")"
rm -f "/tmp/deploy-tests-$stamp.log"

sudo -u "$USER_NAME" python3 "$APP/src/db/children.py" migrate
[ "$(sqlite3 "$DB" 'PRAGMA integrity_check;')" = "ok" ] || die "integrity_check failed after migration (backup: pre-deploy-$stamp.db)"

changed_units=()
new_units=()
while IFS= read -r unit; do
    name=$(basename "$unit")
    target="/etc/systemd/system/$name"
    if [ ! -e "$target" ]; then
        new_units+=("$name")
    elif ! cmp -s "$unit" "$target"; then
        install -m 644 "$unit" "$target"
        changed_units+=("$name")
    fi
done < <(find "$APP/ops" -name '*.service' -o -name '*.timer')
for name in "${new_units[@]}"; do
    install -m 644 "$(find "$APP/ops" -name "$name" | head -n 1)" "/etc/systemd/system/$name"
done
systemctl daemon-reload
[ ${#changed_units[@]} -eq 0 ] || log "updated units: ${changed_units[*]}"
[ ${#new_units[@]} -eq 0 ] || log "NEW units installed, not enabled: ${new_units[*]}"

for service in "${SERVICES[@]}"; do
    if systemctl is-enabled --quiet "$service" 2>/dev/null; then
        systemctl restart "$service"
    fi
done
sleep 3
failed=()
for service in "${SERVICES[@]}"; do
    if systemctl is-enabled --quiet "$service" 2>/dev/null && ! systemctl is-active --quiet "$service"; then
        failed+=("$service")
    fi
done
[ ${#failed[@]} -eq 0 ] || die "not running after restart: ${failed[*]} (previous code: $old)"

log "deployed $("${G[@]}" log --oneline -1)"
