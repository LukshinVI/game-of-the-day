"""Theme of the month: something to play more of, never a test a child can fail."""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp()) / "import-guard.db"))

from src.bot import product_bot as pb  # noqa: E402
from src.bot import vk_dialog  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.feedback import month_theme as mt  # noqa: E402
from src.ops import weekly_digest as wd  # noqa: E402

SCHEMA = PROJECT_ROOT / "src" / "db" / "schema.sql"
TODAY = date(2026, 12, 2)


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GAME_MONTH_THEME": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA.read_text())
        con.close()
        con = ch.connect_db(self.db)
        ch.migrate(con)
        self.baby = ch.add_child(con, "Тёма", "2025-09-07")      # 14 months: no imagination yet
        ch.add_recipient(con, self.baby, "telegram", "555")
        ch.add_recipient(con, self.baby, "vk", "777")
        con.commit()
        self.con = con
        self.addCleanup(con.close)


class Choose(Base):
    def test_parse(self):
        self.assertEqual(mt.parse("Тема"), "")
        self.assertEqual(mt.parse("/theme"), "")
        self.assertEqual(mt.parse("тема речь"), "speech")
        self.assertEqual(mt.parse("тема моторика"), "fine_motor")
        self.assertEqual(mt.parse("тема движение"), "coordination")
        self.assertEqual(mt.parse("тема разное"), mt.MIX)
        self.assertEqual(mt.parse("тема чего-то непонятного"), "")
        self.assertIsNone(mt.parse("понравилось, тема с мячом зашла"))

    def test_choose_set_change_and_mix(self):
        self.assertIn("речь", mt.choose(self.con, "telegram", "555", "speech", TODAY))
        self.assertEqual(mt.current(self.con, self.baby, TODAY), "speech")
        mt.choose(self.con, "vk", "777", mt.MIX, TODAY)
        self.assertEqual(mt.current(self.con, self.baby, TODAY), mt.MIX)
        self.assertIsNone(mt.current(self.con, self.baby, date(2027, 1, 5)))  # a new month starts empty

    def test_a_skill_not_open_at_this_age_is_refused(self):
        self.assertNotIn("imagination", mt.options(self.con, "telegram", "555", TODAY))
        self.assertIn("пока нет", mt.choose(self.con, "telegram", "555", "imagination", TODAY))
        self.assertIsNone(mt.current(self.con, self.baby, TODAY))

    def test_the_question_says_it_is_not_a_test(self):
        text = mt.ask(self.con, "telegram", "555", TODAY)
        self.assertIn("не цель и не проверка", text)
        self.assertIn("декабре", text)
        markup = mt.telegram_markup(self.con, "555", TODAY)
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        self.assertIn("t:speech", data)
        self.assertIn("t:mix", data)


class Selection(Base):
    def test_every_other_day_at_most_and_only_with_a_theme(self):
        self.assertIsNone(mt.required(self.con, self.baby, [], TODAY, 14))
        mt.choose(self.con, "telegram", "555", "speech", TODAY)
        self.assertEqual(mt.required(self.con, self.baby, [{"skill": "sensory"}], TODAY, 14),
                         {"skill": "speech", "direction": "theme", "after": None})
        self.assertIsNone(mt.required(self.con, self.baby, [{"skill": "speech"}], TODAY, 14))
        mt.choose(self.con, "telegram", "555", mt.MIX, TODAY)
        self.assertIsNone(mt.required(self.con, self.baby, [{"skill": "sensory"}], TODAY, 14))

    def test_off_without_the_flag(self):
        mt.choose(self.con, "telegram", "555", "speech", TODAY)
        with mock.patch.dict(os.environ, {"GAME_MONTH_THEME": ""}):
            self.assertIsNone(mt.required(self.con, self.baby, [{"skill": "sensory"}], TODAY, 14))
            self.assertEqual(mt.invitation(self.con, self.baby, TODAY), "")


class Invitation(Base):
    def test_only_in_the_first_days_and_only_without_a_theme(self):
        self.assertIn("«тема»", mt.invitation(self.con, self.baby, TODAY))
        self.assertEqual(mt.invitation(self.con, self.baby, date(2026, 12, 10)), "")
        mt.choose(self.con, "telegram", "555", mt.MIX, TODAY)
        self.assertEqual(mt.invitation(self.con, self.baby, TODAY), "")


class Digest(Base):
    def game(self, gid, skill):
        self.con.execute("INSERT INTO games (id, game_date, title, skill, child_id, message_text) "
                         "VALUES (?, '2026-12-0' || ?, 'Игра', ?, ?, 'т')", (gid, gid, skill, self.baby))
        fid = self.con.execute("INSERT INTO feedback (game_id, channel, raw_text) VALUES (?, 'vk', 'x')",
                               (gid,)).lastrowid
        return fid

    def test_warm_moments_only_and_silence_otherwise(self):
        mt.choose(self.con, "telegram", "555", "speech", TODAY)
        self.game(1, "speech")
        self.assertIsNone(mt.digest_line(self.con, self.baby, [1], TODAY))  # no warm moment: silence
        fid = self.game(2, "speech")
        self.con.execute("INSERT INTO observations (feedback_id, child_id, game_id, kind, value, skill, evidence) "
                         "VALUES (?, ?, 2, 'skill', 'went_well', 'speech', 'повторял «ба» за мной')", (fid, self.baby))
        line = mt.digest_line(self.con, self.baby, [1, 2], TODAY)
        self.assertIn("речь: на этой неделе 2 игры", line)
        self.assertIn("повторял «ба» за мной", line)
        for word in ("цель", "выполн", "%", "норм", "не получ"):
            self.assertNotIn(word, line.casefold())


class Bots(Base):
    def test_telegram_word_and_button(self):
        self.con.execute("INSERT INTO members (chat_id) VALUES ('555')")
        self.con.commit()
        bot = pb.ProductBot(self.db, "1")
        with mock.patch.object(bot, "now", return_value=pb.datetime(2026, 12, 2, 12, tzinfo=pb.MOSCOW)):
            reply = bot.handle({"message": {"chat": {"id": 555, "type": "private"}, "from": {"id": 555},
                                            "message_id": 1, "text": "тема"}})
            self.assertIsInstance(reply, tuple)
            self.assertIn("Тема месяца", reply[0])
            answer, markup, note = bot.handle_callback({"callback_query": {
                "data": "t:speech", "message": {"chat": {"id": 555, "type": "private"}}}})
        self.assertEqual(answer, "Готово 🧭")
        self.assertIn("речь", note)
        self.assertIsNone(markup)
        self.assertEqual(mt.current(ch.connect_db(self.db), self.baby, TODAY), "speech")

    def test_vk_word(self):
        dialog = vk_dialog.VkDialog(now=lambda: pb.datetime(2026, 12, 2, 12, tzinfo=pb.MOSCOW))
        self.assertIn("Варианты:", dialog.handle(self.con, "777", "тема"))
        self.assertIn("движение", dialog.handle(self.con, "777", "тема движение"))
        self.assertEqual(mt.current(self.con, self.baby, TODAY), "coordination")


if __name__ == "__main__":
    unittest.main()
