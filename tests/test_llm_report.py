"""Model run metrics: captured per attempt (pass, rejected, error), persisted even when
the day fails, and summarised per model for the weekly report and /llm."""

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops import llm_report as lr  # noqa: E402
from src.preparation import game_preparation as gp  # noqa: E402

ENVELOPE = {
    "subtype": "success", "is_error": False, "duration_ms": 61000, "duration_api_ms": 58000, "num_turns": 4,
    "total_cost_usd": 0.42, "modelUsage": {"claude-opus-x": {}},
    "usage": {"input_tokens": 1200, "output_tokens": 900, "cache_read_input_tokens": 30000,
              "cache_creation_input_tokens": 5000, "server_tool_use": {"web_search_requests": 3}},
}


def good(age):
    return {
        "title": "Толкни мяч", "age_months": age, "duration_minutes": 5, "goal": "Тренировать ноги.",
        "skill": "coordination", "mechanics": "толкает мяч стопами", "materials": ["Мяч"],
        "steps": ["Положите мяч.", "Покажите."], "safety": "Под присмотром взрослого.",
        "source_name": "Pathways.org", "source_url": "https://pathways.org/activities/kickin-back",
    }


class Capture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        for target in (mock.patch.object(gp, "DB_PATH", self.db),
                       mock.patch.object(gp, "send_preparation_success_alert"),
                       mock.patch.object(gp, "send_failure_alert"),
                       mock.patch.dict("os.environ", {"CHILD_BIRTH_DATE": "2025-09-03", "VK_PEER_ID": "1"})):
            target.start()
            self.addCleanup(target.stop)

    def runs(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute("SELECT * FROM model_runs ORDER BY id")]
        finally:
            con.close()

    def fake_claude(self, candidates):
        def run(prompt):
            gp.LAST_RUN_META.update(gp.claude_meta(ENVELOPE))
            item = candidates.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return run

    def prepare(self, candidates):
        age = gp.child_age_months("2026-11-09", "2025-09-03")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(gp, "run_claude", self.fake_claude(candidates)):
            try:
                gp.prepare_run("2026-11-09")
            except RuntimeError:
                pass
        return age

    def test_a_rejected_then_passing_day_records_both_attempts(self):
        age = self.prepare([{**good(14), "duration_minutes": 30}, good(14)])
        runs = self.runs()
        self.assertEqual([(r["attempt"], r["outcome"]) for r in runs], [(1, "rejected"), (2, "pass")])
        self.assertIn("duration_minutes", json.loads(runs[0]["errors_json"])[0])
        self.assertEqual(runs[1]["model"], "claude-opus-x")
        self.assertEqual((runs[1]["output_tokens"], runs[1]["web_searches"], runs[1]["cost_usd"]), (900, 3, 0.42))
        self.assertEqual(age, 14)

    def test_a_failed_day_is_still_recorded(self):
        self.prepare([RuntimeError("Claude failed")])
        self.assertEqual([(r["attempt"], r["outcome"]) for r in self.runs()], [(1, "error")])
        self.assertIn("Claude failed", self.runs()[0]["errors_json"])

    def test_the_report(self):
        self.prepare([{**good(14), "duration_minutes": 30}, good(14)])
        text = lr.report(self.db, today=date(2026, 11, 10))
        self.assertIn("Модель: claude-opus-x", text)
        self.assertIn("игр подготовлено: 1", text)
        self.assertIn("попыток на игру: 2.0; с первой попытки: 0%", text)
        self.assertIn("всего $0.84, на игру $0.84", text)
        self.assertIn("duration_minutes must be between 5 and 10 ×1", text)
        self.assertIn("не было", lr.report(self.db, today=date(2027, 1, 1)))

    def test_metrics_never_break_preparation_on_an_old_database(self):
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE model_runs")
        con.commit()
        con.close()
        self.prepare([good(14)])
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT count(*) FROM games").fetchone()[0], 1)
        con.close()
