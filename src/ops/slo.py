#!/usr/bin/env python3
"""Service level objectives: is the service doing what it promises families?

Each objective is a share of good events over a rolling window (28 days by default)
against a target; the error budget is how many bad events the target still allows.
Test children are excluded. Read-only.

- on time: a delivery of the day's game sent by DEADLINE Moscow time on its game date;
- fresh game: the day's game made for this child by the model, not taken from the
  library or the reserve;
- feedback answered: a written feedback got its reply within FEEDBACK_REPLY_MINUTES;
- backup: the newest local backup is younger than BACKUP_MAX_HOURS (a state, not a ratio).

A day with no game at all is not counted here: day_outcome_check alerts on it the same day.

    python3 src/ops/slo.py [--days 28]
"""

import argparse
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
BACKUP_MANIFEST = Path("/var/backups/game-of-the-day/.last-local-ok")
MOSCOW = ZoneInfo("Europe/Moscow")

DEADLINE = (10, 15)
FEEDBACK_REPLY_MINUTES = 60
BACKUP_MAX_HOURS = 26
OBJECTIVES = {  # name: (title, target)
    "on_time": ("Игра пришла до 10:15", 0.95),
    "fresh": ("Игра от модели, не из запаса", 0.90),
    "feedback": (f"Ответ на отзыв за {FEEDBACK_REPLY_MINUTES} мин", 0.95),
}


def parse(ts):
    """SQLite stores both ISO with offset and CURRENT_TIMESTAMP (naive, UTC)."""
    if not ts:
        return None
    value = datetime.fromisoformat(str(ts).replace(" ", "T"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _real(alias="g"):
    return f"({alias}.child_id IS NULL OR {alias}.child_id NOT IN (SELECT id FROM children WHERE is_test = 1))"


def collect(con, start, end):
    """Good and total events per objective for game dates start..end (inclusive)."""
    deliveries = con.execute(
        f"""SELECT g.game_date, d.status, d.sent_at FROM deliveries d JOIN games g ON g.id = d.game_id
            WHERE g.game_date BETWEEN ? AND ? AND {_real()}""", (start, end)).fetchall()
    on_time = 0
    for game_date, status, sent_at in deliveries:
        sent = parse(sent_at)
        if status == "sent" and sent is not None:
            local = sent.astimezone(MOSCOW)
            on_time += local.date().isoformat() == game_date and (local.hour, local.minute) <= DEADLINE

    origins = con.execute(
        f"SELECT g.origin FROM games g WHERE g.game_date BETWEEN ? AND ? AND {_real()}", (start, end)).fetchall()

    feedback = con.execute(
        f"""SELECT f.received_at, f.reply_sent_at FROM feedback f JOIN games g ON g.id = f.game_id
            WHERE g.game_date BETWEEN ? AND ? AND f.received_at IS NOT NULL AND {_real()}""",
        (start, end)).fetchall()
    answered = 0
    for received_at, replied_at in feedback:
        received, replied = parse(received_at), parse(replied_at)
        answered += bool(received and replied and replied - received <= timedelta(minutes=FEEDBACK_REPLY_MINUTES))

    return {
        "on_time": (on_time, len(deliveries)),
        "fresh": (sum(1 for (origin,) in origins if origin == "model"), len(origins)),
        "feedback": (answered, len(feedback)),
    }


def backup_age_hours(manifest=BACKUP_MANIFEST, now=None):
    try:
        epoch = int(manifest.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return ((now or time.time()) - epoch) / 3600


def line(title, target, good, total):
    if total == 0:
        return f"• {title}: нет событий (цель {target:.0%})"
    share = good / total
    bad = total - good
    allowed = int((1 - target) * total + 1e-9)
    mark = "✅" if share >= target else "🔴"
    budget = f"запас ошибок {allowed - bad} из {allowed}" if allowed else ("ошибок нет" if bad == 0 else f"ошибок {bad}")
    return f"{mark} {title}: {share:.0%} ({good}/{total}), цель {target:.0%}, {budget}"


def report(db_path=DB_PATH, days=28, today=None, manifest=BACKUP_MANIFEST, now=None):
    today = today or date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        counts = collect(con, start, today.isoformat())
    finally:
        con.close()
    lines = [f"Надёжность за {days} дн."]
    for name, (title, target) in OBJECTIVES.items():
        lines.append(line(title, target, *counts[name]))
    age = backup_age_hours(manifest, now)
    if age is None:
        lines.append("❔ Бэкап: не удалось прочитать отметку последнего бэкапа")
    else:
        mark = "✅" if age <= BACKUP_MAX_HOURS else "🔴"
        lines.append(f"{mark} Бэкап: последний {age:.0f} ч назад (норма до {BACKUP_MAX_HOURS} ч)")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="slo")
    parser.add_argument("--days", type=int, default=28)
    args = parser.parse_args(argv)
    print(report(days=args.days))


if __name__ == "__main__":
    main()
