"""A gentle reminder to families who never write, or went quiet, that a few words make
the next games fit their child better.

It is not a separate message: the reminder replaces the usual feedback footer of the
day's game, so the parent answers the game itself. When it is due (per child):

- never wrote: no written answer yet and at least FIRST_AFTER_GAMES games delivered;
- went quiet: the last written answer (or «полезно ли?» score) is SILENCE_DAYS old or
  more, with at least QUIET_GAMES games delivered since (a pause sends no games, so a
  family back from a pause is not told it was silent);
- at most once in GAP_DAYS and MAX_TIMES in total; never on Sunday (the digest carries
  its own question that day).

A sticker or a photo is not a written answer (src/feedback/reactions.py asks for words
once per game). Off unless GAME_FEEDBACK_NUDGE=1: messages to real families go out only
when the owner decides. Deterministic; runs in the delivery's own transaction.
"""

import os
from datetime import date

ENABLED_ENV = "GAME_FEEDBACK_NUDGE"
FIRST_AFTER_GAMES = 5
SILENCE_DAYS = 21
QUIET_GAMES = 14
GAP_DAYS = 30
MAX_TIMES = 3

TEXTS = {
    "never": (
        "💬 А как малышу наши игры?\n\n"
        "Нам очень помогает обратная связь родителей. Даже пара слов — «понравилось», "
        "«было сложно» или «быстро надоело» — помогает лучше подбирать следующие игры.\n\n"
        "Просто ответьте на это сообщение. Мы обязательно учтём ваши впечатления 💛"
    ),
    "quiet": (
        "💬 Давно не слышали, как проходят игры! Малыши меняются быстро, и подбор успевает "
        "за ними, когда вы рассказываете. Пара слов в ответ на это сообщение — что зашло, "
        "а что нет — и мы подстроимся 🌱"
    ),
}


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def last_spoke(con, child_id):
    """The day of the family's last written answer about this child, or None."""
    row = con.execute(
        "SELECT max(coalesce(date(f.received_at), g.game_date)) FROM feedback f "
        "JOIN games g ON g.id = f.game_id WHERE g.child_id = ? "
        "AND coalesce(f.insights_status, '') != 'reaction'", (child_id,)).fetchone()
    days = [row[0]] if row and row[0] else []
    if _has(con, "pulses"):
        scored = con.execute("SELECT max(date(answered_at)) FROM pulses WHERE child_id = ? AND score IS NOT NULL",
                             (child_id,)).fetchone()[0]
        days += [scored] if scored else []
    return date.fromisoformat(max(days)) if days else None


def _sent_games(con, child_id, after, before):
    return con.execute(
        "SELECT count(DISTINCT g.id) FROM games g JOIN deliveries d ON d.game_id = g.id "
        "WHERE g.child_id = ? AND d.status = 'sent' AND g.game_date > ? AND g.game_date < ?",
        (child_id, after, before)).fetchone()[0]


def kind_due(con, child_id, game_id, today):
    """'never', 'quiet' or None for this child's game on `today`."""
    if today.weekday() == 6:
        return None
    done = con.execute(
        "SELECT count(*), max(g.game_date) FROM nudges n JOIN games g ON g.id = n.game_id "
        "WHERE n.child_id = ? AND n.game_id != ? "
        "AND EXISTS (SELECT 1 FROM deliveries d WHERE d.game_id = n.game_id AND d.status = 'sent')",
        (child_id, game_id)).fetchone()
    if done[0] >= MAX_TIMES or (done[1] and (today - date.fromisoformat(done[1])).days < GAP_DAYS):
        return None
    spoke = last_spoke(con, child_id)
    if spoke is None:
        return "never" if _sent_games(con, child_id, "", today.isoformat()) >= FIRST_AFTER_GAMES else None
    if (today - spoke).days >= SILENCE_DAYS and \
            _sent_games(con, child_id, spoke.isoformat(), today.isoformat()) >= QUIET_GAMES:
        return "quiet"
    return None


def footer(con, game):
    """The reminder to put under this game instead of the usual footer, or None.
    The same game keeps the same reminder for every recipient and on a retry."""
    if os.environ.get(ENABLED_ENV) != "1" or not _has(con, "nudges") or not game.get("child_id"):
        return None
    row = con.execute("SELECT kind FROM nudges WHERE game_id = ?", (game["game_id"],)).fetchone()
    if row is not None:
        return TEXTS[row[0]]
    kind = kind_due(con, game["child_id"], game["game_id"], date.fromisoformat(game["game_date"]))
    if kind is None:
        return None
    con.execute("INSERT INTO nudges (game_id, child_id, kind) VALUES (?, ?, ?)",
                (game["game_id"], game["child_id"], kind))
    return TEXTS[kind]


def summary(con, today, days=30):
    """(reminders sent, of them followed by a written answer within 7 days) in `days`."""
    if not _has(con, "nudges"):
        return 0, 0
    rows = con.execute(
        "SELECT n.child_id, g.game_date FROM nudges n JOIN games g ON g.id = n.game_id "
        "WHERE g.game_date >= date(?, ?) "
        "AND EXISTS (SELECT 1 FROM deliveries d WHERE d.game_id = n.game_id AND d.status = 'sent')",
        (today.isoformat(), f"-{days} days")).fetchall()
    answered = sum(1 for child, day in rows if con.execute(
        "SELECT 1 FROM feedback f JOIN games g ON g.id = f.game_id WHERE g.child_id = ? "
        "AND coalesce(f.insights_status, '') != 'reaction' "
        "AND coalesce(date(f.received_at), g.game_date) BETWEEN ? AND date(?, '+7 days')",
        (child, day, day)).fetchone())
    return len(rows), answered
