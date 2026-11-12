#!/usr/bin/env python3
"""Nightly retention: personal data is kept only as long as docs/privacy.md says.

  - feedback texts and quotes from them: FEEDBACK_TEXT_DAYS, then blanked; the
    conclusions (observations, the child profile) stay;
  - a family with no active recipient and no activity for INACTIVE_DAYS: deleted;
  - an archive of a family that asked to be deleted: purged after ERASURE_GRACE_DAYS
    (src/bot/erasure.py);
  - service rows: an unfinished questionnaire, a rejected application, a queued or
    handled message, a stale VK visitor, an unused link code, owner-facing event texts.

One transaction per run. The owner gets a one-line summary with counts only (no names)
when something was removed. --dry-run reports the counts and changes nothing.
Deterministic code only.

    python3 src/ops/retention.py run [--dry-run]
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.bot import erasure  # noqa: E402
from src.db import children as ch  # noqa: E402

FEEDBACK_TEXT_DAYS = 180
INACTIVE_DAYS = 365
ONBOARDING_DAYS = 7
SERVICE_DAYS = 30
EVENT_TEXT_DAYS = 90
BLANK = ""


def _ago(now, days):
    return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _has(con, table):
    return erasure._has(con, table) is not None


def _run(con, sql, params=()):
    return con.execute(sql, params).rowcount


def inactive_families(con, now):
    """[(children ids, addresses)] with no active recipient and nothing for INACTIVE_DAYS."""
    cutoff = _ago(now, INACTIVE_DAYS)
    seen, found = set(), []
    for (child_id,) in con.execute("SELECT id FROM children ORDER BY id").fetchall():
        if child_id in seen:
            continue
        channel, address = con.execute(
            "SELECT channel, address FROM recipients WHERE child_id = ? LIMIT 1", (child_id,)).fetchone() or (None, None)
        if channel is None:
            kids, addrs = {child_id}, set()
        else:
            kids, addrs = erasure.family(con, channel, address)
        seen |= kids
        ids = erasure._in(kids)
        if con.execute(f"SELECT 1 FROM recipients WHERE child_id IN ({ids}) AND active = 1").fetchone():
            continue
        last = con.execute(
            f"SELECT max(t) FROM ("
            f" SELECT max(created_at) AS t FROM children WHERE id IN ({ids})"
            f" UNION ALL SELECT max(created_at) FROM recipients WHERE child_id IN ({ids})"
            f" UNION ALL SELECT max(d.sent_at) FROM deliveries d JOIN games g ON g.id = d.game_id"
            f"   WHERE g.child_id IN ({ids})"
            f" UNION ALL SELECT max(f.received_at) FROM feedback f JOIN games g ON g.id = f.game_id"
            f"   WHERE g.child_id IN ({ids}))").fetchone()[0]
        if last is None or str(last).replace("T", " ")[:19] < cutoff:
            found.append((kids, addrs))
    return found


def collect(con, now):
    """Apply the terms; return {what: count}. The caller commits or rolls back."""
    counts = {}
    text_cutoff = _ago(now, FEEDBACK_TEXT_DAYS)
    counts["feedback_texts"] = _run(
        con, "UPDATE feedback SET raw_text = ? WHERE raw_text != ? AND coalesce(received_at, analyzed_at, "
             "(SELECT game_date FROM games WHERE games.id = feedback.game_id)) < ?",
        (BLANK, BLANK, text_cutoff))
    counts["quotes"] = _run(
        con, "UPDATE observations SET evidence = ? WHERE evidence != ? AND created_at < ?", (BLANK, BLANK, text_cutoff))
    if _has(con, "wishes"):
        counts["quotes"] += _run(
            con, "UPDATE wishes SET evidence = NULL, chat_id = NULL, child_id = NULL "
                 "WHERE (evidence IS NOT NULL OR chat_id IS NOT NULL OR child_id IS NOT NULL) AND created_at < ?",
            (text_cutoff,))
    if _has(con, "exit_surveys"):
        counts["quotes"] += _run(con, "UPDATE exit_surveys SET text = NULL WHERE text IS NOT NULL AND at < ?",
                                 (text_cutoff,))

    if _has(con, "pulses"):
        counts["quotes"] += _run(con, "UPDATE pulses SET comment = NULL WHERE comment IS NOT NULL AND asked_at < ?",
                                 (text_cutoff,))

    families = inactive_families(con, now)
    for kids, addrs in families:
        erasure.remove(con, kids, addrs)
    counts["inactive_families"] = len(families)

    archives = 0
    if _has(con, "erasures"):
        for row in con.execute("SELECT * FROM erasures WHERE status = 'archived' AND purge_after <= ?",
                               (now.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall():
            erasure.purge(con, row)
            archives += 1
        # A confirmation nobody answered.
        _run(con, "DELETE FROM erasures WHERE status = 'confirming' AND requested_at < ?", (_ago(now, 1),))
    counts["archives"] = archives

    service = 0
    service += _run(con, "DELETE FROM onboarding WHERE updated_at < ?", (_ago(now, ONBOARDING_DAYS),))
    # A VK visitor who never became a family: no child, no application left.
    service += _run(
        con, "DELETE FROM members WHERE chat_id LIKE 'vk:%' AND joined_at < ? "
             "AND chat_id NOT IN (SELECT chat_id FROM onboarding) "
             "AND NOT EXISTS (SELECT 1 FROM recipients r WHERE 'vk:' || r.address = members.chat_id AND r.channel = 'vk') "
             "AND NOT EXISTS (SELECT 1 FROM applications a WHERE 'vk:' || a.address = members.chat_id "
             "AND a.status != 'rejected')",
        (_ago(now, SERVICE_DAYS),))
    if _has(con, "applications"):
        service += _run(con, "DELETE FROM applications WHERE status = 'rejected' AND decided_at < ?",
                        (_ago(now, SERVICE_DAYS),))
    if _has(con, "outbox"):
        service += _run(con, "DELETE FROM outbox WHERE coalesce(sent_at, created_at) < ?", (_ago(now, SERVICE_DAYS),))
    if _has(con, "inbound_events"):
        service += _run(con, "DELETE FROM inbound_events WHERE handled_at < ?", (_ago(now, SERVICE_DAYS),))
    if _has(con, "channel_links"):
        service += _run(con, "DELETE FROM channel_links WHERE status != 'approved' AND created_at < ?",
                        (_ago(now, SERVICE_DAYS),))
    service += _run(con, "UPDATE invites SET used_by = NULL WHERE used_by IS NOT NULL AND used_at < ?",
                    (_ago(now, EVENT_TEXT_DAYS),))
    service += _run(con, "DELETE FROM invites WHERE used_at IS NULL AND expires_at < ?", (_ago(now, EVENT_TEXT_DAYS),))
    # Owner-facing events keep their kind (the counts in /metrics) and lose names and chats.
    service += _run(con, "UPDATE user_events SET text = ?, chat_id = NULL "
                         "WHERE notified_at IS NOT NULL AND (text != ? OR chat_id IS NOT NULL) AND at < ?",
                    (BLANK, BLANK, _ago(now, EVENT_TEXT_DAYS)))
    counts["service"] = service
    return counts


def summary(counts):
    parts = [(counts.get("feedback_texts", 0), "тексты отзывов"), (counts.get("quotes", 0), "цитаты"),
             (counts.get("inactive_families", 0), "неактивные семьи"), (counts.get("archives", 0), "архивы удалений"),
             (counts.get("service", 0), "служебные записи")]
    shown = [f"{label} {n}" for n, label in parts if n]
    return ("🧹 Чистка по срокам хранения: " + ", ".join(shown)) if shown else None


def exits_report(db_path=None, days=7, now=None):
    """The week's departures for the Monday report: counts and reasons, no families."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    since = _ago(now, days)
    # Read-only, like the rest of the Monday report (its unit cannot write the data directory).
    con = sqlite3.connect(f"file:{db_path or ch.DB_PATH}?mode=ro", uri=True, timeout=10)
    try:
        if not _has(con, "erasures"):
            return None
        left = con.execute("SELECT count(*) FROM erasures WHERE archived_at >= ?", (since,)).fetchone()[0]
        back = con.execute("SELECT count(*) FROM erasures WHERE status = 'restored' AND closed_at >= ?",
                           (since,)).fetchone()[0]
        reasons = con.execute("SELECT reason, count(*) FROM exit_surveys WHERE at >= ? GROUP BY reason "
                              "ORDER BY count(*) DESC, reason", (since,)).fetchall()
    finally:
        con.close()
    if not (left or back):
        return f"Уходы за {days} дн.: нет"
    text = f"Уходы за {days} дн.: {left}, вернулись {back}"
    if reasons:
        text += "; причины: " + ", ".join(f"{r} ×{n}" for r, n in reasons)
    elif left:
        text += "; на опрос не ответили"
    return text


def run(db_path=None, now=None, dry_run=False):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    con = ch.connect_db(db_path)
    try:
        con.execute("PRAGMA busy_timeout = 10000")
        con.execute("BEGIN IMMEDIATE")
        counts = collect(con, now)
        text = summary(counts)
        if dry_run:
            con.rollback()
        else:
            if text:
                con.execute("INSERT INTO user_events(chat_id, kind, text) VALUES (NULL, 'retention', ?)", (text,))
            con.commit()
        return counts
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    counts = run(dry_run=args.dry_run)
    print(json.dumps({"dry_run": args.dry_run, **counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
