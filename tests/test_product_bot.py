"""Product bot (self-service onboarding): invites, questionnaire, pause, feedback scoping.

Pure state-machine tests against a temporary database; no Telegram traffic.
"""

import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.bot import product_bot as pb  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.feedback import storage  # noqa: E402

OWNER = "1000"
NOON = datetime(2026, 11, 8, 12, 0, tzinfo=pb.MOSCOW)


class BotTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((PROJECT_ROOT / "src/db/schema.sql").read_text())
        con.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.codes = iter(f"code{i}" for i in range(100))
        self.bot = pb.ProductBot(self.db, OWNER, "gotd_bot", now=lambda: NOON, new_code=lambda: next(self.codes))
        self.message_id = 0

    def say(self, chat, text, chat_type="private"):
        self.message_id += 1
        return self.bot.handle({"message": {
            "message_id": self.message_id, "text": text,
            "chat": {"id": int(chat), "type": chat_type}, "from": {"id": int(chat), "first_name": f"U{chat}"},
        }})

    def rows(self, sql, params=()):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def onboard(self, chat, name="Маша", birth="15.03.2025"):
        link = self.say(OWNER, "/invite")
        code = link.split("start=")[1].split()[0]
        self.assertIn("Как зовут", self.say(chat, f"/start {code}"))
        self.assertIn("Дата рождения", self.say(chat, name))
        self.assertIn("Всё верно", self.say(chat, birth))
        return self.say(chat, "да")


class Invites(BotTest):
    def test_strangers_get_nothing_but_the_closed_notice(self):
        self.assertEqual(self.say("42", "привет"), pb.CLOSED)
        for text in ("/start", "привет", "/invite", "/members", "/block 1"):
            self.assertIsNone(self.say("42", text), text)  # once a day, then silence
        self.assertIn("недействительна", self.say("42", "/start nope"))
        self.assertEqual(self.say("43", "/faq"), pb.CLOSED)
        self.assertEqual(self.rows("SELECT count(*) FROM members"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM invites"), [(0,)])

    def test_an_invite_works_once_and_only_its_hash_is_stored(self):
        link = self.say(OWNER, "/invite")
        self.assertIn("https://t.me/gotd_bot?start=code0", link)
        self.assertEqual(self.rows("SELECT code_hash FROM invites"), [(pb.hash_code("code0"),)])
        self.assertIn("Как зовут", self.say("42", "/start code0"))
        self.assertIn("недействительна", self.say("43", "/start code0"))
        self.assertEqual(self.rows("SELECT kind, chat_id FROM user_events"), [("join", "42")])

    def test_an_expired_invite_is_rejected(self):
        self.say(OWNER, "/invite")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE invites SET expires_at = '2000-01-01 00:00:00'")
        con.commit()
        con.close()
        self.assertIn("недействительна", self.say("42", "/start code0"))

    def test_group_chats_are_ignored(self):
        self.assertIsNone(self.say("-100", "/start code0", chat_type="group"))


class Questionnaire(BotTest):
    def test_onboarding_creates_the_child_and_a_telegram_recipient(self):
        done = self.onboard("42")
        self.assertIn("придёт завтра в 10:00", done)
        self.assertEqual(self.rows("SELECT name, birth_date FROM children"), [("Маша", "2025-03-15")])
        self.assertEqual(self.rows("SELECT channel, address, active FROM recipients"), [("telegram", "42", 1)])
        self.assertEqual(self.rows("SELECT count(*) FROM onboarding"), [(0,)])
        self.assertIn("Маша — 19 месяцев", self.say("42", "/children"))
        con = ch.connect_db(self.db)
        self.assertEqual([c["name"] for c in ch.active_children(con)], ["Маша"])
        con.close()

    def test_the_confirmation_shows_the_computed_age(self):
        link = self.say(OWNER, "/invite")
        self.say("42", "/start " + link.split("start=")[1].split()[0])
        self.say("42", "Петя")
        self.assertIn("сейчас 1 месяц", self.say("42", "1.10.2026"))

    def test_bad_dates_are_explained_and_the_step_is_kept(self):
        link = self.say(OWNER, "/invite")
        self.say("42", "/start " + link.split("start=")[1].split()[0])
        self.say("42", "Петя")
        self.assertIn("разобрать", self.say("42", "весной"))
        self.assertIn("будущем", self.say("42", "01.01.2027"))
        self.assertIn("до 6 лет", self.say("42", "01.01.2015"))
        self.assertIn("31.12.2025", self.say("42", "2025-12-31"))

    def test_no_restarts_and_nothing_is_written_until_yes(self):
        link = self.say(OWNER, "/invite")
        self.say("42", "/start " + link.split("start=")[1].split()[0])
        self.say("42", "Петя")
        self.say("42", "01.01.2026")
        self.assertIn("да» или «нет", self.say("42", "может быть"))
        self.assertIn("заново", self.say("42", "нет"))
        self.assertEqual(self.rows("SELECT count(*) FROM children"), [(0,)])

    def test_the_questionnaire_survives_a_bot_restart(self):
        link = self.say(OWNER, "/invite")
        self.say("42", "/start " + link.split("start=")[1].split()[0])
        self.say("42", "Петя")
        self.bot = pb.ProductBot(self.db, OWNER, "gotd_bot", now=lambda: NOON)
        self.assertIn("Всё верно", self.say("42", "01.01.2026"))

    def test_a_second_child_and_the_owner_can_subscribe_too(self):
        self.onboard("42")
        self.say("42", "/add_child")
        self.say("42", "Петя")
        self.say("42", "01.01.2026")
        self.say("42", "да")
        self.assertIn("Как зовут", self.say(OWNER, "/start"))
        self.assertEqual(self.rows("SELECT count(*) FROM recipients WHERE address='42'"), [(2,)])
        self.assertIn("Маша", self.say(OWNER, "/members"))

    def test_early_morning_signup_gets_todays_game(self):
        self.bot.now = lambda: datetime(2026, 11, 8, 6, 0, tzinfo=pb.MOSCOW)
        self.assertIn("придёт сегодня", self.onboard("42"))


class MemberCommands(BotTest):
    def test_pause_and_resume_toggle_only_this_chat(self):
        self.onboard("42")
        self.onboard("43", name="Оля")
        self.assertIn("Пауза", self.say("42", "/pause"))
        self.assertIn("уже на паузе", self.say("42", "/pause"))
        self.assertEqual(self.rows("SELECT address, active FROM recipients ORDER BY address"), [("42", 0), ("43", 1)])
        self.assertIn("(на паузе)", self.say("42", "/children"))
        self.assertIn("возобновлены", self.say("42", "/resume"))


class Feedback(BotTest):
    def sent_game(self, chat, title, external_id=None, child=None):
        con = sqlite3.connect(self.db)
        game = con.execute(
            "INSERT INTO games(legacy_id, game_date, title, child_id) VALUES (?, '2026-11-08', ?, ?)",
            (title, title, child),
        ).lastrowid
        con.execute(
            "INSERT INTO deliveries(game_id, channel, recipient, idempotency_key, status, external_message_id) "
            "VALUES (?, 'telegram', ?, ?, 'sent', ?)", (game, chat, f"k-{title}", external_id)
        )
        con.commit()
        con.close()
        return game

    def test_feedback_attaches_to_the_game_sent_to_this_chat(self):
        self.onboard("42")
        self.onboard("43", name="Оля")
        self.assertIn("ещё не пришла", self.say("42", "понравилось"))
        mine = self.sent_game("42", "A")
        self.sent_game("43", "B")  # newer game for another family
        self.assertIn("Спасибо", self.say("42", "понравилось"))
        self.assertEqual(
            self.rows("SELECT game_id, channel, raw_text FROM feedback"), [(mine, "telegram", "понравилось")]
        )


    def reply_to(self, chat, text, replied_id):
        self.message_id += 1
        return self.bot.handle({"message": {
            "message_id": self.message_id, "text": text, "reply_to_message": {"message_id": replied_id},
            "chat": {"id": int(chat), "type": "private"}, "from": {"id": int(chat)},
        }})

    def test_with_two_children_in_a_chat_feedback_needs_a_reply_to_the_game(self):
        self.onboard("42")
        self.say("42", "/add_child")
        self.say("42", "Петя")
        self.say("42", "01.01.2026")
        self.say("42", "да")
        masha = self.sent_game("42", "A", external_id="501", child=1)
        self.sent_game("42", "B", external_id="502", child=2)
        self.assertIn("ответьте на сообщение", self.say("42", "понравилось"))
        self.assertEqual(self.rows("SELECT count(*) FROM feedback"), [(0,)])
        self.assertIn("Спасибо", self.reply_to("42", "Маше понравилось", 501))
        self.assertEqual(self.rows("SELECT game_id FROM feedback"), [(masha,)])

    def test_a_reply_cannot_reach_a_game_sent_to_another_chat(self):
        self.onboard("42")
        self.onboard("43", name="Оля")
        mine = self.sent_game("42", "A", external_id="501")
        self.sent_game("43", "B", external_id="777")
        self.reply_to("42", "ок", 777)  # 777 is a message id in someone else's chat
        self.assertEqual(self.rows("SELECT game_id FROM feedback"), [(mine,)])


class SameNames(BotTest):
    def test_the_same_name_twice_in_one_chat_is_questioned(self):
        self.onboard("42")
        self.say("42", "/add_child")
        self.assertIn("уже подписан", self.say("42", "маша"))
        self.assertIn("Дата рождения", self.say("42", "Маша младшая"))

    def test_the_same_name_in_different_families_is_fine(self):
        self.onboard("42")
        self.assertIn("Готово", self.onboard("43"))
        self.assertEqual(self.rows("SELECT count(*) FROM children WHERE name = 'Маша'"), [(2,)])


class Parsing(unittest.TestCase):
    def test_age_words(self):
        self.assertEqual([pb.months_word(n) for n in (1, 2, 5, 11, 12, 21, 22)],
                         ["месяц", "месяца", "месяцев", "месяцев", "месяцев", "месяц", "месяца"])

    def test_age_is_completed_months(self):
        self.assertEqual(pb.age_months(date(2025, 3, 15), date(2026, 11, 14)), 19)
        self.assertEqual(pb.age_months(date(2025, 3, 15), date(2026, 11, 15)), 20)


if __name__ == "__main__":
    unittest.main()


class StartGuard(BotTest):
    def test_the_bot_refuses_to_start_over_an_unregistered_single_child_history(self):
        con = ch.connect_db(self.db)
        self.assertIsNone(pb.single_child_guard(con))  # empty database: fine
        con.execute("INSERT INTO games(legacy_id, game_date, title) VALUES ('1', '2026-11-01', 'A')")
        self.assertIn("adopt-existing", pb.single_child_guard(con))
        ch.add_child(con, "Старший", "2025-09-01", adopt_existing=True)
        self.assertIsNone(pb.single_child_guard(con))
        con.close()


class Faq(BotTest):
    def test_the_welcome_points_to_the_faq_and_the_faq_resumes_the_questionnaire(self):
        link = self.say(OWNER, "/invite")
        welcome = self.say("42", "/start " + link.split("start=")[1].split()[0])
        self.assertIn("/faq", welcome)
        self.say("42", "Петя")
        faq = self.say("42", "/faq")
        self.assertIn("медицинский", faq)
        self.assertTrue(faq.endswith(pb.ASK_BIRTH))
        self.assertIn("Всё верно", self.say("42", "01.01.2026"))
        self.assertLess(len(pb.FAQ), 4000)  # one Telegram message

    def test_strangers_do_not_get_the_faq(self):
        self.assertEqual(self.say("42", "/faq"), pb.CLOSED)


class Abuse(BotTest):
    def add_child(self, chat, name, birth="01.01.2026"):
        self.say(chat, "/add_child")
        self.say(chat, name)
        self.say(chat, birth)
        return self.say(chat, "да")

    def test_children_per_chat_are_capped(self):
        self.onboard("42")
        self.add_child("42", "Петя")
        self.add_child("42", "Оля")
        self.assertIn("до 3 детей", self.say("42", "/add_child"))
        self.assertEqual(self.rows("SELECT count(*) FROM children"), [(3,)])

    def test_the_total_is_capped_and_the_owner_is_told(self):
        with mock.patch.object(pb, "MAX_ACTIVE_CHILDREN", 1):
            self.onboard("42")
            reply = self.onboard("43", name="Оля")
        self.assertIn("мест нет", reply)
        self.assertEqual(self.rows("SELECT count(*) FROM children"), [(1,)])
        self.assertEqual(self.rows("SELECT kind FROM user_events WHERE kind='limit'"), [("limit",)])

    def test_feedback_per_day_is_capped(self):
        self.onboard("42")
        Feedback.sent_game(self, "42", "A")
        with mock.patch.object(pb, "MAX_FEEDBACK_PER_DAY", 2):
            self.say("42", "раз")
            self.say("42", "два")
            self.assertIn("достаточно", self.say("42", "три"))
        self.assertEqual(self.rows("SELECT count(*) FROM feedback"), [(2,)])

    def test_block_stops_the_member_and_their_children_but_not_shared_ones(self):
        self.onboard("42")
        con = ch.connect_db(self.db)
        ch.add_recipient(con, 1, "vk", "999")  # Маша is also reached through VK
        con.close()
        self.add_child("42", "Петя")
        self.assertNotIn("заблокирован", self.say("42", "/block 42"))  # members cannot block
        self.assertIn("заблокирован", self.say(OWNER, "/block 42"))
        self.assertIsNone(self.say("42", "/children"))
        self.assertEqual(self.rows("SELECT name, active FROM children ORDER BY id"), [("Маша", 1), ("Петя", 0)])
        self.assertEqual(self.rows("SELECT count(*) FROM recipients WHERE channel='telegram' AND active=1"), [(0,)])
        self.assertNotIn("42", self.say(OWNER, "/members"))
        self.assertIn("Себя", self.say(OWNER, f"/block {OWNER}"))
