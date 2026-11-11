#!/usr/bin/env python3
"""Owner-only Telegram control for manual delivery.

Commands, accepted only from the owner's private chat:
  /status          state of today's delivery
  /send            when today's delivery is pending: show a one-time code (two minutes)
  /send <code>     start the normal delivery unit
  /retry           when today's delivery failed: show a one-time code
  /retry <code>    start the explicit-retry delivery unit
  /users           product-bot families: children, subscription, last game, feedback
  /ideas           wishes about the service, from feedback and /idea
  /metrics         product metrics: feedback share, liked, difficulty, pauses, reserve days
  /slo             reliability objectives over 28 days and their error budgets
  /llm             the last 7 days of game selection: attempts, latency, tokens, cost, rejections

It also forwards owner-facing events the product bot records in SQLite (new member,
new child, pause, limit, block) within one polling cycle, so the public product bot
never holds this bot's token.

The bot holds only the Telegram credentials. It never sends game messages and never sees
the messaging credentials: it asks systemd to start one of two fixed delivery units, which
carry those credentials themselves (a polkit rule allows exactly these two units).
Outbound long polling only, so there is no inbound port.
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
sys.path.insert(0, str(ROOT))

from src.bot import vk_dialog  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.ops import llm_report, product_metrics, slo, users_report  # noqa: E402

DELIVERY = ROOT / "src" / "delivery" / "game_delivery.py"
API = "https://api.telegram.org"

UNITS = {
    "send": "game-delivery.service",
    "retry": "game-delivery-retry.service",
}
REQUIRED_STATE = {"send": "pending", "retry": "failed"}

CONFIRM_TTL_SECONDS = 120
MAX_MESSAGE_AGE_SECONDS = 60
POLL_TIMEOUT_SECONDS = 30
CLI_TIMEOUT_SECONDS = 60
UNIT_TIMEOUT_SECONDS = 300
EXIT_AFTER_FAILING_SECONDS = 600
MAX_REPLY_CHARS = 3500

HELP = (
    "Команды:\n"
    "/status - состояние сегодняшней доставки\n"
    "/send - отправить сегодняшнюю игру (нужен код подтверждения)\n"
    "/retry - повторить отправку после ошибки (нужен код подтверждения)\n"
    "/users - семьи в продуктовом боте\n"
    "/llm - подбор игр за неделю: попытки, время, токены, стоимость\n"
    "/ideas - пожелания пользователей\n"
    "/metrics - продуктовые метрики за неделю"
    "\n/slo - надёжность за 28 дней: цели и запас ошибок"
)

EXPLANATIONS = {
    "sent": "Сегодняшняя игра уже отправлена.",
    "none": "На сегодня нет подготовленной игры: отправлять нечего.",
    "sending": "Доставка зависла в состоянии sending: нужен ручной разбор, бот ничего не запускает.",
    "ambiguous": "На сегодня несколько доставок: нужен ручной разбор, бот ничего не запускает.",
}


def run_delivery(*args):
    """Run a read-only delivery CLI command and return its output (or an error text)."""
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


def start_unit(unit):
    """Ask systemd to run a oneshot unit and wait until it finishes."""
    try:
        result = subprocess.run(
            ["systemctl", "--no-ask-password", "start", unit],
            capture_output=True,
            text=True,
            timeout=UNIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"{unit} не завершился за {UNIT_TIMEOUT_SECONDS} секунд."

    if result.returncode != 0:
        details = (result.stdout + result.stderr).strip() or "(нет вывода)"
        return f"{unit} завершился с ошибкой (код {result.returncode}):\n{details}"

    return f"{unit} выполнен."


class Controller:
    def __init__(self, owner_id, run=run_delivery, start=start_unit, now=time.time, new_code=None,
                 users=users_report.report, llm=llm_report.report, decide=None):
        self.owner_id = owner_id
        self.decide = decide or decide_application
        self.users = users
        self.llm = llm
        self.run = run
        self.start = start
        self.now = now
        self.new_code = new_code or (lambda: f"{secrets.randbelow(10000):04d}")
        self.pending = None

    def handle(self, update):
        """Return the reply text for an update, or None when it must be ignored."""
        if "callback_query" in update:
            return self.button(update["callback_query"])
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

        if command == "/users":
            try:
                return "Пользователи:\n\n" + self.users()
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось построить отчёт: {type(exc).__name__}"

        if command == "/metrics":
            try:
                return product_metrics.report()
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось построить отчёт: {type(exc).__name__}"

        if command == "/slo":
            try:
                return slo.report()
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось построить отчёт: {type(exc).__name__}"

        if command == "/ideas":
            try:
                return users_report.wishes()
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось построить список: {type(exc).__name__}"

        if command == "/llm":
            try:
                return self.llm()
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось построить отчёт: {type(exc).__name__}"

        if command in ("/send", "/retry"):
            return self.request(command[1:], parts[1:])

        return HELP

    def button(self, query):
        """A tap on an application button: "app:<id>:ok" or "app:<id>:no"."""
        if (query.get("from") or {}).get("id") != self.owner_id:
            return None
        parts = (query.get("data") or "").split(":")
        if len(parts) != 3 or parts[0] != "app" or not parts[1].isdigit() or parts[2] not in ("ok", "no"):
            return None
        try:
            return self.decide(int(parts[1]), parts[2] == "ok")
        except Exception as exc:  # noqa: BLE001
            return f"Не удалось обработать заявку: {type(exc).__name__}"

    def request(self, action, args):
        state = self.run("state").strip()

        if state != REQUIRED_STATE[action]:
            self.pending = None
            return self.explain(action, state)

        code = args[0] if args else ""
        pending = self.pending

        if code:
            valid = (
                pending is not None
                and pending[0] == action
                and self.now() < pending[2]
                and secrets.compare_digest(code, pending[1])
            )
            self.pending = None

            if not valid:
                return f"Код неверен или истёк. Запросите /{action} заново."

            return f"{self.start(UNITS[action])}\n\nПроверка:\n{self.run('status')}"

        new_code = self.new_code()
        self.pending = (action, new_code, self.now() + CONFIRM_TTL_SECONDS)
        what = "отправить" if action == "send" else "повторить после ошибки"

        return (
            "Доставка сегодня:\n"
            + self.run("status")
            + f"\n\nЧтобы {what}, в течение 2 минут: /{action} {new_code}"
        )

    @staticmethod
    def explain(action, state):
        if state == "failed" and action == "send":
            return "Прошлая отправка завершилась ошибкой (причина в /status). Если причина устранена: /retry."

        if state == "pending" and action == "retry":
            return "Ошибки нет, доставка ещё не выполнялась. Для отправки: /send."

        return EXPLANATIONS.get(state, f"Неожиданное состояние: {state}. Бот ничего не запускает.")


def telegram(token, method, payload, timeout):
    request = urllib.request.Request(
        f"{API}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def decide_application(app_id, approve, db_path=None):
    con = ch.connect_db(db_path or users_report.DB_PATH)
    con.execute("PRAGMA busy_timeout = 10000")
    try:
        con.execute("BEGIN IMMEDIATE")
        text = vk_dialog.decide(con, app_id, approve)
        con.commit()
        return text
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def application_buttons(app_id):
    return {"inline_keyboard": [[
        {"text": "✅ Одобрить", "callback_data": f"app:{app_id}:ok"},
        {"text": "❌ Отклонить", "callback_data": f"app:{app_id}:no"},
    ]]}


def forward_user_events(reply, pending=users_report.pending_events, mark=users_report.mark_notified,
                        ask=None):
    """Send the product bot's pending events to the owner, then mark them.

    Marked only after Telegram accepted the message: a failed send is retried on
    the next cycle (at-least-once; a duplicate notice is harmless, a lost one is not).
    An application (kind "application:<id>") goes as its own message with approve /
    reject buttons, sent by ask(text, markup).
    """
    try:
        events = pending()
    except Exception as exc:  # noqa: BLE001 - monitoring must not stop the manual control
        print(f"user events unavailable: {type(exc).__name__}", flush=True)
        return
    if not events:
        return
    plain = [e for e in events if not str(e.get("kind") or "").startswith("application:")]
    for event in events:
        kind = str(event.get("kind") or "")
        if kind.startswith("application:") and ask is not None:
            ask(event["text"], application_buttons(kind.split(":", 1)[1]))
            mark([event["id"]])
        elif kind.startswith("application:"):
            plain.append(event)
    if plain:
        reply("Продуктовый бот:\n" + "\n".join(f"{e['text']}" for e in plain))
        mark([e["id"] for e in plain])


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    owner_id = int(os.environ["TELEGRAM_CHAT_ID"])
    controller = Controller(owner_id)
    offset = None
    last_success = time.time()

    def reply(text):
        telegram(
            token,
            "sendMessage",
            {"chat_id": owner_id, "text": text[:MAX_REPLY_CHARS]},
            20,
        )

    def ask(text, markup):
        telegram(
            token,
            "sendMessage",
            {"chat_id": owner_id, "text": text[:MAX_REPLY_CHARS], "reply_markup": markup},
            20,
        )

    def answered(query, text):
        """Close the button spinner and remove the buttons, so a decision is made once."""
        try:
            telegram(token, "answerCallbackQuery", {"callback_query_id": query["id"]}, 20)
            message = query.get("message") or {}
            if message.get("message_id"):
                telegram(token, "editMessageText", {
                    "chat_id": owner_id, "message_id": message["message_id"],
                    "text": f"{message.get('text', '')}\n\n{text}"[:MAX_REPLY_CHARS],
                }, 20)
        except Exception as exc:  # noqa: BLE001
            print(f"callback answer failed: {type(exc).__name__}", flush=True)

    try:
        reply("🟢 Ops-бот на связи (запуск или перезапуск службы). Ничего не отправлено. /help")
    except Exception as exc:  # noqa: BLE001 - the loop below reports real problems
        print(f"start message failed: {type(exc).__name__}", flush=True)

    while True:
        try:
            payload = {
                "timeout": POLL_TIMEOUT_SECONDS,
                "allowed_updates": ["message", "callback_query"],
            }

            if offset is not None:
                payload["offset"] = offset

            result = telegram(
                token,
                "getUpdates",
                payload,
                POLL_TIMEOUT_SECONDS + 10,
            )
            last_success = time.time()

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                text = controller.handle(update)

                if text and "callback_query" in update:
                    print(f"owner button handled update_id={update['update_id']}", flush=True)
                    answered(update["callback_query"], text)
                elif text:
                    print(f"owner command handled update_id={update['update_id']}", flush=True)
                    reply(text)

            forward_user_events(reply, ask=ask)

        except Exception as exc:  # noqa: BLE001 - log the type only, never URLs or tokens
            print(f"poll error: {type(exc).__name__}", flush=True)

            # A bot that is "active" but cannot reach Telegram is dead in practice: exit so
            # that systemd restarts it and the journal shows the loop.
            if time.time() - last_success > EXIT_AFTER_FAILING_SECONDS:
                print("no successful poll for too long; exiting", flush=True)
                sys.exit(1)

            time.sleep(5)


if __name__ == "__main__":
    main()
