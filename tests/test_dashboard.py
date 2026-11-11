"""Owner dashboard: a self-contained HTML file sent by the ops bot on /dash."""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops import dashboard, manual_bot  # noqa: E402

TODAY = date(2026, 11, 11)
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


class Dashboard(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, '<Сева>', '2025-09-01')")
        con.execute("INSERT INTO children (id, name, birth_date, is_test) VALUES (2, 'Тест', '2025-09-01', 1)")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', '7')")
        for child, day in ((1, "2026-11-10"), (1, "2026-11-11"), (2, "2026-11-11")):
            gid = con.execute("INSERT INTO games (game_date, title, child_id) VALUES (?, 'Игра', ?)", (day, child)).lastrowid
            con.execute("INSERT INTO deliveries (game_id, status, sent_at) VALUES (?, 'sent', ?)",
                        (gid, f"{day}T10:00:00+03:00"))
        con.execute("INSERT INTO feedback (game_id, raw_text, received_at) VALUES (1, 'класс', '2026-11-10T12:00:00+00:00')")
        con.commit()
        con.close()

    def test_page_is_self_contained_and_escapes_names(self):
        page, data = dashboard.build(self.db, today=TODAY, now=NOW)
        self.assertNotIn("<script", page)
        self.assertNotIn("http", page.split("</style>")[1])   # no external resources
        self.assertIn("&lt;Сева&gt;", page)
        self.assertNotIn("Тест", page)                         # test children stay out
        self.assertEqual(sum(data["sent"]), 2)
        self.assertEqual(data["sent"][-1], 1)
        self.assertEqual(sum(data["feedback"]), 1)
        self.assertIn("<svg", page)
        self.assertIn("детей 1", dashboard.summary(data))

    def test_every_view_is_self_contained(self):
        from src.ops import dashboard_views
        for view in (None, *dashboard_views.VIEWS):
            with self.subTest(view=view):
                page, data = dashboard.build(self.db, today=TODAY, now=NOW, view=view)
                self.assertNotIn("<script", page)
                self.assertIn("&lt;Сева&gt;", page)
                self.assertNotIn("Тест", page)
        self.assertEqual(data["funnel"][1], ("Подписано детей", 1))
        self.assertEqual(data["heat"][0]["cells"][-2:], ["fb", "sent"])

    def test_ops_bot_answers_dash_with_a_document(self):
        controller = manual_bot.Controller(42, now=lambda: 1000)
        with mock.patch.object(dashboard, "DB_PATH", self.db):
            reply = controller.handle({"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42},
                                                   "date": 1000, "text": "/dash"}})
        name, content = reply["document"]
        self.assertTrue(name.startswith("dashboard-") and name.endswith(".html"))
        self.assertIn(b"<svg", content)

    def test_send_document_builds_a_multipart_body(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"ok": True}).encode()

        def fake(request, timeout):
            seen["body"], seen["type"], seen["url"] = request.data, request.headers["Content-type"], request.full_url
            return Response()

        with mock.patch.object(manual_bot.urllib.request, "urlopen", fake):
            manual_bot.send_document("T", 42, "d.html", b"<html>", "подпись")
        self.assertTrue(seen["url"].endswith("/botT/sendDocument"))
        self.assertIn("multipart/form-data; boundary=", seen["type"])
        self.assertIn(b'name="document"; filename="d.html"', seen["body"])
        self.assertIn("подпись".encode(), seen["body"])


if __name__ == "__main__":
    unittest.main()
