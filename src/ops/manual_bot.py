#!/usr/bin/env python3
"""Owner-only Telegram control for manual delivery.

Commands, accepted only from the owner's private chat:
  /status         state of today's delivery
  /send           show the state and a one-time code (valid for two minutes)
  /send <code>    send today's game; a delivery that ended in "failed" is retried

The bot never builds or sends game messages itself: it runs the delivery CLI, so the
state machine, idempotency and alerts stay in one place. It only makes outbound calls
(long polling), so it needs no inbound port.
"""

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DELIVERY = ROOT / "src" / "delivery" / "game_delivery.py"
API = "https://api.telegram.org"

CONFIRM_TTL_SECONDS = 120
MAX_MESSAGE_AGE_SECONDS = 60
POLL_TIMEOUT_SECONDS = 30
CLI_TIMEOUT_SECONDS = 180
MAX_REPLY_CHARS = 3500

HELP = (
    "Команды:\n"
    "/status - состояние сегодняшней доставки\n"
    "/send - отправить сегодняшнюю игру (потребуется код подтверждения)"
)


def run_delivery(*args):
    try:
        result = subprocess.run(
            [sys.executable, str(DELIVERY), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Команда не завершилась за {CLI_TIMEOUT_SECONDS} секунд."

    output = (result.stdout + result.stderr).strip() or "(нет вывода)"

    if result.returncode != 0:
        return f"Ошибка (код {result.returncode}):\n{output}"

    return output


class Controller:
    def __init__(self, owner_id, run=run_delivery, now=time.time, new_code=None):
        self.owner_id = owner_id
        self.run = run
        self.now = now
        self.new_code = new_code or (lambda: f"{secrets.randbelow(10000):04d}")
        self.pending = None

    def handle(self, update):
        """Return the reply text for an update, or None when it must be ignored."""
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        if (
            chat.get("type") != "private"
            or chat.get("id") != self.owner_id
            or sender.get("id") != self.owner_id
        ):
            return None

        if self.now() - message.get("date", 0) > MAX_MESSAGE_AGE_SECONDS:
            return None

        parts = (message.get("text") or "").split()

        if not parts:
            return None

        command = parts[0].split("@")[0].lower()

        if command == "/status":
            return "Доставка сегодня:\n" + self.run("status")

        if command == "/send":
            return self.send(parts[1:])

        return HELP

    def send(self, args):
        code = args[0] if args else ""
        pending = self.pending

        if (
            pending
            and self.now() < pending[1]
            and secrets.compare_digest(code, pending[0])
        ):
            self.pending = None
            sent = self.run("send-today", "--retry-failed")
            return f"{sent}\n\nПроверка:\n{self.run('status')}"

        code = self.new_code()
        self.pending = (code, self.now() + CONFIRM_TTL_SECONDS)

        return (
            "Доставка сегодня:\n"
            + self.run("status")
            + f"\n\nЧтобы отправить (или повторить после ошибки), "
            f"в течение 2 минут: /send {code}"
        )


def telegram(token, method, payload, timeout):
    request = urllib.request.Request(
        f"{API}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    owner_id = int(os.environ["TELEGRAM_CHAT_ID"])
    controller = Controller(owner_id)
    offset = None

    def reply(text):
        telegram(
            token,
            "sendMessage",
            {"chat_id": owner_id, "text": text[:MAX_REPLY_CHARS]},
            20,
        )

    try:
        reply("Бот ручной отправки запущен. /help")
    except Exception as exc:  # noqa: BLE001 - the loop below reports real problems
        print(f"start message failed: {type(exc).__name__}", flush=True)

    while True:
        try:
            payload = {
                "timeout": POLL_TIMEOUT_SECONDS,
                "allowed_updates": ["message"],
            }

            if offset is not None:
                payload["offset"] = offset

            result = telegram(
                token,
                "getUpdates",
                payload,
                POLL_TIMEOUT_SECONDS + 10,
            )

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                text = controller.handle(update)

                if text:
                    print(f"owner command handled update_id={update['update_id']}", flush=True)
                    reply(text)

        except Exception as exc:  # noqa: BLE001 - log the type only, never URLs or tokens
            print(f"poll error: {type(exc).__name__}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
