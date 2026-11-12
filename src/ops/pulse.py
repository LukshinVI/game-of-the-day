"""«Полезно ли?»: once a month one question to parents, a 1-5 score.

The question rides on the Sunday digest of the third week of the month (days 15-21):
the first Sunday already asks what is missing, and one ask per digest is the rule. Only
for a recipient whose child has had games for at least MIN_DAYS, once per recipient and
month. Off unless GAME_DIGEST_PULSE=1: messages to real families go out only when the
owner decides.

The answer is a message starting with a digit 1-5 within ANSWER_DAYS («4», «5 очень
нравится»); the rest of the message is the comment. Anything else is ordinary feedback.
The owner sees every answer; the metrics show the average and the distribution.
Deterministic code only; runs in the caller's transaction.
"""

import os
import re
from datetime import date, timedelta, timezone

ENABLED_ENV = "GAME_DIGEST_PULSE"
MIN_DAYS = 14
ANSWER_DAYS = 7
MAX_COMMENT = 500
QUESTION = ("📊 Раз в месяц один вопрос: насколько игры полезны вам и малышу? "
            "Ответьте цифрой от 1 до 5 (5 — очень полезны). Можно добавить пару слов — что сделать лучше.")
SCORE = re.compile(r"^\s*([1-5])(?:\s*(?:/\s*5|из\s*5))?(?![\d.,:])[\s.,!:;—–-]*(.*)$", re.S)


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def month(day):
    return day.strftime("%Y-%m")


def due(con, child_id, channel, address, today):
    """True when this recipient's digest today should carry the question."""
    if os.environ.get(ENABLED_ENV) != "1" or not _has(con, "pulses"):
        return False
    if not 15 <= today.day <= 21:
        return False
    if con.execute("SELECT 1 FROM pulses WHERE channel = ? AND address = ? AND month = ?",
                   (channel, address, month(today))).fetchone():
        return False
    first = con.execute(
        "SELECT min(g.game_date) FROM games g JOIN deliveries d ON d.game_id = g.id "
        "WHERE g.child_id = ? AND d.status = 'sent'", (child_id,)).fetchone()[0]
    return bool(first) and (today - date.fromisoformat(first)).days >= MIN_DAYS


def asked(con, child_id, channel, address, today):
    con.execute("INSERT OR IGNORE INTO pulses(channel, address, child_id, month) VALUES (?, ?, ?, ?)",
                (channel, address, child_id, month(today)))


def answer(con, channel, address, text, now, notify):
    """The reply when the message answers an open question, else None.
    notify(text) records an owner-facing event."""
    if not _has(con, "pulses"):
        return None
    match = SCORE.match(str(text or ""))
    if not match:
        return None
    since = (now.astimezone(timezone.utc) - timedelta(days=ANSWER_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    row = con.execute(
        "SELECT p.id, c.name FROM pulses p LEFT JOIN children c ON c.id = p.child_id "
        "WHERE p.channel = ? AND p.address = ? AND p.score IS NULL AND p.asked_at >= ? ORDER BY p.id DESC LIMIT 1",
        (channel, str(address), since)).fetchone()
    if row is None:
        return None
    score, comment = int(match.group(1)), " ".join(match.group(2).split())[:MAX_COMMENT] or None
    con.execute("UPDATE pulses SET score = ?, comment = ?, answered_at = ? WHERE id = ?",
                (score, comment, now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), row[0]))
    notify(f"📊 Полезность: {score}/5 ({row[1] or '—'})" + (f" — «{comment}»" if comment else ""))
    if score <= 3 and not comment:
        return "Спасибо за честность 🙏 Если подскажете парой слов, что не так, — постараемся исправить."
    return "Спасибо! 🙏 Это помогает понять, что работает."


def summary(con, today, days=90):
    """(answered, asked, average, {score: n}) for questions asked in the last `days`."""
    if not _has(con, "pulses"):
        return 0, 0, None, {}
    since = (today - timedelta(days=days)).isoformat()
    asked_n = con.execute("SELECT count(*) FROM pulses WHERE date(asked_at) >= ?", (since,)).fetchone()[0]
    rows = con.execute("SELECT score, count(*) FROM pulses WHERE score IS NOT NULL AND date(asked_at) >= ? "
                       "GROUP BY score", (since,)).fetchall()
    dist = {s: n for s, n in rows}
    answered = sum(dist.values())
    average = sum(s * n for s, n in dist.items()) / answered if answered else None
    return answered, asked_n, average, dist
