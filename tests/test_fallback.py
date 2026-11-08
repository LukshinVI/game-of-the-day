"""Graceful degradation: primary model -> fallback model -> library, with honest owner alerts."""

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import children as ch  # noqa: E402
from src.preparation import fallback  # noqa: E402
from src.preparation import game_preparation as gp  # noqa: E402

TODAY = datetime.now(gp.MOSCOW).date().isoformat()


def good(age, title="Башенка", skill="fine_motor"):
    return {
        "title": title, "age_months": age, "duration_minutes": 5, "goal": "Строить.", "skill": skill,
        "mechanics": f"{title}: ставит кубик на кубик", "materials": ["Кубики"], "steps": ["Раз.", "Два."],
        "safety": "Под присмотром.", "source_name": "CDC", "source_url": "https://www.cdc.gov/x",
    }


class Chain(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        self.alerts = []
        for target in (
            mock.patch.object(gp, "DB_PATH", self.db), mock.patch.object(ch, "DB_PATH", self.db),
            mock.patch.object(gp, "send_ops_notification", lambda level, title, details: self.alerts.append((level, title, details))),
            mock.patch.object(gp, "send_failure_alert", lambda errors: self.alerts.append(("critical", "fail", errors))),
            mock.patch.dict("os.environ", {"PREPARATION_FALLBACK_MODEL": "haiku", "PREPARATION_DAILY_BUDGET_USD": "3"}),
        ):
            target.start()
            self.addCleanup(target.stop)
        con = ch.connect_db(self.db)
        self.child = ch.add_child(con, "Маша", "2025-09-03")
        ch.add_recipient(con, self.child, "telegram", "555")
        con.close()
        self.age = gp.child_age_months(TODAY, "2025-09-03")
        self.models = []

    def library_game(self, title, age, skill, origin="model", child=None, mechanics=None):
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO games(legacy_id, game_date, age_months, title, skill, mechanics, source_name, source_url, "
            "message_text, origin, child_id) VALUES (?, '2026-10-01', ?, ?, ?, ?, 'CDC', 'https://www.cdc.gov/y', ?, ?, ?)",
            (title, age, title, skill, mechanics or f"{title} mech", f"🎲 Игра дня: «{title}»", origin, child),
        )
        con.commit()
        con.close()

    def model(self, answers):
        def run(prompt):
            self.models.append(gp.MODEL_OVERRIDE or "primary")
            item = answers.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return run

    def prepare(self, answers):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(gp, "run_claude", self.model(answers)):
            try:
                gp.prepare_run(TODAY)
                return None
            except RuntimeError as exc:
                return exc

    def game(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return dict(con.execute("SELECT * FROM games WHERE game_date = ? AND child_id = ?",
                                    (TODAY, self.child)).fetchone() or {})
        finally:
            con.close()

    def test_the_fallback_model_saves_the_day_and_says_so(self):
        bad = {**good(self.age), "duration_minutes": 30}
        self.assertIsNone(self.prepare([bad, bad, bad, good(self.age)]))
        self.assertEqual(self.models, ["primary"] * 3 + ["haiku"])
        game = self.game()
        self.assertEqual(game["origin"], "fallback_model")
        self.assertIn("haiku", game["origin_reason"])
        level, title, details = self.alerts[-1]
        self.assertEqual((level, title), ("warning", "Игра подготовлена резервной моделью"))
        self.assertIn("Причина резерва", details)
        self.assertEqual(json.loads(game["candidate_json"])["duration_minutes"], 5)

    def test_an_exhausted_quota_goes_straight_to_the_library(self):
        self.library_game("Мячик в корзину", self.age - 1, "coordination")
        error = self.prepare([RuntimeError("Claude AI usage limit reached|1731000000")])
        self.assertIsNone(error)
        self.assertEqual(self.models, ["primary"])  # the fallback model shares the quota: not called
        game = self.game()
        self.assertEqual((game["origin"], game["title"]), ("library", "Мячик в корзину"))
        self.assertIn("лимит подписки", game["origin_reason"])
        self.assertEqual(game["message_text"], "🎲 Игра дня: «Мячик в корзину»")
        level, title, details = self.alerts[-1]
        self.assertEqual((level, title), ("warning", "Игра из резерва (библиотека), а не подобрана"))
        self.assertIn("копия игры #1", details)

    def test_a_spent_daily_budget_skips_the_models(self):
        self.library_game("Ладушки", self.age, "speech")
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO model_runs(target_date, attempt, outcome, cost_usd) VALUES (?, 1, 'pass', 3.5)", (TODAY,))
        con.commit()
        con.close()
        self.assertIsNone(self.prepare([]))
        self.assertEqual(self.models, [])
        self.assertIn("бюджет", self.game()["origin_reason"])

    def test_nothing_fits_alerts_once_and_fails(self):
        self.library_game("Для младенца", 2, "speech")  # far too young for a 14-month-old
        error = self.prepare([RuntimeError("usage limit")])
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual([a[0] for a in self.alerts], ["critical"])
        self.assertEqual(self.game(), {})


class LibraryChoice(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript((ROOT / "src/db/schema.sql").read_text())

    def add(self, title, age, skill, origin="model"):
        self.con.execute(
            "INSERT INTO games(legacy_id, game_date, age_months, title, skill, mechanics, source_url, message_text, origin) "
            "VALUES (?, '2026-10-01', ?, ?, ?, ?, 'https://x', 'text', ?)", (title, age, title, skill, title, origin))

    def pick(self, history, age=14):
        result = fallback.library_candidate(self.con, history, age, TODAY, 1)
        return result and result["title"]

    def test_rules(self):
        self.add("Старая речь", 13, "speech")
        self.add("Моторика", 14, "fine_motor")
        self.add("Копия", 14, "sensory", origin="library")   # copies are never sources
        self.add("Слишком взрослая", 20, "coordination")     # older than the child
        self.add("Воображение", 14, "imagination")           # not offered at 14 months
        history = [{"title": "Моторика", "skill": "speech"}, {"title": "X", "skill": "speech"}]
        # speech repeated in both of the last two games; "Моторика" already played
        self.assertIsNone(self.pick(history))
        self.add("Новая моторика", 12, "fine_motor")
        self.assertEqual(self.pick(history), "Новая моторика")

    def test_the_least_recent_skill_wins_then_the_closest_age(self):
        self.add("Речь 14", 14, "speech")
        self.add("Сенсорика 11", 11, "sensory")
        self.add("Сенсорика 13", 13, "sensory")
        history = [{"title": "a", "skill": "speech"}, {"title": "b", "skill": "coordination"}]
        self.assertEqual(self.pick(history), "Сенсорика 13")

    def test_quota_detection(self):
        self.assertTrue(fallback.is_quota_error(RuntimeError("Claude AI usage limit reached|123")))
        self.assertFalse(fallback.is_quota_error(RuntimeError("timeout")))
