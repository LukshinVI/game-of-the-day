"""The reminder under the day's game for families who never write or went quiet
(src/feedback/nudge.py)."""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bot import erasure  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.delivery import game_delivery as gd  # noqa: E402
from src.feedback import nudge  # noqa: E402

THURSDAY = date(2026, 11, 12)


class Nudge(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "n.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        patcher = mock.patch.object(gd, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {nudge.ENABLED_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)
        self.con = ch.connect_db(self.db)
        self.addCleanup(self.con.close)
        self.child = ch.add_child(self.con, "Сева", "2025-05-01")
        ch.add_recipient(self.con, self.child, "telegram", "555")
        ch.add_recipient(self.con, self.child, "vk", "777")
        self.con.commit()

    def game(self, day, status="sent"):
        gid = self.con.execute("INSERT INTO games (game_date, title, child_id, skill, message_text) "
                               "VALUES (?, ?, ?, 'speech', '🎲 Игра')",
                               (day.isoformat(), f"Игра {day}", self.child)).lastrowid
        ids = [self.con.execute(
            "INSERT INTO deliveries (game_id, channel, recipient, status, idempotency_key) VALUES (?, ?, ?, ?, ?)",
            (gid, channel, address, status, f"k{gid}{channel}")).lastrowid
            for channel, address in (("telegram", "555"), ("vk", "777"))]
        self.con.commit()
        return gid, ids

    def history(self, days, until=THURSDAY):
        for i in range(days, 0, -1):
            self.game(until - timedelta(days=i))

    def wrote(self, day, reaction=False):
        gid = self.con.execute("SELECT id FROM games WHERE game_date = ?", (day.isoformat(),)).fetchone()[0]
        self.con.execute("INSERT INTO feedback (game_id, channel, raw_text, received_at, insights_status) "
                         "VALUES (?, 'telegram', ?, ?, ?)",
                         (gid, "[стикер]" if reaction else "понравилось", f"{day}T10:00:00+03:00",
                          "reaction" if reaction else None))
        self.con.commit()

    def due(self, day=THURSDAY):
        gid, _ = self.game(day, status="pending")
        return nudge.kind_due(self.con, self.child, gid, day)

    def deliver(self, day=THURSDAY):
        _, ids = self.game(day, status="pending")
        sent = []
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(gd, "telegram_send", side_effect=lambda t, c: sent.append(t) or 1), \
                mock.patch.object(gd, "vk_send", side_effect=lambda t, r, peer_id=None: sent.append(t) or 2), \
                mock.patch.object(gd, "send_ops_notification"):
            for delivery_id in ids:
                gd.deliver_one(delivery_id)
        return sent

    def test_never_wrote_after_five_games_under_the_game_for_every_recipient(self):
        self.history(4)
        self.assertIsNone(nudge.kind_due(self.con, self.child, 0, THURSDAY))
        self.game(THURSDAY - timedelta(days=5))
        sent = self.deliver()
        self.assertEqual(len(sent), 2)
        for text in sent:
            self.assertTrue(text.endswith(nudge.TEXTS["never"]), text[-80:])
            self.assertNotIn(gd.FEEDBACK_FOOTER, text)
            self.assertIn("🎲 Игра", text)
        self.assertEqual(self.con.execute("SELECT kind FROM nudges").fetchall()[0][0], "never")

    def test_not_again_for_30_days_and_at_most_three_times(self):
        self.history(5, until=THURSDAY - timedelta(days=120))
        start = THURSDAY - timedelta(days=120)
        for k in range(4):
            day = start + timedelta(days=31 * k)
            if day.weekday() == 6:
                day += timedelta(days=1)
            text = self.deliver(day)[0]
            self.assertEqual(text.endswith(nudge.TEXTS["never"]), k < 3, (k, day))
            self.assertFalse(self.deliver(day + timedelta(days=1))[0].endswith(nudge.TEXTS["never"]))
            for i in range(2, 31):
                self.game(day + timedelta(days=i))

    def test_a_sticker_is_not_an_answer_a_word_is(self):
        self.history(6)
        self.wrote(THURSDAY - timedelta(days=3), reaction=True)
        self.assertEqual(self.due(), "never")
        self.con.execute("DELETE FROM nudges")
        self.wrote(THURSDAY - timedelta(days=2))
        self.assertIsNone(nudge.kind_due(self.con, self.child, 0, THURSDAY))

    def test_quiet_after_three_weeks_of_games_but_not_after_a_pause(self):
        self.history(40)
        self.wrote(THURSDAY - timedelta(days=40))
        self.assertEqual(nudge.kind_due(self.con, self.child, 0, THURSDAY), "quiet")
        self.wrote(THURSDAY - timedelta(days=20))
        self.assertIsNone(nudge.kind_due(self.con, self.child, 0, THURSDAY))
        # back from a three-week pause: silent, but there were no games to answer
        self.con.execute("DELETE FROM deliveries WHERE game_id IN (SELECT id FROM games WHERE game_date > ?)",
                         ((THURSDAY - timedelta(days=20)).isoformat(),))
        self.assertIsNone(nudge.kind_due(self.con, self.child, 0, THURSDAY + timedelta(days=5)))
        self.assertEqual(self.deliver()[0].count("💬"), 1)  # the usual footer

    def test_a_score_counts_as_speaking(self):
        self.history(30)
        self.con.execute("INSERT INTO pulses (channel, address, child_id, month, score, answered_at) "
                         "VALUES ('telegram', '555', ?, '2026-11', 4, ?)",
                         (self.child, f"{THURSDAY - timedelta(days=3)} 10:00:00"))
        self.assertIsNone(nudge.kind_due(self.con, self.child, 0, THURSDAY))

    def test_off_by_default_and_never_on_sunday(self):
        self.history(10)
        sunday = THURSDAY + timedelta(days=3)
        self.assertIsNone(nudge.kind_due(self.con, self.child, 0, sunday))
        with mock.patch.dict(os.environ, {nudge.ENABLED_ENV: "0"}):
            self.assertTrue(self.deliver()[0].endswith(gd.FEEDBACK_FOOTER))

    def test_a_retry_carries_the_same_reminder_and_erasure_takes_the_rows(self):
        self.history(5)
        _, ids = self.game(THURSDAY, status="pending")
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(gd, "send_ops_notification"), \
                mock.patch.object(gd, "telegram_send", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                gd.deliver_one(ids[0])
        sent = []
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(gd, "send_ops_notification"), \
                mock.patch.object(gd, "telegram_send", side_effect=lambda t, c: sent.append(t) or 1):
            gd.deliver_one(ids[0], retry_failed=True)
        self.assertTrue(sent[0].endswith(nudge.TEXTS["never"]))
        kids, addrs = erasure.family(self.con, "telegram", "555")
        archive = erasure.snapshot(self.con, kids, addrs)
        self.assertEqual(len(archive["nudges"]), 1)
        erasure.remove(self.con, kids, addrs)
        self.assertEqual(self.con.execute("SELECT count(*) FROM nudges").fetchone()[0], 0)
        erasure.put_back(self.con, archive)
        self.assertEqual(self.con.execute("SELECT count(*) FROM nudges").fetchone()[0], 1)

    def test_summary_counts_answers_within_a_week(self):
        self.history(5)
        self.deliver()
        self.game(THURSDAY + timedelta(days=1))
        self.wrote(THURSDAY + timedelta(days=1))
        self.assertEqual(nudge.summary(self.con, THURSDAY + timedelta(days=2)), (1, 1))


if __name__ == "__main__":
    unittest.main()
