"""Product metrics: shares, the comparison period, test children excluded."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import children as ch  # noqa: E402
from src.ops import product_metrics as pm  # noqa: E402


class Metrics(unittest.TestCase):
    def test_report(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        con = ch.connect_db(db)
        kid = ch.add_child(con, "Маша", "2025-09-03")
        test = ch.add_child(con, "Тест", "2025-09-03", is_test=True)
        for i, (day, child) in enumerate((("2026-11-10", kid), ("2026-11-11", kid), ("2026-11-12", kid),
                                          ("2026-11-03", kid), ("2026-11-11", test))):
            gid = con.execute("INSERT INTO games(legacy_id, game_date, title, child_id, origin) VALUES (?, ?, 'G', ?, ?)",
                              (f"g{i}", day, child, "library" if i == 2 else "model")).lastrowid
            con.execute("INSERT INTO deliveries(game_id, status, idempotency_key) VALUES (?, 'sent', ?)", (gid, f"k{i}"))
            if i in (0, 1, 4):
                status = "reaction" if i == 1 else "done"
                fid = con.execute("INSERT INTO feedback(game_id, raw_text, insights_status) VALUES (?, 'x', ?)",
                                  (gid, status)).lastrowid
                if i in (0, 4):
                    con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, evidence) "
                                "VALUES (?, ?, 'difficulty', 'too_easy', 'e')", (fid, gid))
                    con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, evidence) "
                                "VALUES (?, ?, 'outcome', 'liked', 'e')", (fid, gid))
        con.commit()
        con.close()
        text = pm.report(db, today=date(2026, 11, 15))
        self.assertIn("игр доставлено: 3 (до этого 1)", text)            # the test child is not counted
        self.assertIn("с письменным отзывом: 33% (1 из 3)", text)
        self.assertIn("только реакция (стикер, фото): 1", text)
        self.assertIn("понравилось: 100% (1 из 1)", text)
        self.assertIn("легко 100%", text)
        self.assertIn("дней из резерва: 1", text)
