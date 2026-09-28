#!/usr/bin/env python3
"""Independent check of the day's outcome: was today's game delivered?

Process alerts say "this step failed"; nothing else said "the day's result was not
achieved". This check is deliberately separate from preparation and delivery: it only
reads SQLite and, when the outcome is not met, sends a critical Russian alert. A timer
runs it several times after the delivery deadline, so the alert repeats until fixed.

Exit codes: 0 outcome met (or too early to judge), 1 outcome NOT met (alert sent),
2 the check could not do its job (database unreadable or alert not sent).

It cannot notice that the whole host is down; that needs a heartbeat from outside.
"""

import argparse
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
NOTIFIER = ROOT / "src" / "ops" / "notifier.py"
MOSCOW = ZoneInfo("Europe/Moscow")

DEFAULT_NOT_BEFORE = "10:15"
MAX_ERROR_CHARS = 200

QUERY = """
    SELECT g.legacy_id, g.title, d.status, d.attempts, d.external_message_id, d.last_error
    FROM games AS g
    JOIN deliveries AS d ON d.game_id = g.id
    WHERE d.channel = 'vk'
      AND g.game_date = ?
    ORDER BY g.id
"""

BOT_HINT = "Бот: /status, затем /send или /retry."


def todays_rows(db_path, today):
    con = sqlite3.connect(db_path, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        return con.execute(QUERY, (today,)).fetchall()
    finally:
        con.close()


def evaluate(rows):
    """Return None when the outcome is met, otherwise the alert details in Russian."""
    if not rows:
        return "На сегодня нет подготовленной игры.\nПодготовка не отработала или не записала игру.\n" + BOT_HINT

    if len(rows) > 1:
        return f"На сегодня найдено доставок: {len(rows)}. Нужен ручной разбор."

    row = rows[0]
    game = f"ID: {row['legacy_id']}\nНазвание: {row['title']}"

    if row["status"] == "sent":
        if row["external_message_id"]:
            return None

        return f"{game}\nСтатус sent, но нет номера сообщения VK. Состояние противоречиво."

    details = f"{game}\nСтатус: {row['status']}\nПопыток: {row['attempts']}"

    if row["last_error"]:
        details += f"\nОшибка: {str(row['last_error'])[:MAX_ERROR_CHARS]}"

    return details + "\n" + BOT_HINT


def send_alert(details):
    result = subprocess.run(
        [
            sys.executable,
            str(NOTIFIER),
            "critical",
            "Сегодня игра не доставлена",
            "--details",
            details,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0


def check(db_path=DB_PATH, now=None, alert=send_alert, not_before=DEFAULT_NOT_BEFORE):
    now = now or datetime.now(MOSCOW)
    hour, minute = (int(part) for part in not_before.split(":"))

    if (now.hour, now.minute) < (hour, minute):
        print(f"Слишком рано для проверки (до {not_before} MSK): исход дня ещё не оценивается.")
        return 0

    try:
        problem = evaluate(todays_rows(db_path, now.date().isoformat()))
    except Exception as exc:  # noqa: BLE001 - an unreadable database is itself a failed day
        problem = f"Не удалось прочитать состояние доставки: {type(exc).__name__}."
        print(problem, file=sys.stderr)
        alert_sent = alert(problem)
        return 2 if not alert_sent else 1

    if problem is None:
        print("OK: сегодняшняя игра доставлена.")
        return 0

    print("Исход дня НЕ достигнут:\n" + problem, file=sys.stderr)

    if not alert(problem):
        print("Алерт не отправлен.", file=sys.stderr)
        return 2

    return 1


def main():
    parser = argparse.ArgumentParser(prog="day-outcome-check")
    parser.add_argument("--not-before", default=DEFAULT_NOT_BEFORE, help="HH:MM Moscow time")
    args = parser.parse_args()
    sys.exit(check(not_before=args.not_before))


if __name__ == "__main__":
    main()
