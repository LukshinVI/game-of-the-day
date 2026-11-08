#!/usr/bin/env python3
"""Sunday digest for parents: the week in games, what went well, what to expect next.

For every active, non-test child with at least one game this week, one message per
recipient: games and skills of the week, what the parents' feedback said went well,
and a few typical milestones for the child's age (CDC "Learn the Signs. Act Early."),
clearly marked as a guide, not an assessment. On the first Sunday of a month it also
asks one question: what is missing in the service (the answer arrives as ordinary
feedback and the insights job routes it to the owner as a wish).

Deterministic: no model call. Idempotent per child, recipient and week.

    python3 src/ops/weekly_digest.py run [--date YYYY-MM-DD] [--dry-run]
"""

import argparse
import hashlib
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.delivery import game_delivery  # noqa: E402
from src.preparation import skills  # noqa: E402

DB_PATH = ROOT / "data" / "game-of-the-day.db"
MOSCOW = ZoneInfo("Europe/Moscow")
# A digest of one or two games (a family that just joined, a week after an outage)
# says nothing useful; wait for a real week.
MIN_GAMES = 3

# Typical milestones by age, after CDC "Learn the Signs. Act Early." (2022 revision):
# what most children (about three in four) do by that age.
MILESTONES = [
    (2, ["улыбается, когда с ним говорят", "лёжа на животе приподнимает голову", "следит глазами за лицом"]),
    (4, ["гулит в ответ, когда с ним говорят", "на руках держит голову без поддержки", "тянет игрушку ко рту"]),
    (6, ["смеётся", "переворачивается со спины на живот", "лёжа на животе опирается на руки"]),
    (9, ["откликается на своё имя", "сидит без поддержки", "перекладывает предмет из руки в руку"]),
    (12, ["машет «пока»", "подтягивается, чтобы встать", "берёт мелкие кусочки двумя пальцами"]),
    (15, ["пробует говорить 1–2 слова кроме «мама» и «папа»", "делает несколько шагов сам",
          "показывает пальцем, чтобы что-то попросить"]),
    (18, ["пытается сказать три слова и больше", "ходит сам", "пьёт из открытой чашки, может проливать"]),
    (24, ["говорит два слова вместе: «ещё сок»", "бегает", "бьёт по мячу ногой"]),
    (30, ["говорит около 50 слов", "прыгает на двух ногах", "играет «понарошку» сам"]),
    (36, ["говорит в диалоге 2–3 фразы подряд", "сам надевает простую одежду", "рисует круг, если показать"]),
]


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def age_months(birth, today):
    birth = date.fromisoformat(birth)
    months = (today.year - birth.year) * 12 + today.month - birth.month
    return months - (1 if today.day < birth.day else 0)


def next_milestones(age):
    """The next milestone band at or after the child's age."""
    for months, items in MILESTONES:
        if months >= age:
            return months, items
    return None


def compose(con, child, today):
    week_start = today - timedelta(days=6)
    games = con.execute(
        "SELECT id, title, skill FROM games WHERE child_id = ? AND game_date BETWEEN ? AND ? ORDER BY game_date",
        (child["id"], week_start.isoformat(), today.isoformat()),
    ).fetchall()
    if len(games) < MIN_GAMES:
        return None
    ids = [g["id"] for g in games]
    marks = ",".join("?" * len(ids))
    went_well = [r[0] for r in con.execute(
        f"SELECT DISTINCT evidence FROM observations WHERE game_id IN ({marks}) AND kind = 'skill' "
        f"AND value = 'went_well' LIMIT 3", ids)] if _has(con, "observations") else []
    liked = [r[0] for r in con.execute(
        f"SELECT DISTINCT g.title FROM observations o JOIN games g ON g.id = o.game_id WHERE o.game_id IN ({marks}) "
        f"AND o.kind = 'outcome' AND o.value = 'liked' LIMIT 3", ids)] if _has(con, "observations") else []

    counts = Counter(skills.LABELS.get(g["skill"], g["skill"]) for g in games if g["skill"])
    lines = [f"🗓 Итоги недели — {child['name']}: {len(games)} {_games_word(len(games))}",
             "Навыки: " + ", ".join(f"{label} ×{n}" for label, n in counts.most_common())]
    if liked:
        lines.append("❤️ Понравилось: " + ", ".join(f"«{t}»" for t in liked))
    if went_well:
        lines.append("🌱 Получалось: " + "; ".join(went_well))
    age = age_months(child["birth_date"], today)
    band = next_milestones(age)
    if band:
        months, items = band
        when = "сейчас" if months == age else f"к {months} мес."
        lines += ["", f"👀 Что обычно появляется {when}:", *[f"• {item}" for item in items],
                  "У каждого ребёнка свой темп; это ориентир CDC, а не медицинская оценка."]
    if today.day <= 7:
        lines += ["", "💡 Раз в месяц спрашиваем: чего не хватает в сервисе, что улучшить? "
                      "Ответьте на это сообщение одним предложением."]
    else:
        lines += ["", "💬 Отзыв на любую игру помогает подбирать следующие."]
    return "\n".join(lines)


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def _games_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "игра"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "игры"
    return "игр"


def send(channel, address, text, key):
    if channel == "vk":
        random_id = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        return game_delivery.vk_send(text, random_id, peer_id=address)
    if channel == "telegram":
        return game_delivery.telegram_send(text, address)
    raise RuntimeError(f"unknown channel {channel}")


def run(today=None, dry_run=False, db_path=None, sender=send):
    today = today or datetime.now(MOSCOW).date()
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    con = connect(db_path)
    sent = 0
    try:
        children = con.execute(
            "SELECT id, name, birth_date FROM children WHERE active = 1 AND is_test = 0 ORDER BY id"
        ).fetchall()
        for child in children:
            text = compose(con, child, today)
            if text is None:
                continue
            recipients = con.execute(
                "SELECT channel, address FROM recipients WHERE child_id = ? AND active = 1", (child["id"],)
            ).fetchall()
            for r in recipients:
                done = con.execute("SELECT 1 FROM digests WHERE child_id = ? AND channel = ? AND address = ? AND week = ?",
                                   (child["id"], r["channel"], r["address"], week)).fetchone()
                if done:
                    continue
                if dry_run:
                    print(f"--- {child['name']} → {r['channel']}\n{text}\n")
                    continue
                try:
                    sender(r["channel"], r["address"], text, f"digest-{week}-{child['id']}-{r['address']}")
                except Exception as exc:  # noqa: BLE001 - one recipient failing must not stop the others
                    print(f"digest to {r['channel']} failed: {type(exc).__name__}", file=sys.stderr)
                    continue
                con.execute("INSERT INTO digests(child_id, channel, address, week) VALUES (?, ?, ?, ?)",
                            (child["id"], r["channel"], r["address"], week))
                con.commit()
                sent += 1
    finally:
        con.close()
    print(f"digests sent: {sent}")
    return sent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="weekly-digest")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--date")
    r.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run(date.fromisoformat(args.date) if args.date else None, args.dry_run)


if __name__ == "__main__":
    main()
