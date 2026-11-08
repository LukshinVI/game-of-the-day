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
                self.assertIn("in flight", str(error))
                vk.assert_not_called()

    def test_retry_failed_never_sends_a_pending_delivery(self):
        vk, error = self.run_send(retry_failed=True)
        self.assertIn("Nothing to retry", str(error))
        vk.assert_not_called()
        self.assertEqual(self.row()["status"], "pending")

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

    def test_state_prints_one_word_for_every_situation(self):
        def state():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                gd.print_state()
            return out.getvalue().strip()

        self.assertEqual(state(), "pending")
        for status in ("sending", "sent", "failed"):
            self.set_status(status)
            self.assertEqual(state(), status)
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO games(id, legacy_id, game_date, title, message_text) VALUES (2, '002', ?, 'T2', 't')", (self.today,))
        con.execute("INSERT INTO deliveries(game_id, status, idempotency_key) VALUES (2, 'pending', 'key-2')")
        con.commit()
        self.assertEqual(state(), "ambiguous")
        con.execute("UPDATE games SET game_date = '2000-01-01'")
        con.commit()
        con.close()
        self.assertEqual(state(), "none")

    def test_cli_exposes_the_retry_flag_and_no_send_last(self):
        parser_help = io.StringIO()
        with contextlib.redirect_stdout(parser_help), self.assertRaises(SystemExit):
            with mock.patch.object(sys, "argv", ["game-delivery", "send-today", "--help"]):
                gd.main()
        self.assertIn("--retry-failed", parser_help.getvalue())
        self.assertFalse(hasattr(gd, "send_last"))


class StuckSending(unittest.TestCase):
    """Issue #28: an interrupted 'sending' delivery is resolved from VK history."""

    setUp = SendToday.setUp
    row = SendToday.row
    run_send = SendToday.run_send

    def make_stuck(self, minutes_ago):
        con = sqlite3.connect(self.db)
        con.execute(
            "UPDATE deliveries SET status='sending', attempts=1, "
            "updated_at=datetime('now', ?) WHERE game_id=1",
            (f"-{minutes_ago} minutes",),
        )
        con.commit()
        con.close()

    def run_with_history(self, found_id, **kwargs):
        finder = mock.Mock(return_value=found_id)
        with mock.patch.object(gd, "vk_find_sent", finder):
            vk, error = self.run_send(**kwargs)
        return finder, vk, error

    def test_a_message_found_in_vk_is_recorded_as_sent_without_resending(self):
        self.make_stuck(30)
        finder, vk, error = self.run_with_history(77)
        self.assertIsNone(error)
        vk.assert_not_called()
        finder.assert_called_once_with(gd.deterministic_random_id("key-1"), None)
        row = self.row()
        self.assertEqual((row["status"], row["external_message_id"]), ("sent", "77"))

    def test_a_message_missing_in_vk_becomes_failed_and_needs_an_explicit_retry(self):
        self.make_stuck(30)
        _, vk, error = self.run_with_history(None)
        vk.assert_not_called()
        self.assertIn("explicit retry", str(error))
        row = self.row()
        self.assertEqual(row["status"], "failed")
        self.assertIn("not found in VK", row["last_error"])
        # the explicit retry then resends with the same random_id
        vk, error = self.run_send(retry_failed=True)
        self.assertIsNone(error)
        vk.assert_called_once_with("text", gd.deterministic_random_id("key-1"))

    def test_retry_failed_does_not_resend_a_freshly_recovered_failure(self):
        self.make_stuck(30)
        _, vk, error = self.run_with_history(None, retry_failed=True)
        vk.assert_not_called()
        self.assertEqual(self.row()["status"], "failed")

    def test_a_recent_sending_is_left_alone_as_possibly_in_flight(self):
        self.make_stuck(1)
        finder, vk, error = self.run_with_history(77)
        finder.assert_not_called()
        vk.assert_not_called()
        self.assertIn("in flight", str(error))
        self.assertEqual(self.row()["status"], "sending")

    def test_recover_command_is_a_no_op_without_a_stuck_delivery(self):
        finder = mock.Mock()
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(gd, "vk_find_sent", finder):
            gd.recover_command()
        finder.assert_not_called()
        self.assertIn("Nothing to recover", out.getvalue())

    def test_staleness_uses_the_utc_sqlite_timestamp(self):
        now = datetime(2026, 11, 8, 7, 0, tzinfo=gd.timezone.utc)
        self.assertTrue(gd.is_stale("2026-11-08 06:50:00", now))
        self.assertFalse(gd.is_stale("2026-11-08 06:55:00", now))


if __name__ == "__main__":
    unittest.main(verbosity=2)
