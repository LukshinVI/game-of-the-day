"""The independent model (Workers AI): another provider when Claude cannot answer, citing
only known sources, and never sent unreviewed."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.preparation import review, workers_ai  # noqa: E402
from tests.test_fallback import ChainSetup, TODAY, good  # noqa: E402

KNOWN = "https://www.cdc.gov/y"
MODEL = "@cf/test/model"


class IndependentSetup(ChainSetup):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict("os.environ", {
            "PREPARATION_INDEPENDENT_MODEL": MODEL, "CF_AI_TOKEN": "t", "CF_ACCOUNT_ID": "a",
            "PREPARATION_REVIEW_MODEL": "haiku", "PREPARATION_REVIEW_TIERS": "1"})
        env.start()
        self.addCleanup(env.stop)
        # A game an earlier day sent makes its source a known one.
        self.library_game("Прятки с платком", self.age - 2, "cognitive")
        self.calls, self.reviews = [], []
        self.answers, self.verdicts = [], []

        def post(url, body, timeout):
            schema = body["response_format"]["json_schema"]
            if "block" in schema["properties"]:
                self.reviews.append(url.rsplit("/run/", 1)[1])
                item = self.verdicts.pop(0)
            else:
                self.calls.append(body["messages"][0]["content"])
                item = self.answers.pop(0)
            if isinstance(item, Exception):
                raise item
            return {"success": True, "result": {"response": item, "usage": {"neurons": 250}}}

        patch = mock.patch.object(workers_ai, "_post", post)
        patch.start()
        self.addCleanup(patch.stop)
        haiku = mock.patch.object(review, "_run", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("Claude AI usage limit reached")))
        haiku.start()
        self.addCleanup(haiku.stop)

    def candidate(self, **change):
        return {**good(self.age, title="Кубики в коробку"), "source_url": KNOWN, **change}

    def runs(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT model, outcome, cost_usd FROM model_runs ORDER BY id").fetchall()
        finally:
            con.close()


class Independent(IndependentSetup):
    def test_claude_out_of_quota_the_independent_model_saves_the_day(self):
        self.answers = [self.candidate()]
        self.verdicts = [{"block": [], "fix": []}]
        self.assertIsNone(self.prepare([RuntimeError("Claude AI usage limit reached|1731000000")]))
        game = self.game()
        self.assertEqual((game["origin"], game["title"]), ("independent_model", "Кубики в коробку"))
        self.assertIn(MODEL, game["origin_reason"])
        self.assertEqual(self.reviews, [MODEL])  # reviewed on the same provider, not by Haiku
        self.assertIn("NO WEB SEARCH", self.calls[0])
        self.assertIn(KNOWN, self.calls[0])
        self.assertIn((MODEL, "pass", 0.00275), self.runs())
        level, title, _ = self.alerts[-1]
        self.assertEqual((level, title), ("warning", "Игра подготовлена независимой моделью (Workers AI)"))

    def test_an_invented_source_is_rejected_and_corrected(self):
        self.answers = [self.candidate(source_url="https://example.org/made-up"), self.candidate()]
        self.verdicts = [{"block": [], "fix": []}]
        self.assertIsNone(self.prepare([RuntimeError("usage limit")]))
        self.assertEqual(self.game()["origin"], "independent_model")
        self.assertEqual(len(self.calls), 2)
        self.assertIn("example.org/made-up", self.calls[1])  # the retry sees its rejected source

    def test_no_reviewer_answer_means_the_library_not_an_unreviewed_game(self):
        self.answers = [self.candidate()]
        self.verdicts = [RuntimeError("Workers AI down")]
        self.assertIsNone(self.prepare([RuntimeError("usage limit")]))
        game = self.game()
        self.assertEqual((game["origin"], game["title"]), ("library", "Прятки с платком"))
        self.assertIn("ревьюер", game["origin_reason"])

    def test_a_block_from_its_reviewer_repairs_the_same_game(self):
        self.answers = [self.candidate(materials=["Кубики", "Подушка на диване"]), self.candidate()]
        self.verdicts = [{"block": ["Ребёнок на диване без опоры"], "fix": []}, {"block": [], "fix": []}]
        self.assertIsNone(self.prepare([RuntimeError("usage limit")]))
        self.assertEqual(self.game()["origin"], "independent_model")
        self.assertIn("Keep the same activity", self.calls[1])

    def test_the_daily_call_cap_keeps_it_within_the_free_allocation(self):
        con = sqlite3.connect(self.db)
        con.executemany("INSERT INTO model_runs(target_date, attempt, outcome, model) VALUES (?, 1, 'pass', ?)",
                        [(TODAY, MODEL)] * 20)
        con.commit()
        con.close()
        self.assertIsNone(self.prepare([RuntimeError("usage limit")]))
        self.assertEqual(self.calls, [])
        game = self.game()
        self.assertEqual(game["origin"], "library")
        self.assertIn("дневной лимит вызовов (20 из 20)", game["origin_reason"])

    def test_off_without_the_model_or_the_token(self):
        for env in ({"PREPARATION_INDEPENDENT_MODEL": ""}, {"CF_AI_TOKEN": ""}):
            with self.subTest(env=env), mock.patch.dict("os.environ", env):
                self.assertFalse(workers_ai.enabled())


class ClaudeDown(IndependentSetup):
    """A hung or failing Claude costs one timeout per run, not one per child."""

    def second_child(self):
        from src.db import children as ch
        from tests.test_fallback import BORN
        con = ch.connect_db(self.db)
        child = ch.add_child(con, "Петя", BORN)
        ch.add_recipient(con, child, "telegram", "556")
        con.close()
        return child

    def test_a_timeout_skips_haiku_and_claude_for_the_rest_of_the_run(self):
        import subprocess
        self.second_child()
        self.answers = [self.candidate(), self.candidate(title="Мяч в корзину",
                                                         mechanics="Мяч в корзину: бросает мяч")]
        self.verdicts = [{"block": [], "fix": []}, {"block": [], "fix": []}]
        self.assertIsNone(self.prepare([subprocess.TimeoutExpired("claude", 180)]))
        self.assertEqual(self.models, ["primary"])  # not Haiku, and not again for the second child
        self.assertEqual(len(self.calls), 2)
        con = sqlite3.connect(self.db)
        origins = con.execute("SELECT origin, origin_reason FROM games WHERE game_date = ? ORDER BY id",
                              (TODAY,)).fetchall()
        con.close()
        self.assertEqual([o for o, _ in origins], ["independent_model"] * 2)
        self.assertIn("Claude не отвечает в этом прогоне", origins[1][1])

    def test_an_overloaded_api_is_an_outage_a_rejected_game_is_not(self):
        from src.preparation import chain, fallback
        self.assertTrue(fallback.is_outage(RuntimeError("Claude failed. stdout=API Error: 529 overloaded")))
        self.assertTrue(fallback.is_outage(RuntimeError("Claude AI usage limit reached")))
        self.assertFalse(fallback.is_outage(chain.Exhausted("налить 500 мл воды; timeout в игре")))
        self.assertFalse(fallback.is_outage(RuntimeError("Claude returned no structured_output object")))

    def test_rejected_games_still_go_to_haiku(self):
        bad = {**good(self.age), "duration_minutes": 30}
        self.assertIsNone(self.prepare([bad, bad, bad, good(self.age)]))
        self.assertEqual(self.models, ["primary"] * 3 + ["haiku"])
        self.assertEqual(self.game()["origin"], "fallback_model")


class Parse(unittest.TestCase):
    def test_object_string_and_choices(self):
        self.assertEqual(workers_ai.parse({"response": {"a": 1}}), {"a": 1})
        self.assertEqual(workers_ai.parse({"response": '```json\n{"a": 2}\n```'}), {"a": 2})
        self.assertEqual(workers_ai.parse({"choices": [{"message": {"content": json.dumps({"a": 3})}}]}), {"a": 3})
        with self.assertRaises(RuntimeError):
            workers_ai.parse({"response": "нет"})


if __name__ == "__main__":
    unittest.main()
