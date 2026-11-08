"""VK messages with a sender address: dialog (onboarding, commands) or feedback.

Called by the feedback consumer for raw VK events that carry the peer id. Each event is
handled once (inbound_events); the reply is stored with it, so a queue retry after a
failed send repeats the same text with the same VK random_id (VK drops the duplicate).
Also sends the outbox: messages to families queued by processes without a VK token
(the ops bot approving an application).
"""

import hashlib
import sqlite3

from src.bot import vk_dialog
from src.db import children as ch
from src.feedback import storage

MAX_OUTBOX_ATTEMPTS = 5


def random_id(key):
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF


def connect(db_path=None):
    con = ch.connect_db(db_path or storage.DB_PATH)
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def route(event_id, address, raw_text, send, db_path=None, dialog=None):
    """Return "feedback" when the event is feedback for the storage pipeline, else "handled".

    send(peer, text, random_id) delivers a reply; an exception propagates so the queue
    retries the event.
    """
    dialog = dialog or vk_dialog.VkDialog()
    con = connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        seen = con.execute(
            "SELECT reply FROM inbound_events WHERE channel = 'vk' AND event_id = ?", (event_id,)
        ).fetchone()
        if seen is not None:
            con.commit()
            reply = seen["reply"]
        else:
            result = dialog.handle(con, address, raw_text)
            if result is vk_dialog.FEEDBACK:
                con.rollback()
                return "feedback"
            reply = result
            con.execute(
                "INSERT INTO inbound_events(channel, event_id, address, reply) VALUES ('vk', ?, ?, ?)",
                (event_id, address, reply),
            )
            con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    if reply:
        send(address, reply, random_id(f"in:{event_id}"))
    return "handled"


def drain_outbox(send, db_path=None, limit=10):
    """Send queued VK messages. Marked sent only after VK accepted them; a failure is
    retried on the next call, up to MAX_OUTBOX_ATTEMPTS. Returns the number sent."""
    con = connect(db_path)
    sent = 0
    try:
        try:
            rows = con.execute(
                "SELECT id, address, text FROM outbox WHERE channel = 'vk' AND sent_at IS NULL "
                "AND attempts < ? ORDER BY id LIMIT ?",
                (MAX_OUTBOX_ATTEMPTS, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return 0  # not migrated yet
        for row in rows:
            try:
                send(row["address"], row["text"], random_id(f"out:{row['id']}"))
            except Exception as exc:  # noqa: BLE001 - one family failing must not stop the rest
                con.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                            (type(exc).__name__, row["id"]))
                con.commit()
                continue
            con.execute("UPDATE outbox SET sent_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ?",
                        (row["id"],))
            con.commit()
            sent += 1
    finally:
        con.close()
    return sent
