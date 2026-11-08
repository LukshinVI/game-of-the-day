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
3. `install -m 644 ops/product-bot/game-product-bot.service /etc/systemd/system/ &&
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
same name in one chat is questioned during the questionnaire. The owner gets a message
on every join, subscription and pause.

Strangers without an invite only get a "closed bot" notice; nothing is stored for them.

## Known gaps

- Telegram feedback is stored raw (`processed = 0`). Semantic analysis currently runs only
  for VK events, which reach the enrichment queue through the Worker.
- `/pause` applies from the next preparation; a game already prepared for today is still sent.
- No self-service data deletion yet: the owner removes a family by hand on request.
