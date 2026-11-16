# Deployment

The repository is deployable as is. Everything host- or account-specific comes
from configuration, never from source.

## Host runtime

Conventions the unit files assume:

| | value |
|---|---|
| service user / group | `gotd` (a dedicated system user, no login shell) |
| checkout | `/srv/game-of-the-day` |
| runtime data (SQLite) | `/srv/game-of-the-day/data` |
| backups | `/var/backups/game-of-the-day` |
| service state (model CLI config/cache) | `/var/lib/game-of-the-day` (`StateDirectory=`) |
| secrets | root-owned `EnvironmentFile`s in `/etc/game-of-the-day/` (mode 0600), see below |

Fresh host:

```bash
sudo apt-get install -y git sqlite3 python3-pil   # Pillow: album of the month
sudo useradd --system --home-dir /var/lib/game-of-the-day --no-create-home --shell /usr/sbin/nologin gotd
sudo git clone https://github.com/LukshinVI/game-of-the-day.git /srv/game-of-the-day
sudo install -d -o gotd -g gotd -m 0750 /srv/game-of-the-day/data
sudo -u gotd sqlite3 /srv/game-of-the-day/data/game-of-the-day.db < /srv/game-of-the-day/src/db/schema.sql
```

The checkout stays root-owned: the service user can write only `data/`.

### Secrets

systemd reads these as root before dropping privileges; each unit loads only
what it needs. Variable names are listed in [`.env.example`](../.env.example).

| file | used by | contents |
|---|---|---|
| `game.env` | preparation, delivery | `CHILD_BIRTH_DATE`, `VK_TOKEN` (community key with Messages and Photos), `VK_PEER_ID` |
| `features.env` (optional) | delivery, digest, preparation, insights, feedback consumer, product bot | feature switches only, no secrets: `GAME_CARDS=1`, `GAME_HOLIDAYS=1`, `GAME_BUTTONS=1`, `GAME_MONTH_THEME=1`, `GAME_VOICE=1`, `GAME_FEEDBACK_CONTEXT=1`, `GAME_DIGEST_PULSE=1`, `GAME_ALBUM=1`. One file, so a release switches them on together and one line switches one off |
| `preparation.env` | preparation | `PREPARATION_BACKEND`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |
| `feedback.env` | feedback consumer | `CF_ACCOUNT_ID`, `CF_STORAGE_QUEUE_ID`, `CF_QUEUE_API_TOKEN` |
| `ops-bot.env` | alerts, ops bot | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `watch.env` (optional) | heartbeat, feedback consumer | `WATCH_URL` (the Worker), `WATCH_SECRET` (the same value as the Worker secret) |
| `voice.env` (optional) | feedback consumer, product bot | `CF_AI_TOKEN` (Workers AI), `CF_ACCOUNT_ID`; the switch itself is `GAME_VOICE=1` in `features.env` |
| `offsite.env` | off-site backup units | see [the backup runbook](../ops/backup/README.md) |
| `worker-deploy.env` (owner's host only) | deploying the Worker by hand | a Cloudflare token for `wrangler deploy` |

### Children and recipients

With no children configured the service runs in single-child mode
(`CHILD_BIRTH_DATE`, `VK_PEER_ID`). To serve several children or the Telegram
channel, migrate once and describe them in the database:

```bash
cd /srv/game-of-the-day
sudo -u gotd python3 src/db/children.py migrate
sudo -u gotd python3 src/db/children.py add --name "Старший" --birth-date 2025-09-01 --adopt-existing
sudo -u gotd python3 src/db/children.py add-recipient --child 1 --channel vk --address <peer_id>
sudo -u gotd python3 src/db/children.py add --name "Младший" --birth-date <YYYY-MM-DD>
sudo -u gotd python3 src/db/children.py add-recipient --child 2 --channel telegram --address <chat_id>
sudo -u gotd python3 src/db/children.py list
```

Each child gets its own game every day (own age, own history); each recipient
gets its own delivery with its own state. Telegram delivery needs
`GAME_TELEGRAM_BOT_TOKEN` in `product-bot.env`: a separate product bot, not the ops bot.

Telegram families normally register themselves: the owner sends `/invite` to the
product bot and forwards the one-time link; the family enters the child's name and
birth date and confirms the age. See [the product bot runbook](../ops/product-bot/README.md).
The commands above remain for VK recipients and manual fixes.

### Model CLI

Preparation calls `claude` (default backend) or `codex` from `PATH`. Install the
binary system-wide (for example `/usr/local/bin/claude`); a per-user install under
root's home is not visible to the service user. Pin and update it deliberately:
preparation depends on its `--json-schema` structured output.

### Deploying a new version

```bash
sudo /srv/game-of-the-day/ops/deploy.sh --check   # what would change
sudo /srv/game-of-the-day/ops/deploy.sh           # backup, fast-forward, tests, migrate, units, restart
```

The script refuses a non-fast-forward, backs the database up first, runs the test suite
on the new code before migrating (a failure rolls the code back and leaves the database
untouched), installs changed units, installs new ones without enabling them, and checks
that the long-running services are up afterwards.

### Units

| unit | schedule |
|---|---|
| `ops/preparation/game-preparation.{service,timer}` | 07:00, retries 08:00 and 09:00 MSK (idempotent) |
| `ops/preparation/game-reserve.{service,timer}` | Sundays 04:30 MSK: top up the warm reserve of validated games |
| `ops/delivery/game-digest.{service,timer}` | Sundays 19:00 MSK: digest for parents |
| `ops/delivery/game-album.{service,timer}` | the 1st, 10:15 MSK: album of the month (`GAME_ALBUM=1`); needs `python3-pil` on the host |
| `ops/delivery/game-delivery.{service,timer}` | 10:00 MSK, catch-up 10:12 |
| `ops/delivery/game-delivery-retry.service` | manual, via the bot |
| `ops/feedback/game-feedback-consumer.service` | always on |
| `ops/feedback/game-feedback-insights.{service,timer}` | every 2 minutes: observations, child profile, reply (3 quiet minutes after the last message) |
| `ops/day-outcome/game-day-outcome.{service,timer}` | 10:15, 11:15, 13:15, 17:15 MSK |
| `ops/backup/game-backup*.{service,timer}` | 03:30 backup, 12:00 freshness check |
| `ops/backup/game-offsite-{push,verify}.{service,timer}` | 03:45 encrypted off-site copy; Sundays 05:00 restore test |
| `ops/watch/game-heartbeat.{service,timer}` | every 5 minutes: the pulse for the watcher in the Worker |
| `ops/watch/game-slo-watch.{service,timer}` | 17:25 MSK: SLO alert on a change of budget level |
| `ops/retention/game-retention.{service,timer}` | 03:10 MSK: retention terms ([privacy](privacy.md)) |
| `ops/bot/game-bot.service` | always on (owner only) |
| `ops/llm-report/game-llm-report.{service,timer}` | Mondays 10:30 MSK: model report to the owner |
| `ops/product-bot/game-product-bot.service` | always on (families; optional; runs as `gotd-bot`, see its runbook) |

Then install the units following each runbook:
[backup](../ops/backup/README.md), [manual delivery bot](../ops/bot/README.md),
[daily outcome check](../ops/day-outcome/README.md), [product bot](../ops/product-bot/README.md).

### Using a different service user

If an existing host runs the service under another account, do not edit the
tracked unit files. Override the user with a drop-in per unit, which survives
reinstalling units from the repository:

```bash
sudo systemctl edit game-delivery.service     # repeat for each game-* unit
# [Service]
# User=<user>
# Group=<group>
```

The polkit rule (`ops/bot/50-game-of-the-day.rules`) checks the user by name;
install a host-local copy with the right name instead of the tracked one.

## Cloudflare Worker

Secrets are set once per account and are not in the repository:

```bash
cd worker
npx wrangler secret put VK_GROUP_ID          # numeric community id
npx wrangler secret put VK_CALLBACK_SECRET
npx wrangler secret put VK_CONFIRMATION_CODE
npx wrangler secret put VK_TEST_PEER_ID     # only labels test messages in the logs
npx wrangler secret put WATCH_SECRET        # the same value as in watch.env on the host
npx wrangler secret put OPS_BOT_TOKEN       # the ops bot, for the watcher's alerts
npx wrangler secret put OPS_CHAT_ID
npx wrangler deploy
```

`VK_GROUP_ID` must be set **before** deploying: without it the worker rejects
every callback (fail closed), so feedback stops arriving instead of being
accepted from an unverified source.

Deploy the **host first**, then the Worker. The Worker passes every private VK dialog
with the sender's peer id (`address`); a host older than schema 12 would ignore the
address and store a stranger's message as feedback on the latest VK game. The host
accepts events with and without an address, so the order host → Worker is safe.

The watcher (`worker/src/watch.js`) needs the host to know the canary first (schema 22
and the consumer that drops `canary-v1`): deploy the host, then the Worker, then enable
`game-heartbeat.timer`. Until the first beat the Worker checks nothing.
