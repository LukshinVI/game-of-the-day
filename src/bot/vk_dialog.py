"""VK community chat: self-service onboarding with owner approval, and simple commands.

Every VK message reaches the host through the feedback queue with the sender's peer id
(worker/src/index.js). The feedback consumer asks this module what it is:

- a family already subscribed in VK (a VK recipient): a command ("пауза", "продолжить",
  "мои дети", "добавить ребёнка", "помощь"), an answer in a questionnaire for one more
  child, or otherwise feedback on its games (the normal feedback pipeline);
- anyone else: the questionnaire (name, birth date, confirmation, the same rules as the
  Telegram bot), which ends in an application. The owner approves or rejects it with a
  button in the ops bot (src/ops/manual_bot.py); only then a child and a VK recipient are
  created, so a stranger can never start model spending on their own.

Members and questionnaire state live in the product bot's tables (members, onboarding)
under the key "vk:<peer id>", so a VK peer id never collides with a Telegram chat id.
Deterministic code only: no model call here.
"""

import os
from datetime import date, datetime

from src.bot import channel_link
from src.bot import product_bot as pb

CHANNEL = "vk"
# Strangers who may start a questionnaire per day: bounds a flood of fake applications
# (each one is a message to the owner).
MAX_NEW_PER_DAY = int(os.environ.get("GAME_VK_MAX_NEW_PER_DAY", "20"))

FEEDBACK = object()  # handle() result: not a dialog message, store it as feedback

WELCOME = (
    "Здравствуйте! 👋 Это «Игра дня»: каждое утро одна простая развивающая игра "
    "для малыша от рождения до 3 лет — по возрасту и из того, что есть дома. Бесплатно.\n\n"
    "Подключение займёт минуту: имя малыша и дата рождения."
)
SUBMITTED = (
    "Спасибо! 🙏 Заявка отправлена. Мы подключим вас в ближайшее время и напишем здесь, "
    "с какого дня начнут приходить игры."
)
STILL_PENDING = "Заявка уже у нас 🙂 Как только подключим — напишем здесь."
TELEGRAM_ASKED = (
    "Хорошо! Пришлём сюда ссылку на нашего Telegram-бота — откройте её и нажмите «Старт», "
    "бот спросит имя и дату рождения малыша. Если передумаете и захотите получать игры здесь, "
    "напишите «ВКонтакте»."
)
BUSY = "Сейчас очень много заявок — напишите нам, пожалуйста, завтра 🙏"
HELP = (
    "Каждое утро около {time} по Москве здесь будет игра дня.\n"
    "Ответьте на сообщение с игрой — как прошло, было ли легко или сложно. "
    "Следующие игры подстроятся под малыша.\n\n"
    "Что можно написать:\n"
    "«пауза» — приостановить игры\n"
    "«продолжить» — возобновить\n"
    "«мои дети» — кто подписан\n"
    "«добавить ребёнка» — подписать ещё одного малыша\n"
    "«подключить телеграм» — получать игры ещё и в Telegram\n"
    "«отмена» — прервать анкету"
).format(time=pb.DELIVERY_TIME_MSK)

COMMANDS = {
    "пауза": "pause", "/pause": "pause", "стоп": "pause",
    "продолжить": "resume", "/resume": "resume", "возобновить": "resume",
    "мои дети": "children", "/children": "children", "дети": "children",
    "добавить ребёнка": "add_child", "добавить ребенка": "add_child", "/add_child": "add_child",
    "помощь": "help", "/help": "help", "команды": "help",
    "отмена": "cancel", "/cancel": "cancel",
    "подключить телеграм": "link_telegram", "подключить телеграмм": "link_telegram",
    "подключить telegram": "link_telegram", "подключить тг": "link_telegram",
}
TELEGRAM_WORDS = ("телеграм", "телеграмм", "telegram", "тг")
VK_WORDS = ("вконтакте", "вк", "vk")


def normalize(text):
    return " ".join(text.casefold().replace("ё", "е").strip(" .!?,").split())


def member_key(peer):
    return f"{CHANNEL}:{peer}"


class VkDialog:
    def __init__(self, now=None):
        self.now = now or (lambda: datetime.now(pb.MOSCOW))

    # --- lookups ---------------------------------------------------------------

    @staticmethod
    def children(con, peer):
        return con.execute(
            "SELECT c.id, c.name, c.birth_date, r.active FROM recipients r "
            "JOIN children c ON c.id = r.child_id "
            "WHERE r.channel = 'vk' AND r.address = ? AND c.active = 1 ORDER BY c.id",
            (peer,),
        ).fetchall()

    @staticmethod
    def is_recipient(con, peer):
        return con.execute(
            "SELECT 1 FROM recipients WHERE channel = 'vk' AND address = ?", (peer,)
        ).fetchone() is not None

    @staticmethod
    def pending(con, peer):
        return con.execute(
            "SELECT * FROM applications WHERE channel = 'vk' AND address = ? AND status = 'pending'",
            (peer,),
        ).fetchone()

    # --- entry point -----------------------------------------------------------

    def handle(self, con, peer, text):
        """Return the reply text, FEEDBACK, or None (say nothing). Runs in the caller's
        transaction; the caller commits."""
        peer = str(peer)
        key = member_key(peer)
        member = con.execute("SELECT * FROM members WHERE chat_id = ?", (key,)).fetchone()
        if member is not None and member["blocked_at"]:
            return None
        command = COMMANDS.get(normalize(text))
        code = channel_link.vk_code(text)
        if code:  # a family subscribed in Telegram adds this VK chat
            return channel_link.redeem(con, code, "vk", peer)
        if self.is_recipient(con, peer):
            return self.family(con, peer, key, text, command)
        return self.stranger(con, peer, key, member, text, command)

    # --- a subscribed family ---------------------------------------------------

    def family(self, con, peer, key, text, command):
        current = con.execute("SELECT * FROM onboarding WHERE chat_id = ?", (key,)).fetchone()
        if command == "help":
            return HELP
        if command == "children":
            return self.list_children(con, peer)
        if command in ("pause", "resume"):
            return self.set_active(con, peer, key, command == "resume")
        if command == "cancel":
            con.execute("DELETE FROM onboarding WHERE chat_id = ?", (key,))
            return "Отменено." if current else HELP
        if command == "link_telegram":
            return channel_link.issue(con, "vk", peer, "telegram")
        if command == "add_child":
            limit = self.child_limit(con, peer, key)
            if limit:
                return limit
            self.ensure_member(con, key)
            pb.ProductBot.set_step(con, key, "name")
            return pb.ASK_NAME
        if current is not None:
            if pb.reactions.is_reaction(text):
                return "Ответьте, пожалуйста, текстом 🙂"
            return self.questionnaire(con, peer, key, current, text, family=True)
        if con.execute("SELECT 1 FROM deliveries WHERE channel = 'vk' AND recipient = ? AND status = 'sent'",
                       (peer,)).fetchone() is None:
            return "Первая игра ещё не пришла — отзыв можно будет написать после неё 🙂"
        return FEEDBACK

    def list_children(self, con, peer):
        today = self.now().date()
        rows = self.children(con, peer)
        if not rows:
            return "Пока никто не подписан. Напишите «добавить ребёнка»."
        return "\n".join(
            f"{r['name']} — {pb.describe_age(r['birth_date'], today)}" + ("" if r["active"] else " (на паузе)")
            for r in rows
        )

    def set_active(self, con, peer, key, active):
        changed = con.execute(
            "UPDATE recipients SET active = ? WHERE channel = 'vk' AND address = ? AND active = ?",
            (int(active), peer, int(not active)),
        ).rowcount
        if not changed:
            return "Игры уже идут." if active else "Игры уже на паузе."
        names = ", ".join(r["name"] for r in self.children(con, peer)) or "—"
        pb.ProductBot.notify_owner(
            f"{'▶️' if active else '⏸'} ВК {peer} ({names}): {'возобновил(а)' if active else 'поставил(а) на паузу'} игры",
            con, key, "resume" if active else "pause",
        )
        if active:
            return "Игры возобновлены с ближайшего утра 🧸"
        return ("Поставили на паузу. Уже подготовленная на сегодня игра может прийти, дальше — тишина. "
                "Напишите «продолжить», когда захотите вернуться.")

    def child_limit(self, con, peer, key):
        if len(self.children(con, peer)) >= pb.MAX_CHILDREN_PER_CHAT:
            return f"В одном чате можно подписать до {pb.MAX_CHILDREN_PER_CHAT} детей."
        total = con.execute("SELECT count(*) FROM children WHERE active = 1").fetchone()[0]
        if total >= pb.MAX_ACTIVE_CHILDREN:
            pb.ProductBot.notify_owner(
                f"⚠️ Достигнут лимит детей ({pb.MAX_ACTIVE_CHILDREN}): ВК {peer} не смог подписаться. "
                "Поднять: GAME_MAX_ACTIVE_CHILDREN.",
                con, key, "limit",
            )
            return "Сейчас мест нет — мы уже знаем и напишем, как только появятся 🙏"
        return None

    # --- a stranger ------------------------------------------------------------

    def stranger(self, con, peer, key, member, text, command):
        if self.pending(con, peer) is not None:
            return STILL_PENDING
        words = normalize(text)
        if member is None:
            new_today = con.execute(
                "SELECT count(*) FROM members WHERE chat_id LIKE 'vk:%' AND joined_at >= datetime('now', '-1 day')"
            ).fetchone()[0]
            if new_today >= MAX_NEW_PER_DAY:
                return BUSY
            self.ensure_member(con, key)
            pb.ProductBot.set_step(con, key, "name")
            if any(w in words.split() for w in TELEGRAM_WORDS):
                return self.wants_telegram(con, peer, key)
            pb.ProductBot.notify_owner(f"👋 ВК: новый собеседник vk.com/id{peer} начал анкету", con, key, "join")
            return WELCOME + "\n\n" + pb.ASK_NAME

        current = con.execute("SELECT * FROM onboarding WHERE chat_id = ?", (key,)).fetchone()
        if command == "cancel":
            con.execute("DELETE FROM onboarding WHERE chat_id = ?", (key,))
            return "Отменено. Захотите подключиться — просто напишите сюда."
        if command == "help":
            return WELCOME + "\n\nНапишите что угодно, чтобы начать."
        if words in TELEGRAM_WORDS:
            return self.wants_telegram(con, peer, key)
        if words in VK_WORDS:
            pb.ProductBot.set_step(con, key, "name")
            return "Хорошо, игры будут приходить сюда. " + pb.ASK_NAME
        if current is None:
            pb.ProductBot.set_step(con, key, "name")
            return WELCOME + "\n\n" + pb.ASK_NAME
        if pb.reactions.is_reaction(text):
            return "Ответьте, пожалуйста, текстом 🙂"
        return self.questionnaire(con, peer, key, current, text, family=False)

    def wants_telegram(self, con, peer, key):
        con.execute("DELETE FROM onboarding WHERE chat_id = ?", (key,))
        pb.ProductBot.notify_owner(
            f"📨 ВК vk.com/id{peer} хочет получать игры в Telegram: пришлите ссылку из /invite "
            "продуктового бота ей в сообщения сообщества.",
            con, key, "telegram_request",
        )
        return TELEGRAM_ASKED

    @staticmethod
    def ensure_member(con, key):
        con.execute("INSERT OR IGNORE INTO members(chat_id) VALUES (?)", (key,))

    # --- questionnaire ---------------------------------------------------------

    def questionnaire(self, con, peer, key, current, text, family):
        today = self.now().date()
        if current["step"] == "name":
            name = " ".join(text.split())
            if not name or len(name) > pb.MAX_NAME_CHARS:
                return f"Имя — до {pb.MAX_NAME_CHARS} символов. " + pb.ASK_NAME
            if any(r["name"].casefold() == name.casefold() for r in self.children(con, peer)):
                return (f"{name} у вас уже подписан(а). Если это другой ребёнок, напишите имя иначе "
                        "(например, «Маша младшая»). «отмена» — прервать.")
            pb.ProductBot.set_step(con, key, "birth_date", name=name)
            return pb.ASK_BIRTH

        if current["step"] == "birth_date":
            try:
                birth = pb.parse_birth_date(text, today)
            except ValueError as exc:
                return str(exc)
            pb.ProductBot.set_step(con, key, "confirm", name=current["name"], birth_date=birth)
            shown = date.fromisoformat(birth).strftime("%d.%m.%Y")
            return (f"{current['name']}, {shown} — сейчас {pb.describe_age(birth, today)}. Всё верно?\n"
                    "Ответьте «да» или «нет».")

        answer = normalize(text)
        if answer in pb.NO:
            pb.ProductBot.set_step(con, key, "name")
            return "Давайте заново. " + pb.ASK_NAME
        if answer not in pb.YES:
            return "Ответьте «да» или «нет»."
        con.execute("DELETE FROM onboarding WHERE chat_id = ?", (key,))
        if family:
            limit = self.child_limit(con, peer, key)
            if limit:
                return limit
            create_child(con, peer, current["name"], current["birth_date"])
            pb.ProductBot.notify_owner(
                f"👶 ВК {peer}: подписан ещё один ребёнок — {current['name']}, "
                f"{pb.describe_age(current['birth_date'], today)}",
                con, key, "child",
            )
            return f"Готово! Первая игра для {current['name']} придёт {self.first_day()} в {pb.DELIVERY_TIME_MSK} по Москве 🧸"
        app_id = con.execute(
            "INSERT INTO applications(channel, address, name, birth_date) VALUES ('vk', ?, ?, ?)",
            (peer, current["name"], current["birth_date"]),
        ).lastrowid
        pb.ProductBot.notify_owner(
            f"📝 Заявка #{app_id} из ВК: {current['name']}, {pb.describe_age(current['birth_date'], today)} "
            f"(vk.com/id{peer})",
            con, key, f"application:{app_id}",
        )
        return SUBMITTED

    def first_day(self):
        return "сегодня" if self.now().hour < pb.PREPARATION_HOUR_MSK else "завтра"


def create_child(con, peer, name, birth_date):
    child_id = con.execute(
        "INSERT INTO children(name, birth_date) VALUES (?, ?)", (name, birth_date)
    ).lastrowid
    con.execute("INSERT INTO recipients(child_id, channel, address) VALUES (?, 'vk', ?)", (child_id, peer))
    return child_id


def decide(con, app_id, approve, now=None):
    """The owner's decision on an application (ops bot button). Runs in the caller's
    transaction. Returns the text for the owner; queues the message to the family."""
    now = now or datetime.now(pb.MOSCOW)
    app = con.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if app is None:
        return "Заявка не найдена."
    if app["status"] != "pending":
        return f"Заявка #{app_id} уже {'одобрена' if app['status'] == 'approved' else 'отклонена'}."
    who = f"{app['name']}, {pb.describe_age(app['birth_date'], now.date())}"
    if not approve:
        con.execute("UPDATE applications SET status = 'rejected', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (app_id,))
        queue_message(con, app["channel"], app["address"],
                      "К сожалению, сейчас мы не можем вас подключить. Спасибо за интерес к «Игре дня» 💛")
        return f"❌ Заявка #{app_id} ({who}) отклонена."
    total = con.execute("SELECT count(*) FROM children WHERE active = 1").fetchone()[0]
    if total >= pb.MAX_ACTIVE_CHILDREN:
        return (f"Лимит детей ({pb.MAX_ACTIVE_CHILDREN}) достигнут — заявка #{app_id} остаётся в ожидании. "
                "Поднимите GAME_MAX_ACTIVE_CHILDREN и нажмите снова.")
    child_id = create_child(con, app["address"], app["name"], app["birth_date"])
    con.execute("UPDATE applications SET status = 'approved', decided_at = CURRENT_TIMESTAMP, child_id = ? "
                "WHERE id = ?", (child_id, app_id))
    when = "сегодня" if now.hour < pb.PREPARATION_HOUR_MSK else "завтра"
    queue_message(con, app["channel"], app["address"],
                  f"Готово, вы подключены! 🎉 Первая игра для {app['name']} придёт {when} "
                  f"около {pb.DELIVERY_TIME_MSK} по Москве.\n\n" + HELP)
    return f"✅ Заявка #{app_id} одобрена: {who}, первая игра {when}."


def queue_message(con, channel, address, text):
    con.execute("INSERT INTO outbox(channel, address, text) VALUES (?, ?, ?)", (channel, address, text))
