"""Tests for the independent daily-outcome check (src/ops/day_outcome_check.py).

A temporary database and a mocked alert: no network, no real database, no notifier.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ops import day_outcome_check as doc  # noqa: E402

SCHEMA = PROJECT_ROOT / "src" / "db" / "schema.sql"
TODAY = "2026-09-24"
AFTER_DEADLINE = datetime(2026, 9, 24, 11, 0, tzinfo=doc.MOSCOW)
BEFORE_DEADLINE = datetime(2026, 9, 24, 9, 30, tzinfo=doc.MOSCOW)


class Evaluate(unittest.TestCase):
    def rows(self, **overrides):
        row = {"legacy_id": "023", "title": "Эхо", "status": "sent", "attempts": 1,
               "external_message_id": "46", "last_error": None}
        row.update(overrides)
        return [row]

    def test_a_sent_delivery_with_a_message_id_meets_the_outcome(self):
        self.assertIsNone(doc.evaluate(self.rows()))

    def test_no_game_for_today_is_a_problem(self):
        self.assertIn("нет подготовленной игры", doc.evaluate([]))

    def test_every_unsent_status_is_a_problem_that_names_the_status(self):
        for status in ("pending", "sending", "failed"):
            with self.subTest(status=status):
                text = doc.evaluate(self.rows(status=status, external_message_id=None))
                self.assertIn(f"Статус: {status}", text)
                self.assertIn("/send", text)

    def test_the_failure_reason_is_shown_and_bounded(self):
        text = doc.evaluate(self.rows(status="failed", external_message_id=None, last_error="x" * 500))
        self.assertIn("Ошибка: " + "x" * doc.MAX_ERROR_CHARS, text)
        self.assertNotIn("x" * (doc.MAX_ERROR_CHARS + 1), text)

    def test_sent_without_a_vk_message_id_is_inconsistent(self):
        self.assertIn("нет номера сообщения", doc.evaluate(self.rows(external_message_id=None)))

    def test_several_deliveries_for_today_need_a_manual_look(self):
        self.assertIn("найдено доставок: 2", doc.evaluate(self.rows() + self.rows()))


class Check(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "test.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA.read_text())
        con.close()
        self.alert = mock.Mock(return_value=True)

    def add(self, game_id, date, status, external=None, error=None):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO games(id, legacy_id, game_date, title, message_text) VALUES (?,?,?,?,?)",
                    (game_id, f"{game_id:03d}", date, f"T{game_id}", "text"))
        con.execute("INSERT INTO deliveries(game_id, status, external_message_id, last_error, idempotency_key) VALUES (?,?,?,?,?)",
                    (game_id, status, external, error, f"k{game_id}"))
        con.commit()
        con.close()

    def run_check(self, now=AFTER_DEADLINE, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return doc.check(self.db, now=now, alert=self.alert, **kwargs)

    def test_a_delivered_day_is_silent(self):
        self.add(1, TODAY, "sent", external="46")
        self.assertEqual(self.run_check(), 0)
        self.alert.assert_not_called()

    def test_an_undelivered_day_raises_one_alert_and_fails_the_unit(self):
        self.add(1, TODAY, "pending")
        self.assertEqual(self.run_check(), 1)
        self.alert.assert_called_once()
        self.assertIn("Статус: pending", self.alert.call_args.args[0])

    def test_a_missing_game_raises_an_alert(self):
        self.assertEqual(self.run_check(), 1)
        self.assertIn("нет подготовленной игры", self.alert.call_args.args[0])

    def test_other_days_do_not_count(self):
        self.add(1, "2026-09-23", "sent", external="45")
        self.add(2, "2026-09-25", "pending")
        self.assertEqual(self.run_check(), 1)

    def test_before_the_deadline_nothing_is_judged(self):
        self.assertEqual(self.run_check(now=BEFORE_DEADLINE), 0)
        self.alert.assert_not_called()

    def test_the_deadline_is_configurable(self):
        self.assertEqual(self.run_check(now=BEFORE_DEADLINE, not_before="09:00"), 1)

    def test_when_the_alert_cannot_be_sent_the_check_says_so(self):
        self.add(1, TODAY, "failed", error="VK down")
        self.alert.return_value = False
        self.assertEqual(self.run_check(), 2)

    def test_an_unreadable_database_is_itself_a_failed_day(self):
        self.db.write_bytes(b"not a database" * 100)
        self.assertEqual(self.run_check(), 1)
        self.assertIn("Не удалось прочитать", self.alert.call_args.args[0])
        self.alert.return_value = False
        self.assertEqual(self.run_check(), 2)

    def test_the_check_never_writes_to_the_database(self):
        self.add(1, TODAY, "failed", error="e")
        before = self.db.read_bytes()
        self.run_check()
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
