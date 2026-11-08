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
| secrets | root-owned `EnvironmentFile`, variables as in [`.env.example`](../.env.example) |

Fresh host:

```bash
sudo useradd --system --home-dir /srv/game-of-the-day --shell /usr/sbin/nologin gotd
sudo git clone https://github.com/LukshinVI/game-of-the-day.git /srv/game-of-the-day
sudo chown -R gotd:gotd /srv/game-of-the-day
```

Then install the units following each runbook:
[backup](../ops/backup/README.md), [manual delivery bot](../ops/bot/README.md),
[daily outcome check](../ops/day-outcome/README.md).

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
npx wrangler secret put VK_PROD_PEER_ID
npx wrangler secret put VK_TEST_PEER_ID
npx wrangler deploy
```

`VK_GROUP_ID` must be set **before** deploying: without it the worker rejects
every callback (fail closed), so feedback stops arriving instead of being
accepted from an unverified source.
