"""The weekly test run of the independent model: real children's context, owner only,
nothing written as a game or sent."""

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.preparation import independent_trial as trial  # noqa: E402
from tests.test_fallback import TODAY  # noqa: E402
from tests.test_independent_model import IndependentSetup  # noqa: E402


class Trial(IndependentSetup):
    def setUp(self):
        super().setUp()
        self.sent = []

    def report(self):
        return trial.run_trial(TODAY, 2, send=lambda level, title, details: self.sent.append((level, title, details)))

    def test_chooses_reviews_and_tells_only_the_owner(self):
        self.answers = [self.candidate()]
        self.verdicts = [{"block": [], "fix": []}]
        with mock.patch.dict("os.environ", {"PREPARATION_TRIAL_MODEL": "@cf/trial/m"}):
            self.report()
        level, title, details = self.sent[0]
        self.assertEqual(title, "🧪 Тестовый подбор Workers AI: подобрано 1 из 1")
        self.assertIn("«Кубики в коробку»", details)
        self.assertIn("Маша", details)
        self.assertIn("Ничего не записано", details)
        self.assertIn("Вызовов: 2.", details)  # selection and review, counted against the cap
        self.assertEqual(self.reviews, ["@cf/trial/m"])
        self.assertEqual(self.game(), {})  # no game for today
        con = sqlite3.connect(self.db)
        runs = con.execute("SELECT model, dry_run FROM model_runs").fetchall()
        outbox = con.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        con.close()
        self.assertTrue(runs and all(dry for _, dry in runs))
        self.assertEqual(outbox, 0)
        self.assertEqual(self.alerts, [])  # no per-child alert either

    def test_a_reviewer_block_is_reported_with_its_reason(self):
        risky = self.candidate(materials=["Кубики", "Подушка на диване"])
        self.answers = [risky] * 3
        self.verdicts = [{"block": ["Ребёнок на диване без опоры"], "fix": []}] * 3
        self.report()
        _, title, details = self.sent[0]
        self.assertIn("подобрано 0 из 1", title)
        self.assertIn("⛔ не подобрал", details)
        self.assertIn("🛡 safety review: Ребёнок на диване без опоры", details)

    def test_no_token_does_nothing(self):
        with mock.patch.dict("os.environ", {"CF_AI_TOKEN": ""}):
            self.assertIsNone(self.report())
        self.assertEqual(self.sent, [])


class Pick(unittest.TestCase):
    def test_everyone_in_turn_across_weeks(self):
        kids = [{"id": i} for i in range(1, 7)]
        weeks = ["2026-11-18", "2026-11-25", "2026-12-02"]
        seen = [c["id"] for w in weeks for c in trial.pick(kids, w, 2)]
        self.assertEqual(sorted(seen), [1, 2, 3, 4, 5, 6])

    def test_fewer_children_than_asked(self):
        self.assertEqual(trial.pick([{"id": 5}], "2026-11-18", 2), [{"id": 5}])
        self.assertEqual(trial.pick([], "2026-11-18", 2), [])


if __name__ == "__main__":
    unittest.main()
