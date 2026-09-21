"""Tests for the delivery state machine (send_today) against a temporary database.

VK and the ops notifier are mocked; nothing touches the network or a real database.
"""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.delivery import game_delivery as gd  # noqa: E402

SCHEMA = PROJECT_ROOT / "src" / "db" / "schema.sql"


class SendToday(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "test.db"
        for patcher in (
            mock.patch.object(gd, "DB_PATH", db),
            mock.patch.object(gd, "send_ops_notification"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        con = sqlite3.connect(db)
        con.executescript(SCHEMA.read_text())
        self.today = datetime.now(gd.MOSCOW).date().isoformat()
        con.execute(
            "INSERT INTO games(id, legacy_id, game_date, title, message_text) VALUES (1, '001', ?, 'T', 'text')",
            (self.today,),
        )
        con.execute("INSERT INTO deliveries(game_id, status, idempotency_key) VALUES (1, 'pending', 'key-1')")
        con.commit()
        con.close()
        self.db = db

    def row(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        return con.execute("SELECT * FROM deliveries WHERE game_id = 1").fetchone()

    def set_status(self, status):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE deliveries SET status = ? WHERE game_id = 1", (status,))
        con.commit()
        con.close()

    def run_send(self, vk=None, **kwargs):
        vk = vk or mock.Mock(return_value=44)
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(gd, "vk_send", vk):
            try:
                gd.send_today(**kwargs)
                error = None
            except Exception as exc:  # noqa: BLE001 - asserted by callers
                error = exc
        return vk, error

    def test_a_pending_delivery_is_sent_once_and_recorded(self):
        vk, error = self.run_send()
        self.assertIsNone(error)
        vk.assert_called_once()
        row = self.row()
        self.assertEqual((row["status"], row["external_message_id"], row["attempts"]), ("sent", "44", 1))
        self.assertTrue(row["sent_at"])

    def test_an_already_sent_delivery_is_not_sent_again(self):
        self.run_send()
        vk, error = self.run_send()
        self.assertIsNone(error)
        vk.assert_not_called()

    def test_a_vk_failure_marks_the_delivery_failed_with_the_reason(self):
        vk, error = self.run_send(vk=mock.Mock(side_effect=RuntimeError("VK down")))
        self.assertIsInstance(error, RuntimeError)
        row = self.row()
        self.assertEqual((row["status"], row["last_error"], row["external_message_id"]), ("failed", "VK down", None))

    def test_a_failed_delivery_needs_an_explicit_retry(self):
        self.set_status("failed")
        vk, error = self.run_send()
        self.assertIn("--retry-failed", str(error))
        vk.assert_not_called()
        self.assertEqual(self.row()["status"], "failed")

    def test_retry_failed_sends_with_the_same_random_id_so_vk_can_deduplicate(self):
        first, _ = self.run_send(vk=mock.Mock(side_effect=RuntimeError("timeout")))
        random_id = first.call_args.args[1]
        vk, error = self.run_send(retry_failed=True)
        self.assertIsNone(error)
        self.assertEqual(vk.call_args.args[1], random_id)
        row = self.row()
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("sent", 2, None))

    def test_retry_failed_does_not_touch_other_states(self):
        for status in ("sending",):
            with self.subTest(status=status):
                self.set_status(status)
                vk, error = self.run_send(retry_failed=True)
                self.assertIn("manual recovery", str(error))
                vk.assert_not_called()

    def test_retry_failed_on_a_sent_delivery_sends_nothing(self):
        self.run_send()
        vk, _ = self.run_send(retry_failed=True)
        vk.assert_not_called()

    def test_no_game_for_today_sends_nothing(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET game_date = '2000-01-01'")
        con.commit()
        con.close()
        vk, error = self.run_send()
        self.assertIsNone(error)
        vk.assert_not_called()

    def test_cli_exposes_the_retry_flag_and_no_send_last(self):
        parser_help = io.StringIO()
        with contextlib.redirect_stdout(parser_help), self.assertRaises(SystemExit):
            with mock.patch.object(sys, "argv", ["game-delivery", "send-today", "--help"]):
                gd.main()
        self.assertIn("--retry-failed", parser_help.getvalue())
        self.assertFalse(hasattr(gd, "send_last"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
