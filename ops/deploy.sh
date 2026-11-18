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
# - a service that does not come back after the restart rolls the code and the changed
#   units back to the previous commit and restarts again (the database stays migrated,
#   which the previous code tolerates); the owner is told either way;
# - a failed integrity check after the migration stops the deploy and tells the owner:
#   restoring a database automatically is a decision for a person, not for a script;
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
# The owner's ops bot; a failed alert never changes what the deploy does.
alert() {
    # shellcheck source=/dev/null
    ( set -a; . /etc/game-of-the-day/ops-bot.env 2>/dev/null; set +a
      python3 "$APP/src/ops/notifier.py" critical "$1" --details "$2" ) >/dev/null 2>&1 || true
}
not_running() {
    local service
    for service in "${SERVICES[@]}"; do
        if systemctl is-enabled --quiet "$service" 2>/dev/null && ! systemctl is-active --quiet "$service"; then
            printf '%s ' "$service"
        fi
    done
}

[ "$(id -u)" -eq 0 ] || die "run as root"
exec 9> /run/game-deploy.lock
flock -n 9 || die "another deploy is running"
# The tests run as the application user, which cannot enter root's home: never
# inherit the caller's working directory.
cd "$APP"

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
if [ "$(sqlite3 "$DB" 'PRAGMA integrity_check;')" != "ok" ]; then
    alert "Деплой остановлен: база не прошла проверку целостности" \
        "После миграции integrity_check не ok. Код новый, сервисы не перезапускались. Копия до деплоя: pre-deploy-$stamp.db"
    die "integrity_check failed after migration (backup: pre-deploy-$stamp.db)"
fi

changed_units=()
new_units=()
previous_units=$(mktemp -d "/tmp/deploy-units-$stamp.XXXX")
while IFS= read -r unit; do
    name=$(basename "$unit")
    target="/etc/systemd/system/$name"
    if [ ! -e "$target" ]; then
        new_units+=("$name")
    elif ! cmp -s "$unit" "$target"; then
        cp -p "$target" "$previous_units/$name"
        install -m 644 "$unit" "$target"
        changed_units+=("$name")
    fi
done < <(find "$APP/ops" -name '*.service' -o -name '*.timer' -o -name '*.slice')
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
failed=$(not_running)
if [ -n "$failed" ]; then
    log "not running after restart: $failed; rolling back to $old"
    "${G[@]}" reset -q --hard "$old"
    chown -R "$USER_NAME" "$APP"
    for saved in "$previous_units"/*; do
        [ -e "$saved" ] && install -m 644 "$saved" "/etc/systemd/system/$(basename "$saved")"
    done
    rm -rf "$previous_units"
    systemctl daemon-reload
    for service in "${SERVICES[@]}"; do
        if systemctl is-enabled --quiet "$service" 2>/dev/null; then
            systemctl restart "$service"
        fi
    done
    sleep 3
    still=$(not_running)
    if [ -z "$still" ]; then
        alert "Деплой откатан" \
            "После рестарта не поднялись: $failed. Код и юниты возвращены к $old, сервисы работают. База осталась после миграции (миграции аддитивные). Новая версия: $new"
        die "rolled back to $old; services are running on the previous code"
    fi
    alert "Деплой откатан, но сервисы не работают" \
        "Не поднялись на новой версии: $failed. После отката к $old всё ещё лежат: $still. Нужен человек: journalctl -u <служба>"
    die "rolled back to $old, still not running: $still"
fi
rm -rf "$previous_units"

log "deployed $("${G[@]}" log --oneline -1)"
