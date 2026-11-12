"""«Удалить наши данные»: a family leaves and takes its data with it, with a way back.

  1. «удалить наши данные» (or /forget in Telegram) asks for a confirmation that names
     the children it covers.
  2. «удалить» archives the family: every row about its children and its chats leaves
     the live tables for erasures.archive_json. Games, digests and greetings stop at
     once, and no report, metric or dashboard sees the family any more (they read the
     live tables only). One optional question follows: why are you leaving?
  3. Within ERASURE_GRACE_DAYS, «вернуть» from any chat of the family puts everything
     back (new row ids where the old ones were taken meanwhile) and the games resume.
     «удалить сейчас» purges at once.
  4. After the grace period the nightly retention run (src/ops/retention.py) purges the
     archive. The erasure row keeps only a hash of the address and the counts.

The family is everything reachable from the chat: its children, every chat that
receives them (a family in both VK and Telegram is one family) and their children. The
backups rotate out on their own schedule (docs/privacy.md): the family is told both dates.

The owner is told that a family left or came back, and why, never more than that. The
exit survey is stored without a child or a chat. Deterministic code only; runs in the
caller's transaction, the caller commits.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

ERASURE_GRACE_DAYS = 14
BACKUP_DAYS = 14  # GAME_OFFSITE_MAX_DAYS and GAME_BACKUP_RETAIN_DAYS (docs/privacy.md)
CONFIRM_TTL = timedelta(hours=1)

FORGET = {"/forget", "удалить наши данные", "удалите наши данные", "удалить мои данные",
          "удалить данные", "удалите данные", "забудьте нас"}
CONFIRM = {"удалить", "удаляйте", "подтверждаю"}
CANCEL = {"отмена", "нет", "/cancel", "не надо"}
RESTORE = {"вернуть", "вернуться", "вернуть данные", "/restore"}
PURGE_NOW = {"удалить сейчас", "удалить сразу", "удалите сейчас", "удалите сразу"}

REASONS = {"1": "игры не подходят", "2": "ребёнок вырос", "3": "нет времени",
           "4": "слишком часто", "5": "другое"}
SURVEY = ("Если не сложно — почему уходите? Ответьте цифрой:\n"
          + "\n".join(f"{k} — {v}" + (" (напишите пару слов)" if k == "5" else "") for k, v in REASONS.items())
          + "\nМожно не отвечать.")

# Archived tables: (table, how its rows are found, foreign keys to remap on restore).
# Order matters: parents before children on restore, the reverse on delete.
FAMILY_TABLES = (
    ("children", "id IN ({kids})", {}),
    ("recipients", "child_id IN ({kids})", {"child_id": "children"}),
    ("games", "child_id IN ({kids})", {"child_id": "children"}),
    ("deliveries", "game_id IN ({games})", {"game_id": "games"}),
    ("feedback", "game_id IN ({games})", {"game_id": "games"}),
    ("observations", "child_id IN ({kids}) OR game_id IN ({games}) OR feedback_id IN ({feedback})",
     {"feedback_id": "feedback", "child_id": "children", "game_id": "games"}),
    ("child_profiles", "child_id IN ({kids})", {"child_id": "children"}),
    ("wishes", "child_id IN ({kids}) OR feedback_id IN ({feedback}) OR chat_id IN ({keys})",
     {"feedback_id": "feedback", "child_id": "children"}),
    ("digests", "child_id IN ({kids})", {"child_id": "children"}),
    ("swaps", "child_id IN ({kids})", {"child_id": "children", "game_id": "games"}),
    ("applications", "child_id IN ({kids}) OR (channel || ':' || address) IN ({addrs})", {"child_id": "children"}),
    ("star_requests", "(channel || ':' || address) IN ({addrs})", {"child_id": "children"}),
    ("pulses", "(channel || ':' || address) IN ({addrs}) OR child_id IN ({kids})", {"child_id": "children"}),
    ("members", "chat_id IN ({keys})", {}),
    ("onboarding", "chat_id IN ({keys})", {}),
    ("channel_links", "(from_channel || ':' || from_address) IN ({addrs}) "
                      "OR (to_channel || ':' || coalesce(to_address, '')) IN ({addrs})", {}),
)
# Not archived, deleted: transient rows about these chats (messages queued to them,
# handled inbound events). Owner-facing events keep their kind (the counts) and lose
# their text and chat.
TRANSIENT = (
    ("outbox", "(channel || ':' || address) IN ({addrs})"),
    ("inbound_events", "(channel || ':' || address) IN ({addrs})"),
)


def normalize(text):
    return " ".join(str(text or "").casefold().replace("ё", "е").strip(" .!?,").split())


def hash_address(channel, address):
    return "sha256:" + hashlib.sha256(f"{channel}:{address}".encode()).hexdigest()


def member_key(channel, address):
    return address if channel == "telegram" else f"{channel}:{address}"


def _utc(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()


def _ids(con, sql, params=()):
    return {row[0] for row in con.execute(sql, params)}


def _in(values):
    """SQL literal list for an IN clause; values are ids or quoted strings we built."""
    if not values:
        return "NULL"
    return ", ".join(str(v) if isinstance(v, int) else "'" + str(v).replace("'", "''") + "'" for v in sorted(values))


def family(con, channel, address):
    """(children ids, {(channel, address)}) reachable from this chat."""
    addrs, kids = {(channel, str(address))}, set()
    while True:
        new_kids = set()
        for ch, addr in addrs:
            new_kids |= _ids(con, "SELECT child_id FROM recipients WHERE channel = ? AND address = ?", (ch, addr))
        new_addrs = set(addrs)
        for kid in new_kids:
            new_addrs |= {(r[0], r[1]) for r in con.execute(
                "SELECT channel, address FROM recipients WHERE child_id = ?", (kid,))}
        if new_kids == kids and new_addrs == addrs:
            return kids, addrs
        kids, addrs = new_kids | kids, new_addrs


def _scope(con, kids, addrs):
    games = _ids(con, f"SELECT id FROM games WHERE child_id IN ({_in(kids)})")
    feedback = _ids(con, f"SELECT id FROM feedback WHERE game_id IN ({_in(games)})")
    return {"kids": _in(kids), "games": _in(games), "feedback": _in(feedback),
            "keys": _in({member_key(c, a) for c, a in addrs}), "addrs": _in({f"{c}:{a}" for c, a in addrs})}


def snapshot(con, kids, addrs):
    """{table: [row dicts]} for the family, in FAMILY_TABLES order."""
    scope = _scope(con, kids, addrs)
    return {table: [dict(r) for r in con.execute(f"SELECT * FROM {table} WHERE {where.format(**scope)}")]
            for table, where, _ in FAMILY_TABLES if _has(con, table)}


def remove(con, kids, addrs):
    """Delete the family from the live tables. Returns {table: rows deleted}."""
    scope = _scope(con, kids, addrs)
    counts = {}
    for table, where in TRANSIENT:
        if _has(con, table):
            counts[table] = con.execute(f"DELETE FROM {table} WHERE {where.format(**scope)}").rowcount
    if _has(con, "user_events"):
        counts["user_events"] = con.execute(
            f"UPDATE user_events SET text = '', chat_id = NULL WHERE chat_id IN ({scope['keys']})").rowcount
    if _has(con, "model_runs"):  # the spend stays measured; the link to the child goes
        con.execute(f"UPDATE model_runs SET child_id = NULL WHERE child_id IN ({scope['kids']})")
    if _has(con, "invites"):
        con.execute(f"UPDATE invites SET used_by = NULL WHERE used_by IN ({scope['keys']})")
    for table, where, _ in reversed(FAMILY_TABLES):
        if _has(con, table):
            counts[table] = con.execute(f"DELETE FROM {table} WHERE {where.format(**scope)}").rowcount
    return counts


def put_back(con, archive):
    """Re-insert an archive; an id taken meanwhile gets a new one and its references follow."""
    remap = {}
    for table, _, fks in FAMILY_TABLES:
        rows = archive.get(table) or []
        mapping = remap.setdefault(table, {})
        for row in rows:
            row = dict(row)
            for column, parent in fks.items():
                if row.get(column) is not None:
                    row[column] = remap.get(parent, {}).get(row[column], row[column])
            old_id = row.get("id")
            if old_id is not None and con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (old_id,)).fetchone():
                row.pop("id")
            cols = list(row)
            cur = con.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row[c] for c in cols])
            if old_id is not None:
                mapping[old_id] = row.get("id", cur.lastrowid)


# --- the dialog --------------------------------------------------------------------

def open_erasure(con, channel, address):
    """The live erasure (confirming or archived) this chat belongs to, or None."""
    if not _has(con, "erasures"):
        return None
    return con.execute(
        "SELECT e.* FROM erasures e LEFT JOIN erasure_addresses a ON a.erasure_id = e.id "
        "WHERE e.status IN ('confirming', 'archived') AND ((e.channel = ? AND e.address = ?) "
        "OR (a.channel = ? AND a.address = ?)) ORDER BY e.id DESC LIMIT 1",
        (channel, address, channel, address),
    ).fetchone()


def handle(con, channel, address, text, now, notify):
    """The reply when this message is about erasure (a request, a confirmation, a message
    from an archived family), else None: the caller goes on as usual.
    notify(text, kind) records an owner-facing event."""
    address = str(address)
    words = normalize(text)
    current = open_erasure(con, channel, address)
    if current is not None and current["status"] == "confirming":
        if current["requested_at"] < _utc(now - CONFIRM_TTL):
            con.execute("DELETE FROM erasures WHERE id = ?", (current["id"],))
            current = None
        elif words in CONFIRM:
            return archive(con, current, now, notify)
        else:
            con.execute("DELETE FROM erasures WHERE id = ?", (current["id"],))
            if words in CANCEL:
                return "Хорошо, ничего не удаляем 🙂"
            return None
    if current is not None:
        return archived_message(con, current, channel, address, words, text, now, notify)
    if words in FORGET:
        return request(con, channel, address, now)
    return None


def request(con, channel, address, now):
    kids, addrs = family(con, channel, address)
    names = [r[0] for r in con.execute(f"SELECT name FROM children WHERE id IN ({_in(kids)}) ORDER BY id")]
    con.execute("INSERT INTO erasures(channel, address, requested_at, children) VALUES (?, ?, ?, ?)",
                (channel, address, _utc(now), len(kids)))
    what = (f"{', '.join(names)}, историю игр и ваши отзывы" if names
            else "всё, что вы успели написать (детей у нас пока нет)")
    other = " Это касается и второго мессенджера: семья подключена в обоих." if len({c for c, _ in addrs}) > 1 else ""
    return (f"Удалить данные семьи? Игры остановятся, мы сотрём {what}.{other}\n\n"
            f"{ERASURE_GRACE_DAYS} дней данные хранятся в архиве — можно вернуться словом «вернуть». "
            "Потом удаляются окончательно.\n\n"
            "Напишите «удалить», чтобы подтвердить, или «отмена».")


def _dates(row, now):
    purge = datetime.fromisoformat(row["purge_after"]).replace(tzinfo=timezone.utc)
    return purge.strftime("%d.%m.%Y"), (purge + timedelta(days=BACKUP_DAYS)).strftime("%d.%m.%Y")


def archive(con, row, now, notify):
    kids, addrs = family(con, row["channel"], row["address"])
    first = con.execute(f"SELECT min(created_at) FROM children WHERE id IN ({_in(kids)})").fetchone()[0]
    days_used = (now.astimezone(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(first)).days if first else 0
    span = con.execute(
        f"SELECT min(g.game_date), max(g.game_date) FROM games g JOIN deliveries d ON d.game_id = g.id "
        f"WHERE g.child_id IN ({_in(kids)}) AND d.status = 'sent'").fetchone()
    data = snapshot(con, kids, addrs)
    remove(con, kids, addrs)
    if not kids:  # nothing to come back to: a questionnaire or an application at most
        row = con.execute("SELECT * FROM erasures WHERE id = ?", (row["id"],)).fetchone()
        return purge_now(con, row, now, notify, quiet=True)
    purge_after = _utc(now + timedelta(days=ERASURE_GRACE_DAYS))
    con.execute(
        "UPDATE erasures SET status = 'archived', archived_at = ?, purge_after = ?, children = ?, survey = 'asked', "
        "first_game = ?, last_game = ?, archive_json = ? WHERE id = ?",
        (_utc(now), purge_after, len(kids), span[0], span[1],
         json.dumps({"days_used": max(days_used, 0), "rows": data}, ensure_ascii=False), row["id"]))
    for channel, address in addrs:
        con.execute("INSERT OR REPLACE INTO erasure_addresses(erasure_id, channel, address) VALUES (?, ?, ?)",
                    (row["id"], channel, address))
    notify(f"🗑 Семья удалила данные ({_channels(addrs)}, детей: {len(kids)}). "
           f"Архив до {purge_after[:10]}, потом окончательно.", "erasure")
    row = con.execute("SELECT * FROM erasures WHERE id = ?", (row["id"],)).fetchone()
    until, backups = _dates(row, now)
    return (f"Готово: игры остановлены, данные в архиве до {until}. Передумаете — напишите «вернуть». "
            "Удалить сразу — «удалить сейчас».\n"
            f"После {until} удалим окончательно; из резервных копий данные уйдут не позже {backups}. "
            "Переписка в этом чате хранится у самого мессенджера — её мы удалить не можем.\n\n" + SURVEY)


def _channels(addrs):
    names = {"vk": "ВК", "telegram": "Telegram"}
    return " + ".join(names.get(c, c) for c in sorted({c for c, _ in addrs}))


def archived_message(con, row, channel, address, words, text, now, notify):
    until, backups = _dates(row, now)
    if words in RESTORE:
        return restore(con, row, notify)
    if words in PURGE_NOW:
        return purge_now(con, row, now, notify)
    answers = row["survey"] == "asked_text" or (row["survey"] == "asked" and words in (*REASONS, "другое"))
    if answers:
        stored = json.loads(row["archive_json"] or "{}")
        if row["survey"] == "asked" and words in REASONS and words != "5":
            reason, free = REASONS[words], None
        elif row["survey"] == "asked" and words in ("5", "другое"):
            con.execute("UPDATE erasures SET survey = 'asked_text' WHERE id = ?", (row["id"],))
            return "Напишите, пожалуйста, пару слов — нам правда важно 🙏"
        else:
            reason, free = "другое", " ".join(str(text).split())[:500]
        con.execute("INSERT INTO exit_surveys(channel, reason, text, days_used) VALUES (?, ?, ?, ?)",
                    (channel, reason, free, stored.get("days_used")))
        con.execute("UPDATE erasures SET survey = 'answered' WHERE id = ?", (row["id"],))
        notify(f"📝 Причина ухода: {reason}" + (f" — «{free}»" if free else "")
               + f" (пользовались {stored.get('days_used', '?')} дн.)", "exit_survey")
        return f"Спасибо, передали 🙏 Если захотите вернуться до {until} — напишите «вернуть»."
    return (f"Игры остановлены, данные в архиве до {until}. «вернуть» — вернуться, "
            "«удалить сейчас» — удалить сразу.")


def restore(con, row, notify):
    stored = json.loads(row["archive_json"] or "{}")
    put_back(con, stored.get("rows") or {})
    con.execute("UPDATE erasures SET status = 'restored', closed_at = CURRENT_TIMESTAMP, archive_json = NULL "
                "WHERE id = ?", (row["id"],))
    con.execute("DELETE FROM erasure_addresses WHERE erasure_id = ?", (row["id"],))
    names = [r["name"] for r in (stored.get("rows") or {}).get("children", [])]
    notify(f"↩️ Семья вернулась из архива (детей: {len(names)})", "erasure_restore")
    return (f"С возвращением! 🎉 Всё восстановили: {', '.join(names) or 'данные'}. "
            "Игры снова будут приходить с ближайшего утра.")


def purge(con, row):
    """Drop the archive for good; the row keeps a hash of the address and the counts."""
    con.execute("DELETE FROM erasure_addresses WHERE erasure_id = ?", (row["id"],))
    con.execute(
        "UPDATE erasures SET status = 'purged', closed_at = CURRENT_TIMESTAMP, archive_json = NULL, "
        "address = ? WHERE id = ?", (hash_address(row["channel"], row["address"]), row["id"]))


def purge_now(con, row, now, notify, quiet=False):
    purge(con, row)
    if not quiet:
        notify("🗑 Семья попросила удалить сразу: архив стёрт.", "erasure_purge")
    backups = (now + timedelta(days=BACKUP_DAYS)).strftime("%d.%m.%Y")
    return ("Удалили 🤍 Из резервных копий данные уйдут не позже "
            f"{backups}. Спасибо, что были с нами. Захотите вернуться — просто напишите сюда.")
