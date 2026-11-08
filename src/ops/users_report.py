#!/usr/bin/env python3
"""Owner monitoring of product-bot users, served by the ops bot.

- pending_events / mark_notified: events the product bot recorded (joins, new
  children, pauses, limits, blocks) and the ops bot has not forwarded yet;
- report: one block per family with children, ages, subscription state, the last
  delivered game, feedback in the last 7 days and today's failures, plus totals.

Read-mostly; the only write is marking events as forwarded. A database without
the onboarding tables (single-child mode) yields no events and a short report.

    python3 src/ops/users_report.py      # print the report
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
MOSCOW = ZoneInfo("Europe/Moscow")
MAX_EVENTS_PER_BATCH = 20


def connect(db_path):
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def pending_events(db_path=DB_PATH):
    con = connect(db_path)
    try:
        if not has_table(con, "user_events"):
            return []
        return [dict(r) for r in con.execute(
            "SELECT id, at, kind, text FROM user_events WHERE notified_at IS NULL ORDER BY id LIMIT ?",
            (MAX_EVENTS_PER_BATCH,),
        )]
    finally:
        con.close()


def mark_notified(ids, db_path=DB_PATH):
    if not ids:
        return
    con = connect(db_path)
    try:
        con.executemany(
            "UPDATE user_events SET notified_at = CURRENT_TIMESTAMP WHERE id = ?", [(i,) for i in ids]
        )
        con.commit()
    finally:
        con.close()


def age_months(birth_iso, today):
    birth = date.fromisoformat(birth_iso)
    months = (today.year - birth.year) * 12 + today.month - birth.month
    return months - (1 if today.day < birth.day else 0)


def report(db_path=DB_PATH, now=None):
    now = now or datetime.now(MOSCOW)
    today = now.date()
    week_ago = (today - timedelta(days=7)).isoformat()
    con = connect(db_path)
    try:
        if not has_table(con, "members"):
            return "Пользователей нет: база в режиме одного ребёнка (онбординг не включён)."
        members = con.execute(
            "SELECT chat_id, display_name, joined_at, blocked_at FROM members ORDER BY joined_at"
        ).fetchall()
        blocks, totals = [], {"families": 0, "children": 0, "paused": 0, "new": 0, "failed": 0}
        for m in members:
            kids = con.execute(
                """
                SELECT c.id, c.name, c.birth_date, c.active AS child_active, r.active AS rec_active,
                       (SELECT max(g.game_date) FROM games g JOIN deliveries d ON d.game_id = g.id
                        WHERE g.child_id = c.id AND d.channel = 'telegram' AND d.recipient = r.address
                          AND d.status = 'sent') AS last_sent,
                       (SELECT count(*) FROM games g JOIN deliveries d ON d.game_id = g.id
                        WHERE g.child_id = c.id AND d.channel = 'telegram' AND d.recipient = r.address
                          AND d.status = 'failed' AND g.game_date = ?) AS failed_today
                FROM recipients r JOIN children c ON c.id = r.child_id
                WHERE r.channel = 'telegram' AND r.address = ?
                ORDER BY c.id
                """,
                (today.isoformat(), m["chat_id"]),
            ).fetchall()
            feedback = con.execute(
                "SELECT count(*), max(received_at) FROM feedback WHERE channel = 'telegram' "
                "AND external_event_id LIKE ? AND received_at >= ?",
                (f"tg:{m['chat_id']}:%", week_ago),
            ).fetchone()

            status = "⛔ заблокирован" if m["blocked_at"] else ("🆕 анкета не заполнена" if not kids else "")
            header = f"{m['display_name'] or '—'} · chat {m['chat_id']} · с {m['joined_at'][:10]}"
            lines = [header + (f" · {status}" if status else "")]
            for k in kids:
                state = "▶️" if k["rec_active"] and k["child_active"] else "⏸"
                last = k["last_sent"] or "ещё не было"
                warn = " · 🔴 сегодня не доставлено" if k["failed_today"] else ""
                lines.append(
                    f"  {state} {k['name']} (id {k['id']}), {age_months(k['birth_date'], today)} мес."
                    f" · последняя игра: {last}{warn}"
                )
                totals["children"] += 1 if k["rec_active"] and k["child_active"] else 0
                totals["paused"] += 0 if k["rec_active"] and k["child_active"] else 1
                totals["failed"] += 1 if k["failed_today"] else 0
            if kids:
                lines.append(f"  💬 отзывов за 7 дней: {feedback[0]}" + (
                    f", последний {feedback[1][:10]}" if feedback[1] else ""))
            blocks.append("\n".join(lines))
            if not m["blocked_at"]:
                totals["families"] += 1
                totals["new"] += 1 if m["joined_at"][:10] >= week_ago else 0

        all_active = con.execute("SELECT count(*) FROM children WHERE active = 1").fetchone()[0]
        summary = (
            f"Итого: семей {totals['families']} (новых за неделю {totals['new']}), "
            f"детей в Telegram активно {totals['children']}, на паузе {totals['paused']}; "
            f"всего активных детей (вместе с VK): {all_active}."
        )
        if totals["failed"]:
            summary += f"\n🔴 Сегодня не доставлено: {totals['failed']}."
        if not blocks:
            return "Участников пока нет. Пригласить: /invite в продуктовом боте.\n\n" + summary
        return "\n\n".join(blocks) + "\n\n" + summary
    finally:
        con.close()


if __name__ == "__main__":
    print(report(sys.argv[1] if len(sys.argv) > 1 else DB_PATH))


def wishes(db_path=DB_PATH, limit=20):
    con = connect(db_path)
    try:
        if not has_table(con, "wishes"):
            return "Пожеланий пока нет."
        rows = con.execute(
            "SELECT w.created_at, w.source, w.text, c.name FROM wishes w "
            "LEFT JOIN children c ON c.id = w.child_id ORDER BY w.id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return "Пожеланий пока нет. Они приходят из отзывов и команды /idea в продуктовом боте."
    return "💡 Пожелания (новые сверху):\n" + "\n".join(
        f"• {r['created_at'][:10]} {'/idea' if r['source'] == 'command' else 'отзыв'}"
        f"{', ' + r['name'] if r['name'] else ''}: {r['text']}" for r in rows)
