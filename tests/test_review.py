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


class Tiers(Reviewer):
    ENV = {"PREPARATION_REVIEW_MODEL": "haiku", "PREPARATION_REVIEW_TIERS": "1"}

    def select_tiered(self, answers, verdicts):
        prompts = []
        verdicts = iter(verdicts)

        def fake_model(prompt):
            prompts.append(prompt)
            return answers.pop(0)

        def fake_review(prompt, name, schema=None):
            self.assertIs(schema, review.TIERED_SCHEMA)
            block, fix = next(verdicts)
            return {"structured_output": {"block": block, "fix": fix}, "total_cost_usd": 0.001}

        with contextlib.redirect_stdout(io.StringIO()), mock.patch.dict(os.environ, self.ENV), \
                mock.patch.object(gp, "run_model", side_effect=fake_model), \
                mock.patch.object(review, "_run", fake_review), \
                mock.patch.object(gp, "send_failure_alert"):
            gp.RUN_LOG.clear()
            result = gp.select_candidate([], "2026-11-09", age_months=9)
        return result, prompts

    def test_a_fix_is_added_to_the_safety_notes_and_the_game_passes(self):
        # Production 2026-11-11: "blind cords" rejected a fine shadow game.
        fix = "Шнуры от штор уберите повыше, вне досягаемости ребёнка"
        (result, attempts), _ = self.select_tiered([candidate()], [([], [fix])])
        self.assertEqual(attempts, 1)
        self.assertIn("Под присмотром. " + fix + ".", result["safety"])
        self.assertIn(fix, result["message_text"])
        self.assertEqual([r["outcome"] for r in gp.RUN_LOG], ["pass", "pass"])
        self.assertIn("safety fix: " + fix, gp.RUN_LOG[-1]["errors"])

    def test_a_block_repairs_the_same_game(self):
        (result, attempts), prompts = self.select_tiered(
            [candidate(), candidate(materials=["Мягкая игрушка без пришитых деталей", "Платок"])],
            [(["Игрушка с пришитыми глазами-пуговицами: малыш может их оторвать"], []), ([], [])])
        self.assertEqual(attempts, 2)
        self.assertIn("Keep the same activity", prompts[1])
        self.assertIn("safety review: Игрушка", prompts[1])

    def test_a_fix_that_names_a_real_hazard_is_a_block(self):
        blocks, fixes = review.split([], ["Не давайте фонарик с батарейкой-таблеткой",
                                          "Тёплую воду проверьте, чтобы она не была горячей"])
        self.assertEqual((len(blocks), fixes), (2, []))

    def test_a_caution_explained_by_its_consequence_stays_a_fix(self):
        # Replay 2026-11-11: "срежьте ленточки, которые могут обмотаться вокруг шеи" blocked
        # a game that had passed before.
        fix = "Срежьте длинные ленточки, которые могут обмотаться вокруг шеи, и проверьте швы, чтобы малыш ничего не проглотил"
        self.assertEqual(review.split([], [fix]), ([], [fix]))

    def test_too_many_fixes_is_a_block(self):
        blocks, fixes = review.split([], ["Уберите скотч", "Проверьте швы", "Закрепите шнуры штор"])
        self.assertEqual((len(blocks), fixes), (3, []))
        self.assertEqual(review.split([], ["Уберите скотч", "Проверьте швы"]),
                         ([], ["Уберите скотч", "Проверьте швы"]))

    def test_without_the_flag_every_finding_rejects_as_before(self):
        self.assertFalse(review.tiers_enabled())
        prompt = gp.build_retry_prompt("P", candidate(), ["safety review: x"])
        self.assertNotIn("Keep the same activity", prompt)


class Summary(unittest.TestCase):
    def test_the_morning_summary_lists_blocks_fixes_and_library_days(self):
        import sqlite3
        import tempfile
        from src.db import children as ch
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            con = sqlite3.connect(db)
            con.executescript((root / "src/db/schema.sql").read_text())
            con.close()
            con = ch.connect_db(db)
            child = ch.add_child(con, "Всеволод", "2025-09-03")
            con.execute("INSERT INTO model_runs(at, target_date, child_id, attempt, outcome, model, errors_json, dry_run) "
                        "VALUES ('2026-11-11 04:05:15', '2026-11-11', ?, 1, 'rejected', 'review:haiku', ?, 0)",
                        (child, '["safety review: Батарейки в фонарике"]'))
            con.execute("INSERT INTO model_runs(at, target_date, child_id, attempt, outcome, model, input_tokens, "
                        "cache_read_tokens, output_tokens, cost_usd, dry_run) "
                        "VALUES ('2026-11-11 04:05:15', '2026-11-11', ?, 1, 'pass', 'sonnet', 1500, 20000, 3000, 0.07, 0)",
                        (child,))
            con.execute("INSERT INTO model_runs(at, target_date, child_id, attempt, outcome, model, errors_json, dry_run) "
                        "VALUES ('2026-11-11 04:05:15', '2026-11-11', ?, 2, 'pass', 'review:haiku', ?, 0)",
                        (child, '["safety fix: Снимите ленту после игры"]'))
            con.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id, origin) "
                        "VALUES ('x', '2026-11-11', 'T', 'sensory', ?, 'library')", (child,))
            con.commit()
            con.close()
            with mock.patch.object(gp, "DB_PATH", db):
                text = gp.review_summary("2026-11-11", "2026-11-11 04:00:00")
                self.assertIsNone(gp.review_summary("2026-11-11", "2026-11-11 05:00:00"))
        self.assertIn("Проверок: 2, отклонено: 1 (50%)", text)
        self.assertIn("⛔ Всеволод: Батарейки в фонарике", text)
        self.assertIn("✏️ Всеволод: Снимите ленту после игры", text)
        self.assertIn("📚 Из библиотеки: Всеволод", text)
        self.assertIn("💰 подготовка: 1 вызовов, токены 22k вход / 3k выход, $0.070", text)
        self.assertIn("💰 итого за прогон: $0.070", text)
