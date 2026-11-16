"""Quarantine: a VK message the consumer could not handle is kept, not lost.

Cloudflare drops a queue message after its last retry. Before that happens the consumer
(src/feedback/queue_consumer.py) retries with growing pauses (RETRY_DELAYS) and, after
QUARANTINE_AFTER failed attempts, puts the message here as it arrived, acks it and tells
the owner. Nothing is analysed and nothing reaches the child's tables: the row is the
raw queue message, the error and the sender.

The owner sees them with /quarantine in the ops bot and asks for a replay with /replay N.
The ops bot holds no messaging credentials, so it only marks the row; the consumer,
which has them, replays it on its next loop through the same path as a fresh message.

The table is created by src/db/children.py (schema 22). Kept QUARANTINE_DAYS (src/ops/retention.py); removed with the family on erasure.
"""

import json
from datetime import datetime, timezone

QUARANTINE_AFTER = 3
RETRY_DELAYS = (5, 60, 300)  # seconds before attempt 2, 3, 4...
QUARANTINE_DAYS = 30
TEXT_PREVIEW = 80

def retry_delay(attempts):
    """The pause before the next attempt, after `attempts` deliveries (1 = the first)."""
    index = max(0, min(int(attempts or 1) - 1, len(RETRY_DELAYS) - 1))
    return RETRY_DELAYS[index]


def due(attempts):
    return int(attempts or 1) >= QUARANTINE_AFTER


def _fields(message):
    """event id, channel and sender from the raw message, when it can be read."""
    try:
        payload = json.loads(message.get("body") or "")
    except (TypeError, ValueError):
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    pick = lambda key: payload.get(key) if isinstance(payload.get(key), str) else None  # noqa: E731
    return pick("event_id"), pick("channel"), pick("address")


def put(con, message, error):
    """Keep the message; returns (row id, created). A redelivery of a kept event is not a
    second row."""
    event_id, channel, address = _fields(message)
    metadata = message.get("metadata") or {}
    content_type = (metadata.get("CF-Content-Type") or metadata.get("cf-content-type") or "json").lower()
    body = message.get("body")
    if not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False)
    if event_id:
        row = con.execute("SELECT id FROM quarantine WHERE event_id = ?", (event_id,)).fetchone()
        if row:
            return row[0], False
    cur = con.execute(
        "INSERT INTO quarantine (event_id, channel, address, body, content_type, error, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, channel, address, body, content_type, str(error)[:500], int(message.get("attempts") or 0)))
    con.commit()
    return cur.lastrowid, True


def preview(body):
    try:
        text = json.loads(body).get("raw_text") or ""
    except (TypeError, ValueError, AttributeError):
        text = body or ""
    text = " ".join(str(text).split())
    return text if len(text) <= TEXT_PREVIEW else text[:TEXT_PREVIEW - 1] + "…"


def alert_text(row_id, message, error):
    _, _, address = _fields(message)
    who = f"от VK-диалога …{address[-3:]}" if address else "без отправителя"
    return (f"Сообщение из VK не обработано и отложено в карантин (#{row_id}, {who}).\n"
            f"Причина: {str(error)[:300]}\n"
            f"Текст: {preview(message.get('body'))}\n\n"
            f"Когда причина исправлена: /replay {row_id}. Список: /quarantine")


def listing(con, limit=10):
    rows = con.execute(
        "SELECT id, at, address, error, body, replay_requested_at, replayed_at, replay_error FROM quarantine "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return "Карантин пуст: все сообщения из VK обработаны."
    lines = ["Карантин (последние):"]
    for row_id, at, address, error, body, requested, replayed, replay_error in rows:
        if replayed:
            state = "✅ обработано повторно"
        elif requested:
            state = "⏳ ждёт повтора"
        elif replay_error:
            state = f"🔴 повтор не удался: {replay_error[:80]}"
        else:
            state = f"⚠️ {error[:80]}"
        who = f"…{address[-3:]}" if address else "?"
        lines.append(f"#{row_id} {at[:16]} UTC · VK {who} · {state}\n   «{preview(body)}»")
    lines.append("\nОбработать заново: /replay N")
    return "\n".join(lines)


def request_replay(con, row_id):
    row = con.execute("SELECT replayed_at FROM quarantine WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return f"В карантине нет #{row_id}."
    if row[0]:
        return f"#{row_id} уже обработано повторно."
    con.execute("UPDATE quarantine SET replay_requested_at = ?, replay_error = NULL WHERE id = ?",
                (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), row_id))
    con.commit()
    return f"#{row_id}: обработчик отзывов повторит его в течение минуты и напишет результат."


def requested(con):
    return con.execute(
        "SELECT id, body, content_type FROM quarantine "
        "WHERE replay_requested_at IS NOT NULL AND replayed_at IS NULL ORDER BY id").fetchall()


def mark(con, row_id, error=None):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if error is None:
        con.execute("UPDATE quarantine SET replayed_at = ?, replay_error = NULL WHERE id = ?", (now, row_id))
    else:
        con.execute("UPDATE quarantine SET replay_requested_at = NULL, replay_error = ? WHERE id = ?",
                    (str(error)[:300], row_id))
    con.commit()
