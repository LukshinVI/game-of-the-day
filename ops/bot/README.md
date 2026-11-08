# Manual delivery through the Telegram bot

Owner-only control for the case where the daily delivery did not happen and someone has to send the game by hand. It replaces logging into the host and running the CLI, which cannot work: the messaging credentials exist only in systemd units, and a manual run without them marks today's delivery as `failed`.

| Command | Works when today's delivery is | What it does |
|---|---|---|
| `/status` | any | shows the delivery status |
| `/send`, then `/send <code>` | `pending` | one-time code (2 minutes), then starts `game-delivery.service` and shows the status again |
| `/retry`, then `/retry <code>` | `failed` | one-time code, then starts `game-delivery-retry.service` (`send-today --retry-failed`) and shows the status again |
| `/users` | any | read-only report of product-bot families: children and ages, active or paused, last delivered game, feedback in 7 days, today's failures |

In every other state the bot only explains why it does nothing (already sent, no game, stuck in `sending`, ambiguous). The state is checked again when the code is used. Success is confirmed by the status after the run, not by an exit code.

## Design
- **Trust split.** The bot holds only the Telegram credentials. It never sees the messaging credentials: it asks systemd to start one of two fixed oneshot units, which carry those credentials themselves. A long-running process can be read by any process of the same user through `/proc`, a delivery unit exposes the credentials for seconds.
- **Polkit rule** (`50-game-of-the-day.rules`): lets the service user start exactly `game-delivery.service` and `game-delivery-retry.service`. Nothing else: no other unit, no stop/restart. Note that this also lets any process of that user start those two units, which is the same effect the daily timer has.
- **Authorisation.** Only a private chat whose id and sender id both equal `TELEGRAM_CHAT_ID`; everything else is ignored silently. If that variable is a group id the bot will not answer; use the private chat id. Messages older than a minute are ignored, codes are single-use, expire, and are lost on restart. A wrong or expired code invalidates the challenge.
- **Retry safety.** `--retry-failed` only retries a `failed` delivery (a `pending` one is refused) and reuses the same VK `random_id`, so an ambiguous earlier attempt is deduplicated by VK. A retry is a separate command with a separate confirmation, after the failure reason has been shown by `/status`.
- **Liveness.** Outbound long polling only, no inbound port. If Telegram cannot be reached for 10 minutes the bot exits so that systemd restarts it. An alert cannot help when Telegram itself is the problem, so this does not replace an independent daily-outcome check (#45).

## Install (needs root; the unit files assume the service user `gotd`)
```bash
cd /srv/game-of-the-day && git log -1 --oneline        # the reviewed commit
sudo install -m 0644 ops/delivery/game-delivery-retry.service ops/bot/game-bot.service /etc/systemd/system/
sudo install -m 0644 ops/bot/50-game-of-the-day.rules /etc/polkit-1/rules.d/
sudo systemctl daemon-reload
sudo systemctl enable --now game-bot.service
systemctl is-active game-bot.service
```
The bot sends "Бот ручной отправки запущен" to the owner when it starts. Then send `/status` from the private chat. Do not start `game-delivery-retry.service` by hand to test: it retries a real failed delivery.

## Rollback
```bash
sudo systemctl disable --now game-bot.service
sudo rm /etc/systemd/system/game-bot.service /etc/systemd/system/game-delivery-retry.service /etc/polkit-1/rules.d/50-game-of-the-day.rules
sudo systemctl daemon-reload
```

## Not included
Preparing a game (`/prepare`) is deliberately not part of this bot: it calls the model, has other credentials, creates state and runs the safety validation. If it is ever needed it should be a separate, separately reviewed unit with its own confirmation.

## Tests
`python3 -m unittest tests.test_manual_bot tests.test_game_delivery`

## Product-bot events

The bot also forwards owner-facing events that the product bot (`ops/product-bot`) writes
to `user_events`: a new member, a new child, pause/resume, a reached limit, a block. They
are marked as forwarded only after Telegram accepts the message (at-least-once).
