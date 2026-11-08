"""The selection eval harness, offline: model calls are mocked."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("evals_run", ROOT / "evals/preparation/run_selection_evals.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

from src.preparation import game_preparation as gp  # noqa: E402


def candidate(age, skill="speech", **extra):
    data = {
        "title": "Чья это песенка?", "age_months": age, "duration_minutes": 5, "goal": "Слушать и повторять.",
        "skill": skill, "mechanics": "повторяет звук за взрослым", "materials": ["Игрушка"],
        "steps": ["Покажите игрушку.", "Издайте звук.", "Подождите ответа."],
        "safety": "Под присмотром взрослого.", "source_name": "CDC", "source_url": "https://www.cdc.gov/x",
    }
    data.update(extra)
    return data


class Harness(unittest.TestCase):
    def test_cases_file_is_valid_and_in_range(self):
        cases = json.loads(ev.CASES.read_text())["cases"]
        self.assertGreaterEqual(len(cases), 5)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for c in cases:
            self.assertTrue(1 <= c["age_months"] <= gp.MAX_AGE_MONTHS_SUPPORTED, c["id"])

    def test_a_case_records_gate_attempts_lint_and_cost(self):
        case = {"id": "x", "age_months": 6, "why": "w", "history": [
            {"title": f"G{i}", "skill": "fine_motor", "mechanics": f"m{i}"} for i in range(3)]}
        answers = [candidate(6, duration_minutes=30), candidate(6, skill="fine_motor"), candidate(6, goal="Слушать и повторять")]

        def fake(prompt):
            gp.LAST_RUN_META.update({"model": "m", "cost_usd": 0.5})
            return answers.pop(0)

        with mock.patch.object(gp, "run_claude", fake):
            record = ev.run_case(case)
        self.assertTrue(record["passed"])
        self.assertEqual(record["attempts"], 3)
        self.assertEqual(record["cost_usd"], 1.5)
        self.assertIn("goal is not a finished sentence", record["lint"])
        self.assertTrue(any("duration_minutes" in e for e in record["rejections"]))
        self.assertTrue(any("last 2 games" in e for e in record["rejections"]))
        text = ev.summary("m", None, [record])
        self.assertIn("passed the production gate: 1/1; on the first attempt: 0/1", text)

    def test_a_failing_case_is_a_result_not_a_crash(self):
        def broken(prompt):
            raise RuntimeError("Claude failed")
        with mock.patch.object(gp, "run_claude", broken):
            record = ev.run_case({"id": "y", "age_months": 9, "why": "w", "history": []})
        self.assertFalse(record["passed"])
        self.assertIn("Claude failed", record["error"])
