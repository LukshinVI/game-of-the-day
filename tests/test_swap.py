"""«другую»: a library replacement for today's game, once per child per day."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp(prefix="swap-import-")) / "import.db"))

from src.bot import product_bot as pb  # noqa: E402
from src.bot import swap  # noqa: E402
from src.bot import vk_dialog as vd  # noqa: E402
from src.feedback import storage, vk_inbound  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=pb.MOSCOW)
VK = "777"
TG = "555"


class Swap(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'Сева', '2025-05-01')")  # 18 months
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', ?)", (VK,))
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'telegram', ?)", (TG,))
        con.execute("INSERT INTO members (chat_id) VALUES (?)", (TG,))
        today = self.game(con, 1, "2026-11-11", "Покажи в книжке", "speech", "показывает картинки в книжке")
        for channel, address in (("vk", VK), ("telegram", TG)):
            con.execute("INSERT INTO deliveries (game_id, channel, recipient, status, external_message_id) "
                        "VALUES (?, ?, ?, 'sent', 'm1')", (today, channel, address))
        # the library: another child's past game of a fitting age
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (2, 'Аня', '2025-01-01')")
        self.game(con, 2, "2026-10-01", "Прятки с платочком", "cognitive", "ищет игрушку под платком",
                  text="🎲 Игра дня: «Прятки с платочком»")
        con.commit()
        con.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dialog = vd.VkDialog(now=lambda: NOW)
        self.bot = pb.ProductBot(self.db, "1", "gotd_bot", now=lambda: NOW)
        self.sent, self.n = [], 0

    @staticmethod
    def game(con, child, day, title, skill, mechanics, text=None, age=17):
        return con.execute(
            "INSERT INTO games (game_date, title, child_id, age_months, skill, mechanics, source_url, message_text) "
            "VALUES (?, ?, ?, ?, ?, ?, 'https://example.org', ?)",
            (day, title, child, age, skill, mechanics, text or f"🎲 Игра дня: «{title}»")).lastrowid

    def vk(self, text):
        self.n += 1
        before = len(self.sent)
        result = vk_inbound.route(f"e{self.n}", VK, text, lambda p, t, r: self.sent.append(t), dialog=self.dialog)
        return result if result == "feedback" else (self.sent[-1] if len(self.sent) > before else None)

    def tg(self, text):
        self.n += 1
        return self.bot.handle({"message": {"message_id": self.n, "text": text, "chat": {"id": int(TG), "type": "private"},
                                            "from": {"id": int(TG), "first_name": "Мама"}}})

    def q(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def test_vk_swap_sends_a_library_game_and_keeps_the_original(self):
        reply = self.vk("Другую")
        self.assertIn("Вот другая игра на сегодня", reply)
        self.assertIn("Прятки с платочком", reply)
        self.assertEqual(self.q("SELECT title, origin FROM games WHERE child_id = 1 AND game_date = '2026-11-11'"),
                         [("Прятки с платочком", "swap")])
        [(original,)] = self.q("SELECT original_json FROM swaps")
        self.assertIn("Покажи в книжке", original)
        [(text,)] = self.q("SELECT text FROM user_events WHERE kind = 'swap'")
        self.assertIn("заменили «Покажи в книжке» → «Прятки с платочком»", text)

    def test_one_swap_per_child_per_day_across_channels(self):
        self.vk("другую")
        self.assertIn("уже заменяли", self.tg("/another"))

    def test_telegram_command_and_plain_word(self):
        self.assertIn("Прятки с платочком", self.tg("другую игру"))

    def test_feedback_starting_with_drugoy_stays_feedback(self):
        self.assertEqual(self.vk("Другой раз попробуем ещё"), "feedback")
        self.assertEqual(self.q("SELECT count(*) FROM swaps"), [(0,)])

    def test_no_replacement_tells_the_family_and_the_owner(self):
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM games WHERE child_id = 2")
        con.commit()
        con.close()
        self.assertIn("подходящей замены нет", self.vk("другую"))
        self.assertIn("замены в библиотеке нет", self.q("SELECT text FROM user_events WHERE kind = 'swap'")[0][0])

    def test_nothing_to_swap_before_the_game_arrives(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE deliveries SET status = 'pending'")
        con.commit()
        con.close()
        self.assertIn("ещё не пришла", self.vk("другую"))

    def test_several_children_ask_for_whom(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (2, 'vk', ?)", (VK,))
        con.commit()
        con.close()
        self.assertIn("Для кого", self.vk("другую"))
        self.assertIn("Вот другая игра", self.vk("другую сева"))

    def test_parse(self):
        self.assertEqual(swap.parse("Другую, пожалуйста!"), (True, None))
        self.assertEqual(swap.parse("/another"), (True, None))
        self.assertEqual(swap.parse("другую для Маши"), (True, "маши"))
        self.assertEqual(swap.parse("спасибо"), (False, None))


if __name__ == "__main__":
    unittest.main()
