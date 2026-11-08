"""VK self-service onboarding: questionnaire, owner approval, commands, routing, outbox."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bot import product_bot as pb  # noqa: E402
from src.bot import vk_dialog as vd  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.feedback import storage, vk_inbound  # noqa: E402
from src.ops import manual_bot, users_report  # noqa: E402

MORNING = datetime(2026, 11, 9, 6, 0, tzinfo=pb.MOSCOW)
EVENING = datetime(2026, 11, 9, 20, 0, tzinfo=pb.MOSCOW)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []
        self.now = EVENING
        self.dialog = vd.VkDialog(now=lambda: self.now)
        self.n = 0

    def say(self, peer, text):
        """One VK message through the consumer's routing; returns the reply or 'feedback'."""
        self.n += 1
        before = len(self.sent)
        result = vk_inbound.route(f"e{self.n}", str(peer), text,
                                  lambda p, t, r: self.sent.append((p, t, r)), dialog=self.dialog)
        if result == "feedback":
            return "feedback"
        return self.sent[-1][1] if len(self.sent) > before else None

    def q(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def apply(self, peer=777, name="Маша", birth="15.03.2025"):
        self.say(peer, "Здравствуйте")
        self.say(peer, name)
        self.say(peer, birth)
        return self.say(peer, "да")

    def decide(self, app_id, ok=True):
        return manual_bot.decide_application(app_id, ok, db_path=self.db)

    def family(self, peer=777):
        self.apply(peer)
        self.decide(self.q("SELECT max(id) FROM applications")[0][0])


class Onboarding(Base):
    def test_a_stranger_fills_the_questionnaire_and_waits_for_approval(self):
        self.assertIn("Как зовут", self.say(777, "Здравствуйте, хочу игры"))
        self.assertIn("Дата рождения", self.say(777, "Маша"))
        self.assertIn("Всё верно", self.say(777, "15.03.2025"))
        self.assertIn("Заявка отправлена", self.say(777, "да"))
        self.assertEqual(self.q("SELECT count(*) FROM children"), [(0,)])  # nothing until approved
        self.assertEqual(self.q("SELECT name, birth_date, status FROM applications"),
                         [("Маша", "2025-03-15", "pending")])
        self.assertIn("уже у нас", self.say(777, "ну что?"))
        kinds = [k for (k,) in self.q("SELECT kind FROM user_events ORDER BY id")]
        self.assertEqual(kinds, ["join", "application:1"])

    def test_approval_creates_the_child_and_tells_the_family(self):
        self.apply()
        text = self.decide(1)
        self.assertIn("одобрена", text)
        self.assertEqual(self.q("SELECT c.name, r.channel, r.address FROM children c JOIN recipients r "
                                "ON r.child_id = c.id"), [("Маша", "vk", "777")])
        self.assertIn("уже одобрена", self.decide(1))  # a second tap changes nothing
        self.assertEqual(self.q("SELECT count(*) FROM children"), [(1,)])
        sent = []
        self.assertEqual(vk_inbound.drain_outbox(lambda p, t, r: sent.append((p, t)), db_path=self.db), 1)
        self.assertEqual(sent[0][0], "777")
        self.assertIn("вы подключены", sent[0][1])
        self.assertEqual(vk_inbound.drain_outbox(lambda *a: sent.append(a), db_path=self.db), 0)

    def test_rejection_creates_nothing(self):
        self.apply()
        self.assertIn("отклонена", self.decide(1, ok=False))
        self.assertEqual(self.q("SELECT count(*) FROM children"), [(0,)])
        self.assertEqual(self.q("SELECT status FROM applications"), [("rejected",)])

    def test_approval_respects_the_total_limit(self):
        self.apply()
        with mock.patch.object(pb, "MAX_ACTIVE_CHILDREN", 0):
            self.assertIn("Лимит", self.decide(1))
        self.assertEqual(self.q("SELECT status FROM applications"), [("pending",)])

    def test_wrong_birth_dates_are_explained(self):
        self.say(777, "привет")
        self.say(777, "Маша")
        self.assertIn("разобрать", self.say(777, "весной"))
        self.assertIn("до 3 лет", self.say(777, "01.01.2020"))
        self.assertIn("будущем", self.say(777, "01.01.2030"))

    def test_no_starts_again_and_cancel_stops(self):
        self.say(777, "привет")
        self.say(777, "Маша")
        self.say(777, "15.03.2025")
        self.assertIn("заново", self.say(777, "нет"))
        self.assertIn("Отменено", self.say(777, "отмена"))
        self.assertEqual(self.q("SELECT count(*) FROM onboarding"), [(0,)])

    def test_telegram_request_goes_to_the_owner(self):
        self.assertIn("Telegram", self.say(777, "Хочу получать в телеграм"))
        self.assertEqual(self.q("SELECT kind FROM user_events"), [("telegram_request",)])
        self.assertEqual(self.q("SELECT count(*) FROM onboarding"), [(0,)])
        self.assertIn("Как зовут", self.say(777, "ВКонтакте"))

    def test_a_flood_of_strangers_is_bounded(self):
        with mock.patch.object(vd, "MAX_NEW_PER_DAY", 2):
            self.say(1001, "a")
            self.say(1002, "b")
            self.assertIn("много заявок", self.say(1003, "c"))

    def test_a_blocked_member_gets_nothing(self):
        self.say(777, "привет")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE members SET blocked_at = CURRENT_TIMESTAMP WHERE chat_id = 'vk:777'")
        con.commit()
        con.close()
        self.assertIsNone(self.say(777, "Маша"))

    def test_a_queue_retry_repeats_the_same_reply(self):
        calls = []

        def flaky(peer, text, rid):
            calls.append((text, rid))
            if len(calls) == 1:
                raise RuntimeError("VK down")

        with self.assertRaises(RuntimeError):
            vk_inbound.route("x1", "777", "привет", flaky, dialog=self.dialog)
        vk_inbound.route("x1", "777", "привет", flaky, dialog=self.dialog)
        self.assertEqual(calls[0], calls[1])                      # same text, same random_id
        self.assertEqual(self.q("SELECT count(*) FROM members"), [(1,)])
        self.assertEqual(self.q("SELECT step FROM onboarding"), [("name",)])  # not advanced twice


class Family(Base):
    def test_family_text_is_feedback_after_the_first_game(self):
        self.family()
        self.assertIn("ещё не пришла", self.say(777, "спасибо!"))
        con = ch.connect_db(self.db)
        gid = con.execute("INSERT INTO games(legacy_id, game_date, title, child_id) VALUES ('g', '2026-11-10', "
                          "'Ку-ку', 1)").lastrowid
        con.execute("INSERT INTO deliveries(game_id, channel, recipient, status, idempotency_key) "
                    "VALUES (?, 'vk', '777', 'sent', 'k')", (gid,))
        con.commit()
        con.close()
        self.assertEqual(self.say(777, "понравилось"), "feedback")

    def test_commands(self):
        self.family()
        self.assertIn("пауз", self.say(777, "Пауза"))
        self.assertEqual(self.q("SELECT active FROM recipients"), [(0,)])
        self.assertIn("на паузе", self.say(777, "мои дети"))
        self.assertIn("уже на паузе", self.say(777, "пауза"))
        self.assertIn("возобновлены", self.say(777, "продолжить!"))
        self.assertEqual(self.q("SELECT active FROM recipients"), [(1,)])
        self.assertIn("«пауза»", self.say(777, "помощь"))

    def test_a_family_adds_a_child_without_approval(self):
        self.family()
        self.now = MORNING
        self.assertIn("Как зовут", self.say(777, "добавить ребёнка"))
        self.assertIn("уже подписан", self.say(777, "маша"))
        self.say(777, "Петя")
        self.say(777, "01.06.2026")
        self.assertIn("сегодня", self.say(777, "да"))
        self.assertEqual(self.q("SELECT name FROM children ORDER BY id"), [("Маша",), ("Петя",)])


class FeedbackTarget(Base):
    """With the sender address, two VK families never answer each other's games."""

    def test_feedback_lands_on_the_senders_game(self):
        con = ch.connect_db(self.db)
        for peer, title in (("777", "Ку-ку"), ("888", "Мяч")):
            child = ch.add_child(con, title, "2025-09-03")
            ch.add_recipient(con, child, "vk", peer)
            gid = con.execute("INSERT INTO games(legacy_id, game_date, title, child_id) VALUES (?, '2026-11-10', ?, ?)",
                              (title, title, child)).lastrowid
            con.execute("INSERT INTO deliveries(game_id, channel, recipient, status, idempotency_key) "
                        "VALUES (?, 'vk', ?, 'sent', ?)", (gid, peer, peer))
        con.commit()
        con.close()
        a = storage.save_raw({"event_id": "1", "channel": "vk", "raw_text": "ok", "address": "777"})
        b = storage.save_raw({"event_id": "2", "channel": "vk", "raw_text": "ok", "address": "888"})
        self.assertEqual((a["game"]["title"], b["game"]["title"]), ("Ку-ку", "Мяч"))
        with self.assertRaises(storage.StorageError):
            storage.save_raw({"event_id": "3", "channel": "vk", "raw_text": "ok", "address": "x1"})


class OpsBot(Base):
    def test_applications_come_with_buttons_and_are_decided_by_the_owner_only(self):
        self.apply()
        plain, asked = [], []
        manual_bot.forward_user_events(plain.append, lambda: users_report.pending_events(self.db),
                                       lambda ids: users_report.mark_notified(ids, self.db),
                                       ask=lambda text, markup: asked.append((text, markup)))
        self.assertIn("начал анкету", plain[0])
        self.assertIn("Заявка #1", asked[0][0])
        self.assertEqual(asked[0][1]["inline_keyboard"][0][0]["callback_data"], "app:1:ok")

        decided = []
        bot = manual_bot.Controller(5, decide=lambda app, ok: decided.append((app, ok)) or "done")
        self.assertIsNone(bot.handle({"callback_query": {"from": {"id": 6}, "data": "app:1:ok"}}))
        self.assertIsNone(bot.handle({"callback_query": {"from": {"id": 5}, "data": "app:x:ok"}}))
        self.assertEqual(bot.handle({"callback_query": {"from": {"id": 5}, "data": "app:1:no"}}), "done")
        self.assertEqual(decided, [(1, False)])


if __name__ == "__main__":
    unittest.main()
