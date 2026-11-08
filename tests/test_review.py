"""The safety reviewer: off by default, a serious finding rejects like a gate error and
reaches the retry prompt, a reviewer failure never blocks the day."""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.preparation import game_preparation as gp  # noqa: E402
from src.preparation import review  # noqa: E402


def candidate(**extra):
    data = {
        "title": "Найди игрушку", "age_months": 9, "duration_minutes": 5, "goal": "Искать.", "skill": "cognitive",
        "mechanics": "ищет игрушку под платком", "materials": ["Мягкая игрушка", "Платок"],
        "steps": ["Покажите игрушку.", "Накройте платком."], "safety": "Под присмотром.",
        "source_name": "CDC", "source_url": "https://www.cdc.gov/x",
    }
    data.update(extra)
    return data


class Reviewer(unittest.TestCase):
    def select(self, answers, reviews, env):
        prompts = []

        def fake_model(prompt):
            prompts.append(prompt)
            return answers.pop(0)

        verdicts = iter(reviews)

        def fake_review(prompt, name):
            item = next(verdicts)
            if isinstance(item, Exception):
                raise item
            return {"structured_output": {"serious_problems": item}, "total_cost_usd": 0.001}

        with contextlib.redirect_stdout(io.StringIO()), mock.patch.dict(os.environ, env), \
                mock.patch.object(gp, "run_model", side_effect=fake_model), \
                mock.patch.object(review, "_run", fake_review), \
                mock.patch.object(gp, "send_failure_alert"):
            gp.RUN_LOG.clear()
            result = gp.select_candidate([], "2026-11-09", age_months=9)
        return result, prompts

    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {"PREPARATION_REVIEW_MODEL": ""}):
            self.assertEqual(review.review(candidate(), 9), ([], None))

    def test_a_serious_finding_triggers_a_corrective_retry_with_the_reason(self):
        (result, attempts), prompts = self.select(
            [candidate(), candidate(materials=["Мягкая игрушка с вышитыми глазами", "Платок"])],
            [["Уточните, что у игрушки нет пришитых пуговиц-глаз."], []],
            {"PREPARATION_REVIEW_MODEL": "haiku"})
        self.assertEqual(attempts, 2)
        self.assertIn("safety review: Уточните", prompts[1])
        self.assertIn("review:haiku", {r.get("model") for r in gp.RUN_LOG})

    def test_a_reviewer_outage_does_not_block(self):
        (result, attempts), _ = self.select([candidate()], [RuntimeError("down")],
                                            {"PREPARATION_REVIEW_MODEL": "haiku"})
        self.assertEqual(attempts, 1)
        self.assertEqual([r["outcome"] for r in gp.RUN_LOG], ["pass", "error"])
