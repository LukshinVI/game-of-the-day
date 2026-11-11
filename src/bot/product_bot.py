#!/usr/bin/env python3
"""Product Telegram bot: self-service onboarding and feedback for caregivers.

This is the bot families talk to. It is separate from the owner's ops bot
(src/ops/manual_bot.py), which stays private: alerts and manual delivery only.

Access is by invitation. The owner sends /invite to this bot and gets a one-time
link t.me/<bot>?start=<code> (valid INVITE_TTL_DAYS). Whoever opens it becomes a
member and fills a short questionnaire: the child's name and birth date, then a
confirmation showing the computed age. The child and a Telegram recipient for this
chat are created; the next preparation run picks them up.

Members:
  /add_child   another child
  /children    who is subscribed, with the current age
  /pause       stop the daily games for this chat (from the next game)
  /resume      start them again
  /faq         what it is, who picks the games, what data is kept (src/bot/faq.txt)
  /help
  any other text: feedback on the latest game sent to this chat

Owner (GAME_BOT_OWNER_ID) additionally:
  /invite      a new one-time invite link
  /members     members and their children
  /block ID    disable a member and the children reached only through them

Limits (env): children per chat, active children in total (each is a model run a
day), feedback per chat per day. Strangers get the closed notice once a day.

Deterministic code only: no model call here. State lives in SQLite (invites are
stored as hashes, the questionnaire survives a restart). Outbound long polling,
no inbound port. Feedback is persisted with an explicit game id, so a chat's
answer can never attach to another family's game.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.bot import channel_link  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.feedback import insights, reactions, storage  # noqa: E402

DB_PATH = ch.DB_PATH
API = "https://api.telegram.org"
MOSCOW = ZoneInfo("Europe/Moscow")

INVITE_TTL_DAYS = 7
MAX_NAME_CHARS = 40
# Matches the candidate schema (age_months <= 36): the games are designed for 0-3 years.
MAX_AGE_MONTHS = 36
MAX_FEEDBACK_CHARS = 4000
PREPARATION_HOUR_MSK = 7
DELIVERY_TIME_MSK = "10:00"
# Abuse limits. Every active child costs one model run a day, so the caps bound
# the spend even if an invite leaks or a member misbehaves.
MAX_CHILDREN_PER_CHAT = int(os.environ.get("GAME_MAX_CHILDREN_PER_CHAT", "3"))
MAX_ACTIVE_CHILDREN = int(os.environ.get("GAME_MAX_ACTIVE_CHILDREN", "20"))
MAX_FEEDBACK_PER_DAY = int(os.environ.get("GAME_MAX_FEEDBACK_PER_DAY", "20"))
STRANGER_REPLY_EVERY_SECONDS = 24 * 3600
POLL_TIMEOUT_SECONDS = 30
EXIT_AFTER_FAILING_SECONDS = 600
# An unfinished "add a child" questionnaire stops capturing messages after this long,
# so a family that started /add_child and walked away does not lose its feedback.
ONBOARDING_TTL = timedelta(hours=1)
ADD_CHILD_DROPPED = "Добавление ребёнка не закончено и отменено — /add_child, чтобы начать заново."

YES = {"да", "+", "верно", "ок", "ok", "yes", "д"}
NO = {"нет", "-", "no", "н", "заново"}

CLOSED = "Это закрытый бот. Чтобы подключиться, попросите пригласительную ссылку у того, кто вас пригласил."
# The family-facing commands: the help text and the Telegram menu are both built from
# this list (the menu is set at startup), so the menu can never drift from the code.
COMMANDS = [
    ("children", "кто подписан"),
    ("add_child", "добавить ребёнка"),
    ("pause", "приостановить игры"),
    ("resume", "возобновить игры"),
    ("link_vk", "получать игры ещё и во ВКонтакте"),
    ("idea", "предложить, что улучшить в сервисе"),
    ("faq", "частые вопросы"),
]
HELP = (
    f"Каждый день в {DELIVERY_TIME_MSK} по Москве я присылаю одну развивающую игру по возрасту ребёнка.\n"
    "Ответьте на сообщение с игрой — как прошло, было ли легко или сложно, что понравилось. "
    "Следующие игры подстроятся под ребёнка.\n\n"
    + "\n".join(f"/{name} — {about}" for name, about in COMMANDS)
)
OWNER_HELP = (
    "\n\nВладелец:\n/invite — новая пригласительная ссылка\n/members — участники\n"
    "/block <chat_id> — отключить участника и его детей"
)
FAQ = (Path(__file__).with_name("faq.txt")).read_text(encoding="utf-8").strip()
WELCOME = (
    "Здравствуйте! 👋 Я присылаю одну развивающую игру в день — "
    "короткую, по возрасту ребёнка, из того, что есть дома.\n\n"
    "Подключение займёт минуту: имя ребёнка и дата рождения. "
    "Ответы на частые вопросы (что это, кто подбирает игры, какие данные храним): /faq"
)
ASK_NAME = "Как зовут ребёнка? (можно сокращённо или прозвище)"
ASK_BIRTH = "Дата рождения? Например: 15.03.2025"


def hash_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_birth_date(text, today):
    """Accept DD.MM.YYYY, D.M.YYYY or YYYY-MM-DD. Return ISO date or raise ValueError."""
    text = text.strip().replace("/", ".")
    try:
        if "-" in text:
            value = date.fromisoformat(text)
        else:
            day, month, year = (int(part) for part in text.split("."))
            value = date(year, month, day)
    except (ValueError, TypeError):
        raise ValueError("Не получилось разобрать дату. Напишите так: 15.03.2025") from None
    if value > today:
        raise ValueError("Дата рождения в будущем — проверьте, пожалуйста.")
    if age_months(value, today) > MAX_AGE_MONTHS:
        raise ValueError("Игры рассчитаны на детей до 3 лет.")
    return value.isoformat()


def age_months(birth, today):
    months = (today.year - birth.year) * 12 + today.month - birth.month
    if today.day < birth.day:
        months -= 1
    return months


def months_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "месяц"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "месяца"
    return "месяцев"


def describe_age(birth_iso, today):
    months = age_months(date.fromisoformat(birth_iso), today)
    return f"{months} {months_word(months)}"


class ProductBot:
    def __init__(self, db_path, owner_id, username="", now=None, new_code=None):
        self.db_path = db_path
        self.owner_id = str(owner_id) if owner_id else ""
        self.username = username
        self.now = now or (lambda: datetime.now(MOSCOW))
        self.new_code = new_code or (lambda: secrets.token_urlsafe(12))
        self.stranger_replies = {}  # chat_id -> time of the last "closed" notice (memory only)

    # --- storage -------------------------------------------------------------

    def connect(self):
        con = ch.connect_db(self.db_path)
        con.execute("PRAGMA busy_timeout = 10000")
        return con

    @staticmethod
    def member(con, chat_id):
        return con.execute("SELECT * FROM members WHERE chat_id = ?", (chat_id,)).fetchone()

    @staticmethod
    def step(con, chat_id):
        return con.execute("SELECT * FROM onboarding WHERE chat_id = ?", (chat_id,)).fetchone()

    @staticmethod
    def set_step(con, chat_id, step, name=None, birth_date=None):
        con.execute(
            "INSERT INTO onboarding(chat_id, step, name, birth_date, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(chat_id) DO UPDATE SET "
            "step = excluded.step, name = excluded.name, birth_date = excluded.birth_date, "
            "updated_at = excluded.updated_at",
            (chat_id, step, name, birth_date),
        )

    @staticmethod
    def chat_children(con, chat_id):
        return con.execute(
            "SELECT c.id, c.name, c.birth_date, r.active FROM recipients r "
            "JOIN children c ON c.id = r.child_id "
            "WHERE r.channel = 'telegram' AND r.address = ? AND c.active = 1 ORDER BY c.id",
            (chat_id,),
        ).fetchall()

    # --- dispatch ------------------------------------------------------------

    def handle(self, update):
        """Return the reply text for an update, or None when it must be ignored."""
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") != "private" or sender.get("is_bot"):
            return None
        chat_id = str(chat.get("id"))
        text = (message.get("text") or "").strip()
        label = None if text else reactions.telegram_label(message)
        if label:
            caption = (message.get("caption") or "").strip()
            text = f"{caption} {label}".strip()
        if not text:
            return None

        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            reply = self.dispatch(con, chat_id, text, message, sender)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return reply

    def dispatch(self, con, chat_id, text, message, sender):
        parts = text.split()
        command = parts[0].split("@")[0].lower() if text.startswith("/") else ""
        is_owner = chat_id == self.owner_id
        member = self.member(con, chat_id)

        if is_owner and command == "/invite":
            return self.invite(con)
        if is_owner and command == "/members":
            return self.members(con)
        if is_owner and command == "/block":
            return self.block(con, parts[1] if len(parts) > 1 else "")

        if member is not None and member["blocked_at"]:
            return None  # silently: a blocked member gets no signal to probe with
        if command == "/start" and len(parts) > 1 and parts[1].startswith(channel_link.START_PREFIX):
            # A family subscribed in VK adds this chat: works before the chat is a member.
            return channel_link.redeem(con, parts[1][len(channel_link.START_PREFIX):], "telegram", chat_id)
        if member is None:
            if command == "/start" and len(parts) > 1:
                return self.join(con, chat_id, parts[1], sender)
            if is_owner and command == "/start":
                return self.join_owner(con, chat_id, sender)
            return self.closed(chat_id)

        if command == "/start":
            if not self.chat_children(con, chat_id) and self.step(con, chat_id) is None:
                self.set_step(con, chat_id, "name")
                return "С возвращением! " + ASK_NAME
            return self.help(is_owner)
        if command == "/help":
            return self.help(is_owner)
        if command == "/faq":
            pending = self.step(con, chat_id)
            prompts = {"name": ASK_NAME, "birth_date": ASK_BIRTH, "confirm": "Подтвердите: «да» или «нет»."}
            return FAQ + (f"\n\n— Продолжим подключение. {prompts[pending['step']]}" if pending else "")
        if command == "/add_child":
            limit = self.child_limit(con, chat_id)
            if limit:
                return limit
            self.set_step(con, chat_id, "name")
            return ASK_NAME
        if command == "/cancel":
            con.execute("DELETE FROM onboarding WHERE chat_id = ?", (chat_id,))
            return "Отменено."
        if command == "/children":
            return self.list_children(con, chat_id)
        if command in ("/pause", "/resume"):
            return self.set_active(con, chat_id, command == "/resume")
        if command == "/link_vk":
            return (channel_link.issue(con, "telegram", chat_id, "vk")
                    or "Сначала подпишите ребёнка — /add_child, потом можно подключить ВКонтакте.")
        if command == "/idea":
            return self.idea(con, chat_id, text.split(maxsplit=1)[1] if len(parts) > 1 else "")
        if command:
            # Say so: a bare repeat of the help reads as the bot being stuck.
            return f"Такой команды у меня нет: {parts[0][:40]}\n\n" + self.help(is_owner)

        current = self.step(con, chat_id)
        if current is not None and self.abandoned(con, chat_id, current, text, message):
            # A family with a child already subscribed wrote something that is not an
            # answer: it is feedback. Drop the questionnaire instead of eating the message.
            con.execute("DELETE FROM onboarding WHERE chat_id = ?", (chat_id,))
            reply = self.feedback(con, chat_id, text, message)
            return f"{reply}\n\n{ADD_CHILD_DROPPED}" if reply else None
        if current is not None:
            if reactions.is_reaction(text):
                return "Ответьте, пожалуйста, текстом 🙂"
            return self.questionnaire(con, chat_id, current, text)
        return self.feedback(con, chat_id, text, message)

    def abandoned(self, con, chat_id, current, text, message):
        """Is this message feedback rather than an answer to an unfinished questionnaire?

        Only for a chat that already has a child (a new family must finish signing up):
        the questionnaire is older than ONBOARDING_TTL, the message replies to a game,
        or it is too long to be a name, a date or "да".
        """
        if not self.chat_children(con, chat_id):
            return False
        cutoff = (self.now().astimezone(timezone.utc) - ONBOARDING_TTL).strftime("%Y-%m-%d %H:%M:%S")
        return (
            (current["updated_at"] or "") < cutoff
            or bool(message.get("reply_to_message"))
            or len(text) > MAX_NAME_CHARS
        )

    def help(self, is_owner):
        return HELP + (OWNER_HELP if is_owner else "")

    # --- invites -------------------------------------------------------------

    def invite(self, con):
        code = self.new_code()
        expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        con.execute("INSERT INTO invites(code_hash, expires_at) VALUES (?, ?)", (hash_code(code), expires))
        link = f"https://t.me/{self.username}?start={code}" if self.username else f"/start {code}"
        return (
            f"Одноразовая ссылка, действует {INVITE_TTL_DAYS} дней:\n{link}\n\n"
            "Перешлите её одному человеку: после первого использования она перестаёт работать."
        )

    def members(self, con):
        rows = con.execute(
            "SELECT m.chat_id, m.display_name, m.joined_at, "
            "(SELECT group_concat(c.name || ' ' || c.birth_date || CASE WHEN r.active THEN '' ELSE ' (пауза)' END, ', ') "
            " FROM recipients r JOIN children c ON c.id = r.child_id "
            " WHERE r.channel = 'telegram' AND r.address = m.chat_id) AS kids "
            "FROM members m WHERE m.blocked_at IS NULL ORDER BY m.joined_at"
        ).fetchall()
        if not rows:
            return "Участников пока нет. /invite — пригласить."
        return "\n".join(
            f"{r['display_name'] or '—'} (chat {r['chat_id']}, с {r['joined_at'][:10]}): {r['kids'] or 'анкета не заполнена'}"
            for r in rows
        )

    def join(self, con, chat_id, code, sender):
        row = con.execute(
            "SELECT id, expires_at, used_at FROM invites WHERE code_hash = ?", (hash_code(code),)
        ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] < utcnow():
            return "Ссылка недействительна или уже использована. Попросите новую."
        con.execute("UPDATE invites SET used_at = ?, used_by = ? WHERE id = ?", (utcnow(), chat_id, row["id"]))
        self.add_member(con, chat_id, row["id"], sender)
        return WELCOME + "\n\n" + ASK_NAME

    def join_owner(self, con, chat_id, sender):
        self.add_member(con, chat_id, None, sender)
        return "Вы владелец бота. Можно подписать своего ребёнка или пригласить других: /invite\n\n" + ASK_NAME

    def add_member(self, con, chat_id, invite_id, sender):
        display = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))[:80]
        con.execute(
            "INSERT INTO members(chat_id, invite_id, display_name) VALUES (?, ?, ?)",
            (chat_id, invite_id, display or None),
        )
        self.set_step(con, chat_id, "name")
        if chat_id != self.owner_id:
            self.notify_owner(f"👋 Новый участник: {display or '—'} (chat {chat_id})", con, chat_id, "join")

    def who(self, con, chat_id):
        member = self.member(con, chat_id)
        name = member["display_name"] if member is not None and member["display_name"] else "—"
        return f"{name} (chat {chat_id})"

    @staticmethod
    def notify_owner(text, con, chat_id, kind):
        """Record an owner-facing event; the ops bot forwards it (src/ops/manual_bot.py)."""
        con.execute(
            "INSERT INTO user_events(chat_id, kind, text) VALUES (?, ?, ?)", (chat_id, kind, text)
        )

    # --- questionnaire -------------------------------------------------------

    def questionnaire(self, con, chat_id, current, text):
        today = self.now().date()
        if current["step"] == "name":
            name = " ".join(text.split())
            if not name or len(name) > MAX_NAME_CHARS:
                return f"Имя — до {MAX_NAME_CHARS} символов. " + ASK_NAME
            if any(r["name"].casefold() == name.casefold() for r in self.chat_children(con, chat_id)):
                return (
                    f"{name} у вас уже подписан(а). Если это другой ребёнок, напишите имя иначе, "
                    "чтобы сообщения можно было различить (например, «Маша младшая»). /cancel — отменить."
                )
            self.set_step(con, chat_id, "birth_date", name=name)
            return ASK_BIRTH

        if current["step"] == "birth_date":
            try:
                birth = parse_birth_date(text, today)
            except ValueError as exc:
                return str(exc)
            self.set_step(con, chat_id, "confirm", name=current["name"], birth_date=birth)
            shown = date.fromisoformat(birth).strftime("%d.%m.%Y")
            return (
                f"{current['name']}, {shown} — сейчас {describe_age(birth, today)}. Всё верно?\n"
                "Ответьте «да» или «нет»."
            )

        answer = text.lower().strip(" .!")
        if answer in NO:
            self.set_step(con, chat_id, "name")
            return "Давайте заново. " + ASK_NAME
        if answer not in YES:
            return "Ответьте «да» или «нет»."

        limit = self.child_limit(con, chat_id)
        if limit:
            con.execute("DELETE FROM onboarding WHERE chat_id = ?", (chat_id,))
            return limit
        child_id = con.execute(
            "INSERT INTO children(name, birth_date) VALUES (?, ?)", (current["name"], current["birth_date"])
        ).lastrowid
        con.execute(
            "INSERT INTO recipients(child_id, channel, address) VALUES (?, 'telegram', ?)", (child_id, chat_id)
        )
        con.execute("DELETE FROM onboarding WHERE chat_id = ?", (chat_id,))
        self.notify_owner(
            f"👶 Подписан ребёнок: {current['name']}, {describe_age(current['birth_date'], today)} (chat {chat_id})",
            con, chat_id, "child",
        )
        when = "сегодня" if self.now().hour < PREPARATION_HOUR_MSK else "завтра"
        return (
            f"Готово! Первая игра для {current['name']} придёт {when} в {DELIVERY_TIME_MSK} по Москве.\n\n" + HELP
        )

    # --- limits and blocking -------------------------------------------------

    def closed(self, chat_id):
        """The closed notice at most once a day per stranger; nothing is stored."""
        now = time.monotonic()
        last = self.stranger_replies.get(chat_id)
        if last is not None and now - last < STRANGER_REPLY_EVERY_SECONDS:
            return None
        if len(self.stranger_replies) > 10000:  # a flood of new ids must not grow memory
            self.stranger_replies.clear()
        self.stranger_replies[chat_id] = now
        return CLOSED

    def child_limit(self, con, chat_id):
        if len(self.chat_children(con, chat_id)) >= MAX_CHILDREN_PER_CHAT:
            return f"В одном чате можно подписать до {MAX_CHILDREN_PER_CHAT} детей."
        total = con.execute("SELECT count(*) FROM children WHERE active = 1").fetchone()[0]
        if total >= MAX_ACTIVE_CHILDREN:
            self.notify_owner(
                f"⚠️ Достигнут лимит детей ({MAX_ACTIVE_CHILDREN}): chat {chat_id} не смог подписаться. "
                "Поднять: GAME_MAX_ACTIVE_CHILDREN в product-bot.env.",
                con, chat_id, "limit",
            )
            return "Сейчас мест нет — я сообщил владельцу. Попробуйте позже."
        return None

    def block(self, con, target):
        target = target.strip()
        if not target.lstrip("-").isdigit():
            return "Использование: /block <chat_id> (из /members)"
        if target == self.owner_id:
            return "Себя заблокировать нельзя."
        if self.member(con, target) is None:
            return "Такого участника нет."
        con.execute("UPDATE members SET blocked_at = CURRENT_TIMESTAMP WHERE chat_id = ?", (target,))
        con.execute("DELETE FROM onboarding WHERE chat_id = ?", (target,))
        con.execute("UPDATE recipients SET active = 0 WHERE channel = 'telegram' AND address = ?", (target,))
        # Children reached only through this chat stop costing model runs.
        con.execute(
            "UPDATE children SET active = 0 WHERE id IN (SELECT child_id FROM recipients "
            "WHERE channel = 'telegram' AND address = ?) AND NOT EXISTS (SELECT 1 FROM recipients r "
            "WHERE r.child_id = children.id AND r.active = 1)",
            (target,),
        )
        self.notify_owner(f"⛔ chat {target} заблокирован", con, target, "block")
        return f"Участник {target} заблокирован, его игры остановлены."

    # --- member commands -----------------------------------------------------

    def list_children(self, con, chat_id):
        rows = self.chat_children(con, chat_id)
        if not rows:
            return "Пока никто не подписан. /add_child — добавить ребёнка."
        today = self.now().date()
        return "\n".join(
            f"{r['name']} — {describe_age(r['birth_date'], today)}" + ("" if r["active"] else " (на паузе)")
            for r in rows
        )

    def set_active(self, con, chat_id, active):
        changed = con.execute(
            "UPDATE recipients SET active = ? WHERE channel = 'telegram' AND address = ? AND active = ?",
            (int(active), chat_id, int(not active)),
        ).rowcount
        if not changed:
            return "Игры уже идут." if active else "Игры уже на паузе (или ещё не подписаны)."
        self.notify_owner(
            f"{'▶️' if active else '⏸'} {self.who(con, chat_id)}: {'возобновил(а)' if active else 'поставил(а) на паузу'} игры",
            con, chat_id, "resume" if active else "pause",
        )
        if active:
            return "Игры возобновлены с ближайшего утра."
        return "Пауза. Уже подготовленная на сегодня игра может прийти, дальше — тишина. /resume — вернуть."

    # --- wishes ---------------------------------------------------------------

    def idea(self, con, chat_id, text):
        text = " ".join(text.split())[:500]
        if not text:
            return ("Напишите пожелание сразу после команды, например:\n"
                    "/idea хочется больше игр на улице")
        recent = con.execute(
            "SELECT count(*) FROM wishes WHERE chat_id = ? AND created_at >= datetime('now', '-1 day')",
            (chat_id,),
        ).fetchone()[0]
        if recent >= 5:
            return "Спасибо! На сегодня пожеланий достаточно — продолжим завтра."
        insights.record_wish(con, "command", text, chat_id=chat_id, who=self.who(con, chat_id))
        return "Спасибо! 💡 Передал пожелание. Такие идеи помогают сделать сервис лучше."

    # --- feedback ------------------------------------------------------------

    def feedback(self, con, chat_id, text, message):
        """Attach to the replied-to game, or to the latest one when the chat has one child.

        With several children in one chat "the latest game" is a guess between
        siblings (both arrive at 10:00), so the bot asks for a reply instead.
        """
        sent = (
            "SELECT g.id FROM deliveries d JOIN games g ON g.id = d.game_id "
            "WHERE d.channel = 'telegram' AND d.recipient = ? AND d.status = 'sent' "
        )
        replied = (message.get("reply_to_message") or {}).get("message_id")
        game = None
        if replied is not None:
            game = con.execute(sent + "AND d.external_message_id = ?", (chat_id, str(replied))).fetchone()
        if game is None:
            latest = con.execute(sent + "ORDER BY g.game_date DESC, g.id DESC LIMIT 1", (chat_id,)).fetchone()
            if latest is None:
                return "Первая игра ещё не пришла — отзыв можно будет написать после неё."
            if len(self.chat_children(con, chat_id)) > 1:
                return (
                    "У вас подписано несколько детей — чтобы отзыв попал к нужной игре, "
                    "ответьте на сообщение с игрой (свайп влево или «Ответить»)."
                )
            game = latest
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        recent = con.execute(
            "SELECT count(*) FROM feedback WHERE channel = 'telegram' AND external_event_id LIKE ? "
            "AND received_at >= ?",
            (f"tg:{chat_id}:%", since),
        ).fetchone()[0]
        if recent >= MAX_FEEDBACK_PER_DAY:
            return "Спасибо! На сегодня отзывов достаточно — продолжим завтра."
        # storage.save_raw opens its own connection: release our write lock first.
        con.commit()
        result = storage.save_raw({
            "event_id": f"tg:{chat_id}:{message.get('message_id')}",
            "channel": "telegram",
            "raw_text": text[:MAX_FEEDBACK_CHARS],
            "game_id": game["id"],
        })
        if result.get("status") == "existing":
            return None
        if reactions.is_reaction(text):
            # No model for a sticker or a photo; ask for words once per game.
            nudge = not reactions.already_nudged(con, game["id"])
            con.execute(
                "UPDATE feedback SET insights_at = ?, insights_status = 'reaction', "
                "reply_sent_at = CASE WHEN ? THEN CURRENT_TIMESTAMP END WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), int(nudge), result["feedback_id"]),
            )
            return reactions.NUDGE if nudge else None
        return "Спасибо, записал! 🙏 Через несколько минут напишу, что учтём."


def single_child_guard(con):
    """Refuse to start while the database is still in single-child mode with history.

    The first child added through the bot switches preparation to the children
    table; a child served only by CHILD_BIRTH_DATE / VK_PEER_ID would silently
    stop getting games. Register that child first (children.py add --adopt-existing
    plus its VK recipient).
    """
    if ch.has_children(con):
        return None
    if con.execute("SELECT 1 FROM games LIMIT 1").fetchone() is None:
        return None
    return (
        "The database has games but no children: register the existing child first "
        "(src/db/children.py add --adopt-existing, then add-recipient --channel vk), "
        "otherwise the first family that signs up switches it off."
    )


# --- Telegram transport ------------------------------------------------------

def set_menu(token, call=None):
    """Make the bot's command menu exactly COMMANDS (owner commands stay out of it)."""
    call = call or telegram
    commands = [{"command": name, "description": about} for name, about in COMMANDS]
    for scope in ("default", "all_private_chats"):
        call(token, "setMyCommands", {"commands": commands, "scope": {"type": scope}}, 20)


def telegram(token, method, payload, timeout):
    request = urllib.request.Request(
        f"{API}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def main():
    token = os.environ["GAME_TELEGRAM_BOT_TOKEN"]
    owner_id = os.environ.get("GAME_BOT_OWNER_ID", "")
    con = ch.connect_db(DB_PATH)
    try:
        ch.migrate(con)  # additive and idempotent
        legacy_problem = single_child_guard(con)
    finally:
        con.close()
    if legacy_problem:
        raise SystemExit(legacy_problem)
    username = telegram(token, "getMe", {}, 20)["result"].get("username", "")
    try:
        set_menu(token)
    except Exception as exc:  # noqa: BLE001 - a stale menu must not stop the bot
        print(f"menu not set: {type(exc).__name__}", flush=True)
    con = ch.connect_db(DB_PATH)
    try:
        channel_link.remember_bot_username(con, username)  # the VK side builds t.me links with it
        con.commit()
    finally:
        con.close()
    bot = ProductBot(DB_PATH, owner_id, username)
    offset = None
    last_success = time.time()

    def send(chat_id, text):
        try:
            telegram(token, "sendMessage", {"chat_id": chat_id, "text": text[:3500]}, 20)
        except Exception as exc:  # noqa: BLE001 - never log tokens or URLs
            print(f"send failed: {type(exc).__name__}", flush=True)

    from src.feedback import vk_inbound  # here: vk_inbound -> vk_dialog -> this module

    def send_queued(address, text, _random_id):
        telegram(token, "sendMessage", {"chat_id": address, "text": text[:3500]}, 20)

    while True:
        try:
            payload = {"timeout": POLL_TIMEOUT_SECONDS, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            result = telegram(token, "getUpdates", payload, POLL_TIMEOUT_SECONDS + 10)
            last_success = time.time()
            try:  # messages queued by other processes (e.g. a link approved in the ops bot)
                vk_inbound.drain_outbox(send_queued, DB_PATH, channel="telegram")
            except Exception as exc:  # noqa: BLE001 - the outbox must not stop the bot
                print(f"outbox failed: {type(exc).__name__}", flush=True)
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                try:
                    reply = bot.handle(update)
                except Exception as exc:  # noqa: BLE001 - one bad update must not stop the bot
                    print(f"update {update['update_id']} failed: {type(exc).__name__}: {exc}", flush=True)
                    reply = "Что-то пошло не так, попробуйте ещё раз чуть позже."
                chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
                if reply and chat_id is not None:
                    send(chat_id, reply)
        except Exception as exc:  # noqa: BLE001
            print(f"poll error: {type(exc).__name__}", flush=True)
            if time.time() - last_success > EXIT_AFTER_FAILING_SECONDS:
                print("no successful poll for too long; exiting", flush=True)
                sys.exit(1)
            time.sleep(5)


if __name__ == "__main__":
    main()
