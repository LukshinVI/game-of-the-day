"""Owner monitoring: product-bot events are forwarded once, and /users summarises families."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.bot import product_bot as pb  # noqa: E402
from src.feedback import storage  # noqa: E402
from src.ops import manual_bot  # noqa: E402
from src.ops import users_report as ur  # noqa: E402

OWNER = "1000"
NOON = datetime(2026, 11, 8, 12, 0, tzinfo=pb.MOSCOW)


class Monitoring(unittest.TestCase):
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
        self.bot = pb.ProductBot(self.db, OWNER, "b", now=lambda: NOON)
        self.n = 0

    def say(self, chat, text):
        self.n += 1
        return self.bot.handle({"message": {"message_id": self.n, "text": text,
                                            "chat": {"id": int(chat), "type": "private"},
                                            "from": {"id": int(chat), "first_name": "Аня"}}})

    def onboard(self, chat):
        code = self.say(OWNER, "/invite").split("start=")[1].split()[0]
        for text in (f"/start {code}", "Маша", "15.03.2025", "да"):
            self.say(chat, text)

    def test_events_are_forwarded_once_and_only_after_a_successful_send(self):
        self.onboard("42")
        sent = []

        def failing(text):
            raise OSError("telegram down")

        with self.assertRaises(OSError):
            manual_bot.forward_user_events(failing, lambda: ur.pending_events(self.db),
                                           lambda ids: ur.mark_notified(ids, self.db))
        manual_bot.forward_user_events(sent.append, lambda: ur.pending_events(self.db),
                                       lambda ids: ur.mark_notified(ids, self.db))
        manual_bot.forward_user_events(sent.append, lambda: ur.pending_events(self.db),
                                       lambda ids: ur.mark_notified(ids, self.db))
        self.assertEqual(len(sent), 1)
        self.assertIn("Новый участник: Аня (chat 42)", sent[0])
        self.assertIn("Подписан ребёнок: Маша, 19 месяцев", sent[0])

    def test_the_report_shows_each_family(self):
        self.onboard("42")
        self.say("42", "/pause")
        code = self.say(OWNER, "/invite").split("start=")[1].split()[0]
        self.say("43", f"/start {code}")
        text = ur.report(self.db, now=NOON)
        self.assertIn("Аня · chat 42", text)
        self.assertIn("⏸ Маша (id 1), 19 мес. · последняя игра: ещё не было", text)
        self.assertIn("chat 43 · с ", text)
        self.assertIn("анкета не заполнена", text)
        self.assertIn("семей 2 (новых за неделю 2)", text)

    def test_an_unmigrated_database_is_reported_plainly(self):
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE user_events")
        con.execute("DROP TABLE onboarding")
        con.execute("DROP TABLE members")
        con.commit()
        con.close()
        self.assertEqual(ur.pending_events(self.db), [])
        self.assertIn("режиме одного ребёнка", ur.report(self.db))

    def test_users_command_is_owner_only(self):
        controller = manual_bot.Controller(1, run=lambda *a: "", start=lambda u: "", now=lambda: 100,
                                           users=lambda: "REPORT")
        update = {"message": {"text": "/users", "date": 100, "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}
        self.assertIn("REPORT", controller.handle(update))
        update["message"]["chat"]["id"] = update["message"]["from"]["id"] = 2
        self.assertIsNone(controller.handle(update))


if __name__ == "__main__":
    unittest.main()
