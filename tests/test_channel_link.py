"""An existing family adds its second messenger: code, redemption, owner decision."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# storage binds its DB path at import: point it at a temp file before anything imports it
# (test_feedback_storage refuses to run if an earlier module bound the real one).
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp(prefix="link-import-")) / "import.db"))

from src.bot import channel_link as cl  # noqa: E402
from src.bot import product_bot as pb  # noqa: E402
from src.bot import vk_dialog as vd  # noqa: E402
from src.feedback import storage, vk_inbound  # noqa: E402
from src.ops import manual_bot  # noqa: E402

OWNER = "1"
VK = "777"
TG = "555"


class Link(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'Сева', '2025-05-01')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', ?)", (VK,))
        con.execute("INSERT INTO games (id, game_date, title, child_id) VALUES (1, '2026-11-11', 'И', 1)")
        con.execute("INSERT INTO deliveries (game_id, channel, recipient, status) VALUES (1, 'vk', ?, 'sent')", (VK,))
        con.commit()
        con.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dialog = vd.VkDialog(now=lambda: datetime(2026, 11, 11, 20, 0, tzinfo=pb.MOSCOW))
        self.bot = pb.ProductBot(self.db, OWNER, "gotd_bot")
        self.vk_sent, self.n = [], 0

    def vk(self, peer, text):
        self.n += 1
        before = len(self.vk_sent)
        result = vk_inbound.route(f"e{self.n}", peer, text, lambda p, t, r: self.vk_sent.append((p, t)),
                                  dialog=self.dialog)
        return result if result == "feedback" else (self.vk_sent[-1][1] if len(self.vk_sent) > before else None)

    def tg(self, chat, text):
        self.n += 1
        return self.bot.handle({"message": {"message_id": self.n, "text": text, "chat": {"id": int(chat), "type": "private"},
                                            "from": {"id": int(chat), "first_name": "Мама"}}})

    def q(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def outbox(self, channel):
        sent = []
        vk_inbound.drain_outbox(lambda a, t, r: sent.append((a, t)), self.db, channel=channel)
        return sent

    def events(self):
        return self.q("SELECT kind, text FROM user_events WHERE kind LIKE 'link:%'")

    def vk_link(self):
        con = sqlite3.connect(self.db)
        cl.remember_bot_username(con, "gotd_bot")
        con.commit()
        con.close()
        reply = self.vk(VK, "Подключить Телеграм")
        self.assertIn("https://t.me/gotd_bot?start=link_", reply)
        return reply.split("start=")[1].split()[0]

    def test_vk_family_adds_telegram_after_the_owner_says_yes(self):
        start = self.vk_link()
        self.assertIn("Заявка на подключение отправлена", self.tg(TG, f"/start {start}"))
        self.assertEqual(self.q("SELECT count(*) FROM recipients WHERE channel = 'telegram'"), [(0,)])
        [(kind, text)] = self.events()
        self.assertIn("существующая семья добавляет второй канал, новых детей не создаётся", text)
        self.assertIn("vk.com/id777 (дети: Сева)", text)
        self.assertIn(f"Telegram chat {TG}", text)
        link_id = int(kind.split(":")[1])

        self.assertIn("одобрена", manual_bot.decide_channel_link(link_id, True, db_path=self.db))
        self.assertEqual(self.q("SELECT child_id, channel, address FROM recipients ORDER BY id"),
                         [(1, "vk", VK), (1, "telegram", TG)])
        self.assertEqual(self.q("SELECT count(*) FROM children"), [(1,)])
        [(addr, text)] = self.outbox("telegram")
        self.assertEqual(addr, TG)
        self.assertIn("Игры для Сева теперь будут приходить и сюда", text)
        [(addr, _)] = self.outbox("vk")
        self.assertEqual(addr, VK)
        self.assertIn("Сева", self.tg(TG, "/children"))  # the chat is a family now, not a stranger
        self.assertIn("уже", manual_bot.decide_channel_link(link_id, True, db_path=self.db))

    def test_telegram_family_adds_vk_by_typing_the_code(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (2, 'Аня', '2025-01-01')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (2, 'telegram', ?)", (TG,))
        con.execute("INSERT INTO members (chat_id) VALUES (?)", (TG,))
        con.commit()
        con.close()
        reply = self.tg(TG, "/link_vk")
        code = reply.split("код ")[1].split()[0]
        self.assertIn("Заявка на подключение отправлена", self.vk("888", f"код {code.lower()}"))
        link_id = int(self.events()[0][0].split(":")[1])
        manual_bot.decide_channel_link(link_id, True, db_path=self.db)
        self.assertEqual(self.q("SELECT address FROM recipients WHERE child_id = 2 AND channel = 'vk'"), [("888",)])

    def test_rejected_link_adds_nothing_and_tells_the_new_chat_politely(self):
        self.tg(TG, f"/start {self.vk_link()}")
        link_id = int(self.events()[0][0].split(":")[1])
        self.assertIn("отклонена", manual_bot.decide_channel_link(link_id, False, db_path=self.db))
        self.assertEqual(self.q("SELECT count(*) FROM recipients WHERE channel = 'telegram'"), [(0,)])
        [(addr, text)] = self.outbox("telegram")
        self.assertIn("не получается", text)

    def test_codes_are_one_time_and_expire_and_only_families_get_them(self):
        start = self.vk_link()
        self.tg(TG, f"/start {start}")
        self.assertIn("недействителен", self.tg("556", f"/start {start}"))  # used
        self.assertIn("недействителен", self.tg("556", "/start link_ZZZZZZZZ"))  # never issued
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        past = datetime.now(timezone.utc) - timedelta(hours=cl.CODE_TTL_HOURS + 1)
        cl.issue(con, "vk", VK, "telegram", now=past, code="OLDCODE2")
        con.commit()
        con.close()
        self.assertIn("недействителен", self.tg("557", "/start link_OLDCODE2"))
        self.assertNotIn("t.me", self.vk("999", "подключить телеграм") or "")  # a stranger gets no code

    def test_a_vk_code_cannot_be_redeemed_in_vk(self):
        start = self.vk_link()
        code = start[len(cl.START_PREFIX):]
        self.assertIn("недействителен", self.vk("888", f"код {code}"))

    def test_owner_warning_when_the_new_chat_already_has_its_own_children(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (3, 'Петя', '2025-01-01')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (3, 'telegram', ?)", (TG,))
        con.execute("INSERT INTO members (chat_id) VALUES (?)", (TG,))
        con.commit()
        con.close()
        self.tg(TG, f"/start {self.vk_link()}")
        self.assertIn("уже есть свои дети: Петя", self.events()[0][1])

    def test_ops_bot_shows_buttons_for_link_requests(self):
        self.assertEqual(manual_bot.decision("link:7"), ("lnk", "7"))
        self.assertEqual(manual_bot.decision("application:3"), ("app", "3"))
        self.assertIsNone(manual_bot.decision("join"))
        c = manual_bot.Controller(42, decide_link=lambda i, ok: f"{i}:{ok}")
        self.assertEqual(c.button({"from": {"id": 42}, "data": "lnk:7:ok"}), "7:True")
        self.assertIsNone(c.button({"from": {"id": 43}, "data": "lnk:7:ok"}))


if __name__ == "__main__":
    unittest.main()
