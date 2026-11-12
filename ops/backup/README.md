# SQLite backup

Daily online backup of the production database, with a separate freshness check and failure alerts.

| File | Purpose |
|---|---|
| `game-backup.sh run` | snapshot (`sqlite3 .backup`) -> `integrity_check` -> checkpoint -> gzip -> `gzip -t` -> atomic publish -> manifest `.last-local-ok` (`<epoch> <file>`, replaced atomically) -> optional off-site step -> retention; a `flock` refuses a second concurrent run |
| `game-backup.sh check` | fail unless the file named in the manifest exists, is a valid gzip and the manifest is younger than 26h (touching files cannot fake freshness) |
| `game-backup.{service,timer}` | 03:30 Moscow time, `Persistent=true`, sandboxed |
| `game-backup-check.{service,timer}` | 12:00 Moscow time freshness check (catches a timer that never fired) |
| `game-backup-alert@.service` | `OnFailure=` target; sends a critical message in Russian ("Сбой службы: <unit>") through `src/ops/notifier.py` as the service user (never root) |

Design rules: the live file is never copied; retention runs only after every earlier step succeeded (including the optional off-site step configured with `GAME_BACKUP_OFFSITE_CMD`); a failed run publishes nothing and leaves no temporary files; the shell never decides alerting on its own (`OnFailure=` does).

Scope of this first version: local copy on the same disk. It protects against corruption and mistakes, **not** against losing the host. The encrypted off-host copy is the next step (issue #37).

## Install (needs root; the unit files assume the database owner is `gotd`)

Install from the exact reviewed commit, not from a moving branch: after PR #40 is merged, take its merge commit SHA.

```bash
cd /srv/game-of-the-day && git fetch origin && git merge --ff-only <MERGE_COMMIT_SHA>
git log -1 --oneline                          # must show that commit
sudo install -d -m 0700 -o gotd -g gotd /var/backups/game-of-the-day
sudo install -m 0644 ops/backup/game-backup.service ops/backup/game-backup.timer \
  ops/backup/game-backup-check.service ops/backup/game-backup-check.timer \
  'ops/backup/game-backup-alert@.service' /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/game-backup.service /etc/systemd/system/game-backup-check.service
sudo systemctl daemon-reload
```

## Verify before enabling the timers

```bash
sudo systemctl start game-backup.service && sudo journalctl -u game-backup.service -n 20 --no-pager
ls -la /var/backups/game-of-the-day           # one game-*.db.gz (mode 0600) plus the hidden .last-local-ok manifest
sudo systemctl start game-backup-check.service && sudo journalctl -u game-backup-check.service -n 5 --no-pager
```

Forced-failure tests (two different things):

```bash
# 1. the real failure path: a missing database must fail loudly and change nothing
GAME_DB=/definitely/missing.db /srv/game-of-the-day/ops/backup/game-backup.sh run; echo "exit=$?"   # exit=1
ls -la /var/backups/game-of-the-day             # same files as before, no .tmp-* left

# 2. the alert wiring: OnFailure must reach Telegram (a message has to arrive). Use the literal unit
#    name: systemd-run does not expand the %n specifier that unit files support.
sudo systemd-run --wait --unit=game-backup-failtest --property=User=gotd \
  --property=OnFailure=game-backup-alert@game-backup-failtest.service /bin/false
```

Enable:

```bash
sudo systemctl enable --now game-backup.timer game-backup-check.timer
systemctl list-timers 'game-backup*'
```

## Restore test (do this once, then periodically)

```bash
gunzip -c /var/backups/game-of-the-day/game-<STAMP>.db.gz > /tmp/restore-test.db
sqlite3 /tmp/restore-test.db 'PRAGMA integrity_check;'    # must print: ok
sqlite3 /tmp/restore-test.db 'PRAGMA foreign_key_check;'  # should print nothing
sqlite3 /tmp/restore-test.db '.tables'                    # games, deliveries, feedback, ... must be present
sqlite3 /tmp/restore-test.db 'SELECT COUNT(*) FROM games;' # plausible, non-zero
rm /tmp/restore-test.db
```

To restore for real: stop the writers first (queue consumer, preparation and delivery timers), keep the damaged files aside, then copy the decompressed file into place with the original owner and mode.

## Verified on the production host (2026-09-19)

Installed from `04f4195`: first run and check succeeded under the sandboxed units, the `OnFailure` alert delivered a Telegram message as the service user, timers enabled (backup 02:30 CEST = 03:30 MSK, freshness check 11:00 CEST = 12:00 MSK). The service user cannot read the system journal, so use `sudo journalctl`.

## Rollback

```bash
sudo systemctl disable --now game-backup.timer game-backup-check.timer
sudo rm /etc/systemd/system/game-backup*.service /etc/systemd/system/game-backup*.timer
sudo systemctl daemon-reload
```

Existing backups stay in `/var/backups/game-of-the-day`.

## Tests

`python3 -m unittest tests.test_backup_script` runs the script against temporary databases: consistency under a concurrent writer, corrupt source, missing database, low disk space, invalid configuration, stale temp files, a refused concurrent run (needs `flock`), retention (minimum count, ordering by name), a failing off-site step, and every `check` failure mode including a touched-old-file and a malformed manifest.

## Off-site copy (encrypted, private Git repository)

`offsite-git.sh` encrypts the newest verified `.db.gz` with [age](https://age-encryption.org),
proves the round trip, and pushes the ciphertext to a branch of a private repository. Only
ciphertext leaves the host; GitHub's host keys are pinned (`github_known_hosts`). The branch
is one commit with no parents, force-pushed every night: it holds the copies of the last
`GAME_OFFSITE_MAX_DAYS` (14) days, at most `GAME_OFFSITE_KEEP` (14), and nothing older lives
in its history. This is what lets a deletion (docs/privacy.md) reach the off-site copies.
GitHub may keep unreachable objects until its own garbage collection; they are ciphertext.

| unit | when | what |
|---|---|---|
| `game-offsite-push.{service,timer}` | 03:45 MSK, after the 03:30 local backup | `push-latest` |
| `game-offsite-verify.{service,timer}` | Sundays 05:00 MSK | clone, decrypt the newest copy in a private temp dir, `integrity_check` |

Both run as **root**, not as the application user: the repository key may be broader than
one repository (a deploy key for the backup repository alone is better), and it must stay
out of reach of the bots, which run as the application user and face the public. Both alert
the owner on failure.

Setup (once, as root):

```bash
apt-get install -y age
d=/var/lib/game-of-the-day/offsite; install -d -m 700 -o gotd -g gotd $d
sudo -u gotd sh -c "umask 077; age-keygen -o $d/age.key"
# keep a copy of $d/age.key OFFLINE (password manager): without it nothing can be restored
cat > /etc/game-of-the-day/offsite.env <<ENV
GAME_OFFSITE_REPO=git@github.com:<owner>/<private-backup-repo>.git
GAME_OFFSITE_SSH_KEY=/root/.ssh/<key with write access to it>
ENV
chmod 600 /etc/game-of-the-day/offsite.env
install -m 644 ops/backup/game-offsite-{push,verify}.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now game-offsite-push.timer game-offsite-verify.timer
```

Restore on a new host (after putting `age.key` back into the state dir):

```bash
env $(cat /etc/game-of-the-day/offsite.env) \
  /srv/game-of-the-day/ops/backup/offsite-git.sh restore /srv/game-of-the-day/data/game-of-the-day.db
chown gotd:gotd /srv/game-of-the-day/data/game-of-the-day.db
```

`restore` never overwrites an existing database.

The backup repository also hosts the external watcher: a scheduled workflow (every 6 hours)
fails, and GitHub emails the owner, when the newest copy is older than 26 hours. A host that
is down, unreachable or not backing up is noticed from outside the host.
