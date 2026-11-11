"""A family already subscribed in one messenger adds the other one.

    1. In the first channel the parent asks ("подключить телеграм" in VK, /link_vk in
       Telegram) and gets a one-time code, valid CODE_TTL_HOURS.
    2. In the second channel the parent redeems it (the t.me link with start=link_<code>,
       or "код <code>" in the VK community messages). Holding the code proves the same
       person controls both chats, so nobody can attach to another family's child.
    3. The owner approves or rejects (ops bot buttons). Only then the second channel is
       added as a recipient of the same children: no new child is created.

Deterministic code only. Runs in the caller's transaction; the caller commits.
Messages to families go through the outbox; owner-facing events through user_events.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

CODE_TTL_HOURS = 24
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: it is read and retyped
CODE_LENGTH = 8
START_PREFIX = "link_"
VK_CODE = re.compile(r"^\s*код\s*[:\-]?\s*([A-Za-z0-9]{%d})\s*$" % CODE_LENGTH, re.IGNORECASE)
LABEL = {"vk": "ВКонтакте", "telegram": "Telegram"}
BOT_USERNAME_KEY = "telegram_bot_username"


def hash_code(code):
    """Same hashing as the product bot's invites: only the hash is stored."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def notify_owner(con, chat_id, kind, text):
    """An owner-facing event; the ops bot forwards it (src/ops/manual_bot.py)."""
    con.execute("INSERT INTO user_events(chat_id, kind, text) VALUES (?, ?, ?)", (chat_id, kind, text))


def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def _utc(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def describe(channel, address):
    return f"ВК vk.com/id{address}" if channel == "vk" else f"Telegram chat {address}"


def children_of(con, channel, address):
    return con.execute(
        "SELECT c.id, c.name, c.birth_date FROM recipients r JOIN children c ON c.id = r.child_id "
        "WHERE r.channel = ? AND r.address = ? AND c.active = 1 ORDER BY c.id",
        (channel, address),
    ).fetchall()


def bot_username(con):
    row = con.execute("SELECT value FROM migration_meta WHERE key = ?", (BOT_USERNAME_KEY,)).fetchone()
    return row[0] if row and row[0] else ""


def remember_bot_username(con, username):
    """The product bot records its name so the VK side can build the t.me link."""
    if username:
        con.execute(
            "INSERT INTO migration_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (BOT_USERNAME_KEY, username),
        )


def issue(con, from_channel, from_address, to_channel, now=None, code=None):
    """A code for a subscribed family. Returns the reply for the parent."""
    if not children_of(con, from_channel, from_address):
        return None
    already = con.execute(
        "SELECT 1 FROM recipients r JOIN recipients o ON o.child_id = r.child_id "
        "WHERE r.channel = ? AND r.address = ? AND o.channel = ? LIMIT 1",
        (from_channel, from_address, to_channel),
    ).fetchone()
    if already:
        return f"Игры уже приходят и в {LABEL[to_channel]} 🙂"
    if con.execute(
        "SELECT 1 FROM channel_links WHERE from_channel = ? AND from_address = ? AND status = 'requested'",
        (from_channel, from_address),
    ).fetchone():
        return "Заявка на подключение второго мессенджера уже у нас — скоро подтвердим 🙏"
    con.execute(
        "UPDATE channel_links SET status = 'replaced' WHERE from_channel = ? AND from_address = ? "
        "AND status = 'issued'",
        (from_channel, from_address),
    )
    code = code or new_code()
    now = now or datetime.now(timezone.utc)
    con.execute(
        "INSERT INTO channel_links(code_hash, from_channel, from_address, to_channel, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (hash_code(code), from_channel, from_address, to_channel, _utc(now + timedelta(hours=CODE_TTL_HOURS))),
    )
    if to_channel == "telegram":
        username = bot_username(con)
        where = (f"https://t.me/{username}?start={START_PREFIX}{code}" if username
                 else f"нашему Telegram-боту команду: /start {START_PREFIX}{code}")
        return (f"Чтобы игры приходили ещё и в Telegram, откройте ссылку в Telegram и нажмите «Старт»:\n{where}\n\n"
                f"Ссылка одноразовая, действует {CODE_TTL_HOURS} часа. Здесь всё останется как было.")
    return ("Чтобы игры приходили ещё и во ВКонтакте, напишите в сообщения нашего сообщества ВКонтакте:\n"
            f"код {code}\n\nКод одноразовый, действует {CODE_TTL_HOURS} часа. Здесь всё останется как было.")


def redeem(con, code, to_channel, to_address, now=None):
    """The second channel presents the code. Returns the reply for the parent."""
    row = con.execute("SELECT * FROM channel_links WHERE code_hash = ?", (hash_code(code.upper()),)).fetchone()
    if (row is None or row["status"] != "issued" or row["to_channel"] != to_channel
            or row["expires_at"] < _utc(now)):
        return "Код недействителен или устарел. Попросите новый там, где уже получаете игры."
    kids = children_of(con, row["from_channel"], row["from_address"])
    if not kids:
        return "Код недействителен или устарел. Попросите новый там, где уже получаете игры."
    con.execute(
        "UPDATE channel_links SET status = 'requested', to_address = ?, requested_at = ? WHERE id = ?",
        (to_address, _utc(now), row["id"]),
    )
    own = children_of(con, to_channel, to_address)
    names = ", ".join(k["name"] for k in kids)
    text = (f"🔗 Заявка #{row['id']}: существующая семья добавляет второй канал, новых детей не создаётся.\n"
            f"Уже получает: {describe(row['from_channel'], row['from_address'])} (дети: {names})\n"
            f"Хочет добавить: {describe(to_channel, to_address)}")
    if own:
        text += f"\n⚠️ У этого {LABEL[to_channel]} уже есть свои дети: {', '.join(k['name'] for k in own)}"
    notify_owner(con, to_address, f"link:{row['id']}", text)
    return ("Спасибо! 🙏 Заявка на подключение отправлена. Как только подтвердим, игры для "
            f"{names} начнут приходить и сюда — мы напишем.")


def decide(con, link_id, approve):
    """The owner's decision (ops bot button). Returns the text for the owner."""
    row = con.execute("SELECT * FROM channel_links WHERE id = ?", (link_id,)).fetchone()
    if row is None:
        return "Заявка не найдена."
    if row["status"] != "requested":
        return f"Заявка #{link_id} уже {'одобрена' if row['status'] == 'approved' else 'закрыта'}."
    to = (row["to_channel"], row["to_address"])
    if not approve:
        con.execute("UPDATE channel_links SET status = 'rejected', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (link_id,))
        queue(con, *to, "К сожалению, сейчас не получается подключить второй мессенджер. "
                        "Игры продолжат приходить туда, где вы их уже получаете 💛")
        return f"❌ Заявка #{link_id} на второй канал отклонена."
    kids = children_of(con, row["from_channel"], row["from_address"])
    for kid in kids:
        con.execute("INSERT OR IGNORE INTO recipients(child_id, channel, address) VALUES (?, ?, ?)",
                    (kid["id"], *to))
    if row["to_channel"] == "telegram":
        con.execute("INSERT OR IGNORE INTO members(chat_id) VALUES (?)", (row["to_address"],))
    con.execute("UPDATE channel_links SET status = 'approved', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
                (link_id,))
    names = ", ".join(k["name"] for k in kids)
    queue(con, *to, f"Готово! 🎉 Игры для {names} теперь будут приходить и сюда, "
                    f"и в {LABEL[row['from_channel']]}. Отзыв можно писать там, где удобнее.")
    queue(con, row["from_channel"], row["from_address"],
          f"Готово! Игры для {names} теперь приходят и в {LABEL[row['to_channel']]} 🎉")
    return f"✅ Заявка #{link_id} одобрена: {describe(*to)} получает игры для {names}."


def queue(con, channel, address, text):
    con.execute("INSERT INTO outbox(channel, address, text) VALUES (?, ?, ?)", (channel, address, text))


def vk_code(text):
    """The code from a VK message "код ABCD2345", or None."""
    match = VK_CODE.match(text or "")
    return match.group(1).upper() if match else None
