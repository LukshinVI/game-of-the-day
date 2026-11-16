"""src/ops/slo_watch.py: an alert when an error budget changes level, silence otherwise."""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops import slo_watch  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "db" / "schema.sql"
TODAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


class SloWatch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        self.state = Path(tmp.name) / "state.json"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA.read_text())
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'A', '2025-09-01')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', '1')")
        con.commit()
        con.close()
        self.sent = []

    def days(self, first, last, late=()):
        con = sqlite3.connect(self.db)
        day = first
        while day <= last:
            gid = day.toordinal()
            con.execute("INSERT INTO games (id, child_id, game_date, title, skill, mechanics) VALUES (?, 1, ?, 't', 'speech', 'm')",
                        (gid, day.isoformat()))
            hour = "15" if day in late else "10"
            con.execute("INSERT INTO deliveries (game_id, status, sent_at) VALUES (?, 'sent', ?)",
                        (gid, f"{day.isoformat()}T{hour}:00:00+03:00"))
            day = date.fromordinal(day.toordinal() + 1)
        con.commit()
        con.close()

    def run_watch(self, today=TODAY):
        return slo_watch.run(db_path=self.db, state=self.state, today=today,
                             now=datetime.combine(today, datetime.min.time(), timezone.utc).replace(hour=15),
                             send=lambda head, details: self.sent.append((head, details)))

    def test_levels(self):
        self.assertEqual(slo_watch.level(0.95, 0, 0), "ok")
        self.assertEqual(slo_watch.level(0.95, 40, 40), "ok")
        self.assertEqual(slo_watch.level(0.95, 39, 40), "ok")   # allowed 2, one used
        self.assertEqual(slo_watch.level(0.95, 38, 40), "last")
        self.assertEqual(slo_watch.level(0.95, 37, 40), "out")
        self.assertEqual(slo_watch.level(0.99, 27, 27), "ok")   # nothing allowed, nothing missed
        self.assertEqual(slo_watch.level(0.99, 26, 27), "out")

    def test_first_run_records_quietly_then_only_changes_are_sent(self):
        self.days(date(2026, 11, 1), TODAY)
        self.assertIn("first run", self.run_watch())
        self.assertEqual(self.sent, [])
        self.assertIn("on_time:7", json.loads(self.state.read_text()))
        self.assertEqual(self.run_watch(), "no change")
        self.assertEqual(self.sent, [])

    def test_a_late_week_warns_once_and_recovery_is_reported(self):
        self.days(date(2026, 11, 1), date(2026, 11, 30),
                  late={date(2026, 11, 19), date(2026, 11, 20)})
        self.run_watch(date(2026, 11, 18))  # first run: all fine
        self.run_watch(date(2026, 11, 19))
        self.run_watch(date(2026, 11, 20))
        self.assertTrue(self.sent)
        head, details = self.sent[-1]
        self.assertIn("тает", head)
        self.assertIn("🔴 За 7 дн.", details)
        count = len(self.sent)
        self.run_watch(date(2026, 11, 21))
        self.assertEqual(len(self.sent), count, "an unchanged level is silence")
        self.run_watch(date(2026, 11, 27))  # both late days left the 7-day window
        head, details = self.sent[-1]
        self.assertIn("восстановился", head)
        self.assertIn("🟢 За 7 дн.", details)

    def test_dry_run_sends_and_writes_nothing(self):
        self.days(date(2026, 11, 1), TODAY)
        self.state.write_text(json.dumps({"on_time:7": {"level": "out", "line": ""}}))
        out = slo_watch.run(db_path=self.db, state=self.state, today=TODAY, now=NOW, dry_run=True,
                            send=lambda *a: self.sent.append(a))
        self.assertIn("восстановился", out)
        self.assertEqual(self.sent, [])
        self.assertEqual(json.loads(self.state.read_text())["on_time:7"]["level"], "out")


if __name__ == "__main__":
    unittest.main()
