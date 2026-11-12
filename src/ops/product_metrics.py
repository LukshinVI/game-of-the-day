#!/usr/bin/env python3
"""Product metrics: is the service useful to families, and is personalization working?

Compares the last `days` with the `days` before (test children excluded):
- games delivered, and the share that got a written answer or just a reaction;
- outcomes (liked / disliked) and difficulty (too easy / right / too hard) from
  observations: "too easy" falling after the adaptation means personalization works;
- pauses and blocks; wishes about the service;
- days served from the reserve, and how often the safety reviewer rejected a candidate;
- retention by monthly cohort (src/ops/cohorts.py) and the «полезно ли?» score (src/ops/pulse.py).

Read-only. Shown by /metrics in the ops bot and in the Monday report.

    python3 src/ops/product_metrics.py [--days 7]
"""

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.feedback import nudge  # noqa: E402
from src.ops import cohorts, pulse  # noqa: E402
DB_PATH = ROOT / "data" / "game-of-the-day.db"


def _has(con, table, column=None):
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is None:
        return False
    return column is None or column in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def period(con, start, end):
    """Numbers for game dates in [start, end]."""
    not_test = ("AND NOT EXISTS (SELECT 1 FROM children c WHERE c.id = g.child_id AND c.is_test = 1)"
                if _has(con, "children", "is_test") else "")
    sent = con.execute(
        f"SELECT count(DISTINCT g.id) FROM games g JOIN deliveries d ON d.game_id = g.id "
        f"WHERE d.status = 'sent' AND g.game_date BETWEEN ? AND ? {not_test}", (start, end)).fetchone()[0]
    status = "f.insights_status" if _has(con, "feedback", "insights_status") else "NULL"
    rows = con.execute(
        f"SELECT g.id, max(CASE WHEN COALESCE({status}, '') != 'reaction' THEN 1 ELSE 0 END) AS written "
        f"FROM feedback f JOIN games g ON g.id = f.game_id WHERE g.game_date BETWEEN ? AND ? {not_test} GROUP BY g.id",
        (start, end)).fetchall()
    answered = sum(1 for r in rows if r[1])
    reacted = sum(1 for r in rows if not r[1])
    obs = {}
    if _has(con, "observations"):
        for kind, value, n in con.execute(
                f"SELECT o.kind, o.value, count(*) FROM observations o JOIN games g ON g.id = o.game_id "
                f"WHERE g.game_date BETWEEN ? AND ? AND o.kind IN ('outcome', 'difficulty') {not_test} "
                f"GROUP BY o.kind, o.value", (start, end)):
            obs[(kind, value)] = n
    reserve = con.execute(
        f"SELECT count(*) FROM games g WHERE g.game_date BETWEEN ? AND ? AND COALESCE(g.origin, 'model') NOT IN ('model', 'swap') "
        f"{not_test}", (start, end)).fetchone()[0] if _has(con, "games", "origin") else 0
    reviews = flagged = 0
    if _has(con, "model_runs"):
        reviews, flagged = con.execute(
            "SELECT count(*), COALESCE(sum(outcome = 'rejected'), 0) FROM model_runs "
            "WHERE model LIKE 'review:%' AND dry_run = 0 AND target_date BETWEEN ? AND ?", (start, end)).fetchone()
    pauses = con.execute(
        "SELECT count(*) FROM user_events WHERE kind IN ('pause', 'block') AND date(at) BETWEEN ? AND ?",
        (start, end)).fetchone()[0] if _has(con, "user_events") else 0
    wishes = con.execute("SELECT count(*) FROM wishes WHERE date(created_at) BETWEEN ? AND ?",
                         (start, end)).fetchone()[0] if _has(con, "wishes") else 0
    return {"sent": sent, "answered": answered, "reacted": reacted, "obs": obs, "reserve": reserve,
            "reviews": reviews, "flagged": flagged, "pauses": pauses, "wishes": wishes}


def pct(part, whole):
    return f"{part * 100 // whole}%" if whole else "—"


def line(label, now, before):
    return f"• {label}: {now} (до этого {before})"


def report(db_path=DB_PATH, days=7, today=None):
    today = today or date.today()
    cur_start = (today - timedelta(days=days - 1)).isoformat()
    prev_end = (today - timedelta(days=days)).isoformat()
    prev_start = (today - timedelta(days=2 * days - 1)).isoformat()
    con = sqlite3.connect(db_path, timeout=10)
    try:
        cur = period(con, cur_start, today.isoformat())
        prev = period(con, prev_start, prev_end)
        value = [cohorts.report(con, today), usefulness(con, today), reminders(con, today)]
    finally:
        con.close()

    def feedback_share(p):
        return f"{pct(p['answered'], p['sent'])} ({p['answered']} из {p['sent']})"

    def difficulty(p):
        easy, right, hard = (p["obs"].get(("difficulty", v), 0) for v in ("too_easy", "right", "too_hard"))
        total = easy + right + hard
        return f"легко {pct(easy, total)} · в самый раз {pct(right, total)} · сложно {pct(hard, total)}" if total else "нет данных"

    def liked(p):
        yes, no = p["obs"].get(("outcome", "liked"), 0), p["obs"].get(("outcome", "disliked"), 0)
        return f"{pct(yes, yes + no)} ({yes} из {yes + no})" if yes + no else "нет данных"

    return "\n".join([
        f"📈 Продукт за {days} дн. (в скобках — предыдущие {days} дн.)",
        line("игр доставлено", cur["sent"], prev["sent"]),
        line("с письменным отзывом", feedback_share(cur), feedback_share(prev)),
        line("только реакция (стикер, фото)", cur["reacted"], prev["reacted"]),
        line("понравилось", liked(cur), liked(prev)),
        line("сложность", difficulty(cur), difficulty(prev)),
        line("паузы и блокировки", cur["pauses"], prev["pauses"]),
        line("пожелания", cur["wishes"], prev["wishes"]),
        line("дней из резерва", cur["reserve"], prev["reserve"]),
        line("ревьюер отклонил", f"{cur['flagged']} из {cur['reviews']}", f"{prev['flagged']} из {prev['reviews']}"),
        "",
        *value,
    ])


def usefulness(con, today):
    answered, asked, average, dist = pulse.summary(con, today)
    if not asked:
        return "📊 Полезность (1–5, за 90 дн.): ещё не спрашивали"
    if not answered:
        return f"📊 Полезность (1–5, за 90 дн.): спросили {asked}, ответов пока нет"
    spread = " ".join(f"{s}×{dist[s]}" for s in sorted(dist, reverse=True))
    return f"📊 Полезность (1–5, за 90 дн.): в среднем {average:.1f}, ответили {answered} из {asked} ({spread})"


def reminders(con, today):
    sent, answered = nudge.summary(con, today)
    if not sent:
        return "💬 Напоминания молчащим (за 30 дн.): не было"
    return f"💬 Напоминания молчащим (за 30 дн.): {sent}, после них написали за неделю {pct(answered, sent)} ({answered})"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="product-metrics")
    parser.add_argument("--days", type=int, default=7)
    print(report(days=parser.parse_args(argv).days))


if __name__ == "__main__":
    main()
