"""Sunday digest: content from the week's games and feedback, milestones, idempotency."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import children as ch  # noqa: E402
from src.ops import weekly_digest as wd  # noqa: E402


class Digest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        con = ch.connect_db(self.db)
        self.child = ch.add_child(con, "Маша", "2025-09-03")
        ch.add_recipient(con, self.child, "telegram", "42")
        test = ch.add_child(con, "Тест", "2025-09-03", is_test=True)
        ch.add_recipient(con, test, "vk", "1")
        for day, title, skill in (("2026-11-09", "Башенка", "fine_motor"), ("2026-11-10", "Ку-ку", "speech"),
                                  ("2026-11-11", "Мяч", "fine_motor")):
            gid = con.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, ?, ?)",
                              (title, day, title, skill, self.child)).lastrowid
            con.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, ?, ?)",
                        (title + "t", day, title, skill, test))
            if title == "Башенка":
                con.execute("INSERT INTO feedback(id, game_id, raw_text) VALUES (1, ?, 'x')", (gid,))
                con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, evidence) "
                            "VALUES (1, ?, 'outcome', 'liked', 'понравилось')", (gid,))
                con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, skill, evidence) "
                            "VALUES (1, ?, 'skill', 'went_well', 'fine_motor', 'строил сам')", (gid,))
        con.commit()
        con.close()
        self.sent = []

    def run_digest(self, day):
        return wd.run(day, db_path=self.db, sender=lambda ch_, addr, text, key: self.sent.append((ch_, addr, text)))

    def test_content_and_once_per_week(self):
        self.assertEqual(self.run_digest(date(2026, 11, 15)), 1)   # the test child gets nothing
        channel, address, text = self.sent[0]
        self.assertEqual((channel, address), ("telegram", "42"))
        self.assertIn("Итоги недели — Маша: 3 игры", text)
        self.assertIn("мелкая моторика ×2", text)
        self.assertIn("❤️ Понравилось: «Башенка»", text)
        self.assertIn("🌱 Получалось: строил сам", text)
        self.assertIn("к 15 мес.", text)
        self.assertIn("не медицинская оценка", text)
        self.assertNotIn("чего не хватает", text)
        self.assertEqual(self.run_digest(date(2026, 11, 15)), 0)   # idempotent

    def test_the_first_sunday_of_a_month_asks_what_is_missing(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET game_date = date('2026-12-06', '-' || (id % 3) || ' days')")
        con.commit()
        con.close()
        self.run_digest(date(2026, 12, 6))
        self.assertIn("чего не хватает", self.sent[0][2])

    def test_too_few_games_no_digest(self):
        self.assertEqual(self.run_digest(date(2027, 1, 10)), 0)
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET game_date = '2026-10-01' WHERE title = 'Мяч'")
        con.commit()
        con.close()
        self.assertEqual(self.run_digest(date(2026, 11, 15)), 0)  # two games are not a week
