"""The LoRA reviewer experiment's data tools, offline: model calls are mocked."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "lora-reviewer"))

import common  # noqa: E402
import generate  # noqa: E402
import label  # noqa: E402


def activity(title="Мягкий мячик", materials=("Мячик",)):
    return {"title": title, "materials": list(materials), "steps": ["Покажите."], "safety": "Рядом.",
            "duration_minutes": 5}


class Data(unittest.TestCase):
    def test_eval_cases_never_leak_into_training(self):
        evals = common.eval_activities()
        self.assertTrue(all(common.leaks(case, evals) for case in evals))
        self.assertFalse(common.leaks(generate.EXAMPLE, evals))

    def test_specs_are_numbered_and_carry_their_risk(self):
        plan = generate.specs(50, random.Random(3))
        self.assertEqual([s["n"] for s in plan], list(range(1, 51)))
        for s in plan:
            self.assertIn(s["mode"], generate.MODES)
            self.assertEqual("hazard" in s, s["mode"] == "overt")

    def test_an_age_problem_is_only_drawn_for_ages_where_it_is_one(self):
        ranges = {p: (lo, hi) for p, lo, hi in generate.SUBTLE}
        for s in generate.specs(400, random.Random(5)):
            if s["mode"] == "subtle":
                lo, hi = ranges[s["problem"]]
                self.assertTrue(lo <= s["age_months"] <= hi, s)

    def test_activities_are_matched_to_specs_by_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "a.jsonl"
            # The model skipped spec 1 and returned spec 2 first: no shift between them.
            answer = {"activities": [{"n": 3, **activity("Третья")}, {"n": 2, **activity("Вторая")}]}
            with mock.patch.object(common, "call", return_value=(answer, 0.01)), \
                    mock.patch.object(sys, "argv", ["generate", "--n", "3", "--batch", "3", "--out", str(out)]), \
                    mock.patch("builtins.print"):
                generate.main()
            rows = common.read_jsonl(out)
            plan = generate.specs(3, random.Random(1))
            self.assertEqual([r["activity"]["title"] for r in rows], ["Вторая", "Третья"])
            self.assertEqual([r["intended"] for r in rows], [plan[1]["mode"], plan[2]["mode"]])

    def test_labels_keep_the_raw_split_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "a.jsonl", Path(tmp) / "l.jsonl"
            for i in range(3):
                common.append_jsonl(src, {"id": f"x{i}", "age_months": 6, "intended": "safe", "activity": activity()})
            # A caution naming batteries: the model said fix, the production rules say block.
            answer = {"block": [], "fix": ["Уберите батарейки подальше."]}
            argv = ["label", "--limit", "2", "--src", str(src), "--out", str(out)]
            with mock.patch.object(common, "call", return_value=(answer, 0.01)) as call, \
                    mock.patch.object(sys, "argv", argv), mock.patch("builtins.print"):
                label.main()
                label.main()
            rows = common.read_jsonl(out)
            self.assertEqual([r["id"] for r in rows], ["x0", "x1", "x2"])
            self.assertEqual(call.call_count, 3)
            self.assertEqual(rows[0]["label"]["fix"], ["Уберите батарейки подальше."])
            self.assertEqual(rows[0]["label"]["verdict"], "block")
            self.assertEqual(rows[0]["label"]["by_rules"], ["Уберите батарейки подальше."])
            self.assertIn("ACTIVITY (JSON)", call.call_args[0][0])
            self.assertEqual(json.loads(json.dumps(rows[0]))["teacher"], "sonnet")


if __name__ == "__main__":
    unittest.main()
