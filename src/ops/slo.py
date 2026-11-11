#!/usr/bin/env python3
"""Service level objectives (docs/slo.md): is the service doing what it promises families?

Each objective is a share of good events over a rolling window against a target; the
error budget is how many bad events the target still allows. Test children are
excluded. Read-only.

- on time: a child-day whose game was first sent by 10:15 Moscow time on its date (the
  evening before counts); a child-day with no game sent is a miss, not a gap;
- same day: the same child-days, sent at all by the end of that day;
- feedback freshness: stored feedback whose insights ran within an hour (feedback
  deferred by the daily caps is left out);
- fresh game: the day's game made for this child by the model, not taken from the
  library or the reserve (a quality signal, not in docs/slo.md);
- backup: the newest local backup is younger than BACKUP_MAX_HOURS (a state, not a ratio).

A child-day is (child, date) from the child's first game to today (today only once its
deadline has passed), for non-test children with an active recipient. Pauses are not
stored as intervals yet, so a family that paused and resumed counts its paused days.

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
FEEDBACK_MINUTES = 60
BACKUP_MAX_HOURS = 26
OBJECTIVES = {  # name: (title, target)
    "on_time": ("Игра пришла до 10:15", 0.95),
    "same_day": ("Игра пришла в тот же день", 0.99),
    "feedback": (f"Отзыв разобран за {FEEDBACK_MINUTES} мин", 0.95),
    "fresh": ("Игра от модели, не из запаса", 0.90),
}


def parse(ts):
    """SQLite stores both ISO with offset and CURRENT_TIMESTAMP (naive, UTC)."""
    if not ts:
        return None
    value = datetime.fromisoformat(str(ts).replace(" ", "T"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _real(alias="g"):
    return f"({alias}.child_id IS NULL OR {alias}.child_id NOT IN (SELECT id FROM children WHERE is_test = 1))"


def child_days(con, start, end, now):
    """[(child_id, date)] in start..end; end itself only after its 10:15 deadline."""
    last = date.fromisoformat(end)
    local = now.astimezone(MOSCOW)
    if local.date() <= last and (local.hour, local.minute) < DEADLINE:
        last = local.date() - timedelta(days=1)
    children = con.execute(
        """SELECT c.id, min(g.game_date) FROM children c JOIN games g ON g.child_id = c.id
           WHERE c.is_test = 0 AND c.active = 1
             AND EXISTS (SELECT 1 FROM recipients r WHERE r.child_id = c.id AND r.active = 1)
           GROUP BY c.id""").fetchall()
    days = []
    for child_id, first in children:
        day = max(date.fromisoformat(start), date.fromisoformat(first))
        while day <= last:
            days.append((child_id, day.isoformat()))
            day += timedelta(days=1)
    return days


def collect(con, start, end, now=None):
    """Good and total events per objective for dates start..end (inclusive)."""
    now = now or datetime.now(timezone.utc)
    first_sent = {}
    for child_id, game_date, sent_at in con.execute(
            "SELECT g.child_id, g.game_date, d.sent_at FROM deliveries d JOIN games g ON g.id = d.game_id "
            "WHERE g.game_date BETWEEN ? AND ? AND d.status = 'sent' AND g.child_id IS NOT NULL",
            (start, end)):
        sent = parse(sent_at)
        if sent is not None:
            key = (child_id, game_date)
            first_sent[key] = min(sent, first_sent.get(key, sent))
    days = child_days(con, start, end, now)
    on_time = same_day = 0
    for child_id, day in days:
        sent = first_sent.get((child_id, day))
        if sent is None:
            continue
        cutoff = datetime.combine(date.fromisoformat(day), datetime.min.time(), MOSCOW)
        on_time += sent <= cutoff + timedelta(hours=DEADLINE[0], minutes=DEADLINE[1])
        same_day += sent < cutoff + timedelta(days=1)

    origins = con.execute(
        f"SELECT g.origin FROM games g WHERE g.game_date BETWEEN ? AND ? AND {_real()}", (start, end)).fetchall()

    feedback = con.execute(
        f"""SELECT f.received_at, f.insights_at FROM feedback f JOIN games g ON g.id = f.game_id
            WHERE g.game_date BETWEEN ? AND ? AND f.received_at IS NOT NULL
              AND coalesce(f.insights_status, '') != 'deferred' AND {_real()}""",
        (start, end)).fetchall()
    fresh_feedback = 0
    for received_at, insights_at in feedback:
        received, done = parse(received_at), parse(insights_at)
        fresh_feedback += bool(received and done and done - received <= timedelta(minutes=FEEDBACK_MINUTES))

    return {
        "on_time": (on_time, len(days)),
        "same_day": (same_day, len(days)),
        "feedback": (fresh_feedback, len(feedback)),
        # a swap asked by the family replaced a model game: the day itself was fresh
        "fresh": (sum(1 for (origin,) in origins if origin in ("model", "swap")), len(origins)),
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
    if bad > allowed:
        budget = f"бюджет ошибок исчерпан: {bad} при допустимых {allowed}"
    elif allowed:
        budget = f"запас ошибок {allowed - bad} из {allowed}"
    else:
        budget = "ошибок нет"
    return f"{mark} {title}: {share:.0%} ({good}/{total}), цель {target:.0%}, {budget}"


def report(db_path=DB_PATH, days=28, today=None, manifest=BACKUP_MANIFEST, now=None):
    today = today or date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        counts = collect(con, start, today.isoformat(), now=datetime.fromtimestamp(now, timezone.utc) if now else None)
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
