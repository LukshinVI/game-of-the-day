# Daily outcome check

Independent check that today's game actually reached VK. Process alerts (preparation
failed, delivery failed) say "this step failed"; nothing said "the day's result was
not achieved" — on 2026-09-20 and 2026-09-21 the preparation alert fired both days,
but the missed game went unnoticed for two days because nothing checked the outcome
itself. This closes that gap.

## What it does

`src/ops/day_outcome_check.py` reads (read-only, `PRAGMA query_only`) today's row in
`deliveries` and reports one of:

| Situation | Result |
|---|---|
| Exactly one delivery for today, `status = sent`, has a VK message id | met — silent, exit 0 |
| No game for today | not met — alert, exit 1 |
| `pending` / `sending` / `failed` | not met — alert with the status, attempts and error, exit 1 |
| `sent` but no VK message id | not met — inconsistent state, alert, exit 1 |
| More than one delivery for today | not met — needs a manual look, alert, exit 1 |
| Called before `--not-before` (default 10:15 MSK) | too early to judge — silent, exit 0 |
| Database unreadable, or the alert itself could not be sent | exit 2 |

It is deliberately separate from preparation and delivery: a bug in either of those
does not stop this from noticing the outcome. It cannot notice that the whole host is
down — that needs an external heartbeat, which is out of scope here.

## Install (needs root)

```bash
cd /srv/game-of-the-day && git log -1 --oneline
sudo install -m 0644 ops/day-outcome/game-day-outcome.service ops/day-outcome/game-day-outcome.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## Verify before enabling

```bash
sudo systemctl start game-day-outcome.service && sudo journalctl -u game-day-outcome.service -n 10 --no-pager
```

Expect `OK: сегодняшняя игра доставлена.` on a normal day, or `Слишком рано для проверки` before 10:15 MSK.

Forced-failure test (checks a date with no game — safe, it only reads):

```bash
/usr/bin/python3 /srv/game-of-the-day/src/ops/day_outcome_check.py --not-before 00:00
```
On a day the game has already been sent this prints `OK`; the alert path itself was already exercised live during the incident recovery on 2026-09-21 (`send-today --retry-failed`) and via the backup package's `OnFailure` test.

Enable the timer:

```bash
sudo systemctl enable --now game-day-outcome.timer
systemctl list-timers 'game-day-outcome*'
```

## Rollback

```bash
sudo systemctl disable --now game-day-outcome.timer
sudo rm /etc/systemd/system/game-day-outcome.service /etc/systemd/system/game-day-outcome.timer
sudo systemctl daemon-reload
```

## Not included

An external heartbeat (something outside this host confirming the host itself is up) and a check that the *legacy* Google path is silent (relevant once #32's exit criteria are being tracked).

## Tests

`python3 -m unittest tests.test_day_outcome_check`
