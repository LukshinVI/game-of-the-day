"""Reliability objectives: on time, fresh games, answered feedback, backup freshness."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops import slo  # noqa: E402

TODAY = date(2026, 11, 11)


class Objectives(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'А', '2025-01-01')")
        con.execute("INSERT INTO children (id, name, birth_date, is_test) VALUES (9, 'Тест', '2025-01-01', 1)")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', '1')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (9, 'vk', '9')")
        con.commit()
        self.con = con
        self.addCleanup(con.close)

    def game(self, day, origin="model", child=1, sent_at=None, status="sent"):
        cur = self.con.execute("INSERT INTO games (game_date, title, child_id, origin) VALUES (?, 'И', ?, ?)",
                               (day, child, origin))
        self.con.execute("INSERT INTO deliveries (game_id, status, sent_at) VALUES (?, ?, ?)",
                         (cur.lastrowid, status, sent_at))
        self.con.commit()
        return cur.lastrowid

    def counts(self, start="2026-11-01", end="2026-11-10"):
        con = sqlite3.connect(self.db)
        try:
            return slo.collect(con, start, end, now=datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))
        finally:
            con.close()

    def test_child_days_count_misses_and_the_evening_before_is_on_time(self):
        self.game("2026-11-07", status="failed")                          # first day: missed
        self.game("2026-11-08", sent_at="2026-11-07T19:00:00+03:00")      # evening before: on time
        self.game("2026-11-09", sent_at="2026-11-09T07:20:00+00:00")      # 10:20 MSK: late, same day
        self.game("2026-11-10", sent_at="2026-11-11T09:00:00+03:00")      # next day: late, not same day
        counts = self.counts()
        self.assertEqual(counts["on_time"], (1, 4))   # 07..10, from the child's first game
        self.assertEqual(counts["same_day"], (2, 4))

    def test_today_counts_only_after_its_deadline(self):
        self.game("2026-11-11", status="pending")
        con = sqlite3.connect(self.db)
        try:
            early = slo.collect(con, "2026-11-11", "2026-11-11", now=datetime(2026, 11, 11, 6, 0, tzinfo=timezone.utc))
            late = slo.collect(con, "2026-11-11", "2026-11-11", now=datetime(2026, 11, 11, 8, 0, tzinfo=timezone.utc))
        finally:
            con.close()
        self.assertEqual(early["on_time"], (0, 0))
        self.assertEqual(late["on_time"], (0, 1))

    def test_reserve_and_library_are_not_fresh_and_test_children_do_not_count(self):
        self.game("2026-11-10")
        self.game("2026-11-09", origin="library")
        self.game("2026-11-08", origin="reserve")
        self.game("2026-11-08", child=9, origin="reserve")
        self.assertEqual(self.counts()["fresh"], (1, 3))

    def test_feedback_insights_within_the_hour_deferred_left_out(self):
        game = self.game("2026-11-10", sent_at="2026-11-10T10:00:00+03:00")
        for received, done, status in (("2026-11-10T17:39:15+00:00", "2026-11-10T17:52:41+00:00", "done"),
                                       ("2026-11-10T06:00:00+00:00", "2026-11-10 07:30:00", "done"),
                                       ("2026-11-10T06:00:00+00:00", None, None),
                                       ("2026-11-10T06:00:00+00:00", None, "deferred")):
            self.con.execute("INSERT INTO feedback (game_id, raw_text, received_at, insights_at, insights_status) "
                             "VALUES (?, 'x', ?, ?, ?)", (game, received, done, status))
        self.con.commit()
        self.assertEqual(self.counts()["feedback"], (1, 3))

    def test_error_budget_wording(self):
        self.assertIn("запас ошибок 0 из 1", slo.line("t", 0.90, 9, 10))
        self.assertIn("🔴", slo.line("t", 0.95, 8, 10))
        self.assertIn("нет событий", slo.line("t", 0.95, 0, 0))

    def test_report_reads_backup_manifest(self):
        manifest = self.dir / ".last-local-ok"
        manifest.write_text("1000 game-x.db.gz\n")
        text = slo.report(db_path=self.db, today=TODAY, manifest=manifest, now=1000 + 3600 * 30)
        self.assertIn("🔴 Бэкап: последний 30 ч назад", text)
        text = slo.report(db_path=self.db, today=TODAY, manifest=self.dir / "missing")
        self.assertIn("не удалось прочитать", text)


if __name__ == "__main__":
    unittest.main()
