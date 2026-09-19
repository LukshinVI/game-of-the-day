# SQLite backup

Daily online backup of the production database, with a separate freshness check and failure alerts.

| File | Purpose |
|---|---|
| `game-backup.sh run` | snapshot (`sqlite3 .backup`) -> `integrity_check` -> checkpoint -> gzip -> `gzip -t` -> atomic publish -> `.last-local-ok` -> optional off-site step -> retention |
| `game-backup.sh check` | fail unless the newest backup exists, is non-empty, is a valid gzip and is younger than 26h |
| `game-backup.{service,timer}` | 03:30 Moscow time, `Persistent=true`, sandboxed |
| `game-backup-check.{service,timer}` | 12:00 Moscow time freshness check (catches a timer that never fired) |
| `game-backup-alert@.service` | `OnFailure=` target; sends a critical message through `src/ops/notifier.py` as the service user (never root) |

Design rules: the live file is never copied; retention runs only after every earlier step succeeded (including the optional off-site step configured with `GAME_BACKUP_OFFSITE_CMD`); a failed run publishes nothing and leaves no temporary files; the shell never decides alerting on its own (`OnFailure=` does).

Scope of this first version: local copy on the same disk. It protects against corruption and mistakes, **not** against losing the host. The encrypted off-host copy is the next step (issue #37).

## Install (needs root; the unit files assume the database owner is `gotd`)

```bash
cd /srv/game-of-the-day && git fetch origin && git merge --ff-only origin/main
sudo install -d -m 0700 -o gotd -g gotd /var/backups/game-of-the-day
sudo install -m 0644 ops/backup/game-backup.service ops/backup/game-backup.timer \
  ops/backup/game-backup-check.service ops/backup/game-backup-check.timer \
  'ops/backup/game-backup-alert@.service' /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/game-backup.service /etc/systemd/system/game-backup-check.service
sudo systemctl daemon-reload
```

## Verify before enabling the timers

```bash
sudo systemctl start game-backup.service && journalctl -u game-backup.service -n 20 --no-pager
ls -l /var/backups/game-of-the-day            # one game-*.db.gz, mode 0600, plus .last-local-ok
sudo systemctl start game-backup-check.service && journalctl -u game-backup-check.service -n 5 --no-pager
```

Forced-failure test of the alert path (a Telegram message must arrive):

```bash
sudo systemd-run --wait --unit=game-backup-failtest --property=OnFailure=game-backup-alert@%n.service /bin/false
```

Enable:

```bash
sudo systemctl enable --now game-backup.timer game-backup-check.timer
systemctl list-timers 'game-backup*'
```

## Restore test (do this once, then periodically)

```bash
gunzip -c /var/backups/game-of-the-day/game-<STAMP>.db.gz > /tmp/restore-test.db
sqlite3 /tmp/restore-test.db 'PRAGMA integrity_check;'   # must print: ok
rm /tmp/restore-test.db
```

To restore for real: stop the writers first (feedback API, consumer, preparation and delivery timers), keep the damaged files aside, then copy the decompressed file into place with the original owner and mode.

## Rollback

```bash
sudo systemctl disable --now game-backup.timer game-backup-check.timer
sudo rm /etc/systemd/system/game-backup*.service /etc/systemd/system/game-backup*.timer
sudo systemctl daemon-reload
```

Existing backups stay in `/var/backups/game-of-the-day`.

## Tests

`python3 -m unittest tests.test_backup_script` runs the script against temporary databases: consistency under a concurrent writer, corrupt source, missing database, low disk space, stale temp files, retention with a minimum count, a failing off-site step, and every `check` failure mode.
