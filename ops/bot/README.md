# Manual delivery through the Telegram bot

Owner-only control for the case where the daily delivery did not happen and someone has to send the game by hand. It replaces logging into the host and running the CLI, which cannot work reliably: the messaging credentials are only available to systemd units, and a manual run without them marks today's delivery as `failed`.

| Command | What it does |
|---|---|
| `/status` | shows today's delivery state (`game_delivery.py status`) |
| `/send` | shows the state and a one-time four-digit code, valid for two minutes; sends nothing |
| `/send <code>` | runs `game_delivery.py send-today --retry-failed`, then shows the state again to confirm it is `sent` |

## Security model
- Only a **private chat with the owner** is served: the chat id and the sender id must both equal `TELEGRAM_CHAT_ID`. Everything else is ignored silently (no reply, nothing runs). If that variable is a group id the bot will not answer; use the private chat id.
- Messages older than a minute are ignored, so a backlog after a restart cannot trigger a send. A code is single-use and expires.
- The bot only runs fixed argument lists of the delivery CLI; no user text reaches a command line. It never builds or sends game messages itself, so the state machine and idempotency stay in `src/delivery/game_delivery.py`.
- Outbound long polling only: no inbound port, no webhook. Logs contain exception types, never URLs or tokens.
- `--retry-failed` reuses the same VK `random_id` (derived from the delivery's idempotency key), so a retry after an ambiguous failure is deduplicated by VK.

## Install (needs root)
```bash
cd /srv/game-of-the-day && git log -1 --oneline        # the reviewed commit
sudo install -m 0644 ops/bot/game-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now game-bot.service
systemctl is-active game-bot.service
```
The bot sends "Бот ручной отправки запущен" to the owner when it starts. Then send `/status` from the private chat.

## Rollback
```bash
sudo systemctl disable --now game-bot.service
sudo rm /etc/systemd/system/game-bot.service && sudo systemctl daemon-reload
```

## Not included yet
Preparing a game (`/prepare`) needs the model CLI and its credentials, which the sandbox of this unit does not provide; it would need a separately reviewed change.

## Tests
`python3 -m unittest tests.test_manual_bot tests.test_game_delivery`
