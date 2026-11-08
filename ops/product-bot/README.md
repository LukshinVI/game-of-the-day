# Product bot: self-service onboarding

`src/bot/product_bot.py`, unit `game-product-bot.service`. The bot families talk to.
The owner's ops bot (`ops/bot`) stays private and is never shown to users.

## Setup (once)

1. Create a bot in @BotFather, then put the token on the host, not in a chat:

   ```bash
   install -m 600 /dev/null /etc/game-of-the-day/product-bot.env
   # GAME_TELEGRAM_BOT_TOKEN=...      the product bot token
   # GAME_BOT_OWNER_ID=...            the owner's Telegram user id (/invite, /members)
   ```

2. The delivery unit reads the same file (`EnvironmentFile=-…/product-bot.env`) to send
   games to Telegram recipients.
3. A separate identity for the public-facing process, sharing only the database:

   ```bash
   groupadd --system gotd-data
   useradd --system --no-create-home --shell /usr/sbin/nologin --user-group gotd-bot
   usermod -aG gotd-data gotd-bot
   chgrp gotd-data /srv/game-of-the-day/data /srv/game-of-the-day/data/game-of-the-day.db*
   chmod 2770 /srv/game-of-the-day/data && chmod 660 /srv/game-of-the-day/data/game-of-the-day.db
   ```

   SQLite gives the `-wal`/`-shm` files the database file's mode, and the setgid directory
   gives them its group, so both identities keep access.
4. `install -m 644 ops/product-bot/game-product-bot.service /etc/systemd/system/ &&
   systemctl daemon-reload && systemctl enable --now game-product-bot.service`.
   On start the bot applies the additive schema migration (`src/db/children.py`).

## Flow

```text
owner: /invite  ->  one-time link t.me/<bot>?start=<code>  (7 days, only the hash is stored)
user:  opens it ->  member  ->  child's name  ->  birth date  ->  "N months, right?"  ->  да
       -> children + recipients(telegram, chat) rows; picked up by the next 07:00 preparation
```

The invite opens with a short welcome that points to `/faq` (`src/bot/faq.txt`: what it
is, who picks the games, not medical advice, what data is kept and how to delete it).
Edit that file to change the wording; `/faq` in the middle of the questionnaire repeats
the pending question afterwards.

Member commands: `/faq`, `/children`, `/add_child`, `/pause`, `/resume`, `/cancel`, `/help`.
Any other text is feedback, stored with an explicit game id: a reply to a game message
goes to that game; plain text goes to the latest game sent **to this chat**, but only when
the chat has one child (with siblings the bot asks for a reply). One family's answer
cannot attach to another family's game.

A chat with several children gets each game headed `👶 <name>`; a second child with the
same name in one chat is questioned during the questionnaire. Owner monitoring lives in the ops bot, not here: the product bot records each join,
subscription, pause, limit and block in `user_events`, the ops bot forwards them within a
polling cycle and answers `/users` (`src/ops/users_report.py`). The public bot never holds
the ops token.

Strangers without an invite only get a "closed bot" notice; nothing is stored for them.

## Abuse model

The bot is public in Telegram search, so anyone can write to it.

| threat | control |
|---|---|
| a stranger uses the bot | nothing without a valid invite: no rows, no model run; the "closed" notice at most once a day per chat, then silence |
| guessing an invite | 96-bit random code, one-time, 7 days, stored as a hash |
| impersonating the owner | owner commands check the sender chat id that Telegram sets |
| a leaked invite or a misbehaving member | `GAME_MAX_CHILDREN_PER_CHAT` (3), `GAME_MAX_ACTIVE_CHILDREN` (20; each child is a model run a day, the owner is alerted at the cap), `GAME_MAX_FEEDBACK_PER_DAY` (20); `/block <chat_id>` |
| a compromised bot process | runs as `gotd-bot`, not the application user: it cannot read the other services' tokens through `/proc` (`ProtectProc=invisible`), only the database |
| a leaked bot token | the token lives only in a root-owned 0600 file read by systemd; rotate with BotFather `/revoke` |
| prompt injection through names | the child's name and birth date never reach a model; only the age in months does |

Group chats are ignored; there is no inbound port (long polling).

## Known gaps

- Telegram feedback is stored raw (`processed = 0`). Semantic analysis currently runs only
  for VK events, which reach the enrichment queue through the Worker.
- `/pause` applies from the next preparation; a game already prepared for today is still sent.
- No self-service data deletion yet: the owner removes a family by hand on request.
