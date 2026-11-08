"""Regression tests for the candidate validation and the attempt loop of game preparation.

Pure functions and mocked model calls only: nothing here touches a database, the
network or the notifier.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.preparation import game_preparation as gp  # noqa: E402

URL = "https://pathways.org/activities/kickin-back"


def candidate(**overrides):
    data = {
        "title": "Толкни мяч",
        "age_months": 12,
        "duration_minutes": 5,
        "goal": "Тренировать направленные движения ног.",
        "skill": "coordination",
        "mechanics": "Ребёнок лёжа на спине толкает большой мягкий мяч стопами",
        "materials": ["Большой мягкий мяч", "Нескользящий коврик"],
        "steps": ["Положите ребёнка на спину.", "Поднесите мяч к его стопам."],
        "safety": "Играйте под присмотром. Без магнитов, батареек и мелких деталей.",
        "source_name": "Pathways.org",
        "source_url": URL,
        "message_text": f"Толкни мяч\n\nВозраст: 12 месяцев.\n\nИсточник: {URL}",
    }
    data.update(overrides)
    return data


def hazard_errors(**overrides):
    return [e for e in gp.validate_candidate(candidate(**overrides), [], 12) if "dangerous-term" in e]


class ValidCandidate(unittest.TestCase):
    def test_a_well_formed_candidate_passes(self):
        self.assertEqual(gp.validate_candidate(candidate(), [], 12), [])

    def test_the_safety_field_may_name_forbidden_items(self):
        # The gate scans title, mechanics, materials and steps only, so prohibitions belong in safety.
        self.assertEqual(hazard_errors(safety="Не используйте магниты, монеты, бусины и ножи."), [])


class KnifeGate(unittest.TestCase):
    LEGS = ("ножка", "ножки", "ножку", "ножке", "ножкой", "ножкою", "ножек", "ножкам", "ножками", "ножках",
            "ножонки", "ноженьки", "подножка", "подножки")
    KNIVES = ("нож", "ножа", "ножу", "ножом", "ноже", "ножи", "ножей", "ножам", "ножик", "ножиком",
              "ножичек", "ножичком", "ножевой", "ножовка", "ножовкой", "ножницы", "ножницами", "ножны",
              "ножнами", "ножная")

    def test_child_leg_words_are_not_knives(self):
        for word in self.LEGS:
            with self.subTest(word=word):
                self.assertEqual(hazard_errors(steps=[f"Ребёнок двигает {word}.", "Повторите."]), [])

    def test_every_other_word_starting_with_nozh_is_rejected(self):
        for word in self.KNIVES:
            with self.subTest(word=word):
                errors = hazard_errors(steps=[f"Возьмите {word}.", "Повторите."])
                self.assertEqual(errors, ["dangerous-term gate triggered: нож"])

    def test_an_unknown_word_family_is_denied_by_default(self):
        self.assertTrue(hazard_errors(steps=["Возьмите ножеточку.", "Повторите."]))

    def test_one_legs_word_does_not_hide_a_knife_in_the_same_text(self):
        self.assertTrue(hazard_errors(steps=["Ножки ребёнка рядом, а рядом лежит нож.", "Повторите."]))

    def test_the_word_boundary_is_not_bypassed_by_case_or_punctuation(self):
        self.assertTrue(hazard_errors(steps=["Осторожно: НОЖ!", "Повторите."]))
        self.assertTrue(hazard_errors(materials=["Нож, вилка"]))


class OtherHazardsStayStrict(unittest.TestCase):
    def test_stems_are_rejected_in_every_scanned_field(self):
        for term in ("магнит", "монет", "бусин", "пуговиц", "батарей", "стекл", "игол", "кипят", "горяч", "лекарств", "химикат"):
            for field in ("title", "mechanics", "materials", "steps"):
                with self.subTest(term=term, field=field):
                    value = f"игра со словом {term}ами"
                    override = {field: [value, "ещё"] if field == "steps" else ([value] if field == "materials" else value)}
                    self.assertTrue(hazard_errors(**override), f"{term} in {field} must be rejected")

    def test_a_negation_inside_materials_is_still_rejected(self):
        # The rule is deliberately conservative; the prompt tells the model to keep such text in `safety`.
        self.assertTrue(hazard_errors(materials=["Лёгкая игрушка без мелких деталей, магнитов и батареек"]))


class FormattingRules(unittest.TestCase):
    def test_markdown_links_in_the_message_are_rejected(self):
        errors = gp.validate_candidate(candidate(message_text=f"Игра\n\n[Pathways.org]({URL})"), [], 12)
        self.assertIn("Текст для отправки не должен содержать Markdown-ссылки", errors)

    def test_the_plain_source_url_must_be_in_the_message(self):
        errors = gp.validate_candidate(candidate(message_text="Игра без ссылки"), [], 12)
        self.assertIn("Текст для отправки должен содержать чистую ссылку source_url", errors)

    def test_duplicates_of_history_are_rejected(self):
        history = [{"title": "Толкни мяч", "mechanics": "другое"}]
        errors = gp.validate_candidate(candidate(), history, 12)
        self.assertTrue(any(e.startswith("duplicate title") for e in errors))


class Prompt(unittest.TestCase):
    def test_the_selector_prompt_states_the_rules_the_validator_enforces(self):
        text = gp.PROMPT_PATH.read_text()
        self.assertIn("plain URL exactly equal to source_url", text)
        self.assertIn("never use Markdown link syntax", text)
        self.assertIn("put every prohibition in the safety field", text)


class AttemptLoop(unittest.TestCase):
    def setUp(self):
        # 12 completed months on the 2026-09-21 delivery date used below.
        env = mock.patch.dict(os.environ, {"CHILD_BIRTH_DATE": "2025-09-01"})
        env.start()
        self.addCleanup(env.stop)

    def run_select(self, candidates):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(gp, "run_model", side_effect=candidates) as codex, \
                mock.patch.object(gp, "send_failure_alert") as alert:
            try:
                result = gp.select_candidate([], "2026-09-21")
            except RuntimeError as exc:
                result = exc
        return result, codex, alert

    def test_a_valid_third_attempt_saves_the_day(self):
        # Message formatting can no longer fail (the layout is rendered by code), so
        # the rejected attempts use field-level problems instead.
        too_long = candidate(duration_minutes=30)
        hazard = candidate(steps=["Возьмите нож.", "Повторите."])
        result, codex, alert = self.run_select([too_long, hazard, candidate()])
        self.assertEqual(result[1], 3)
        self.assertEqual(codex.call_count, 3)
        alert.assert_not_called()

    def test_all_attempts_failing_raises_and_alerts_once(self):
        bad = candidate(duration_minutes=30)
        result, codex, alert = self.run_select([bad] * gp.MAX_ATTEMPTS)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(codex.call_count, gp.MAX_ATTEMPTS)
        alert.assert_called_once()
        self.assertIn(str(gp.MAX_ATTEMPTS), str(result))

    def test_a_good_first_attempt_calls_the_model_once(self):
        result, codex, _ = self.run_select([candidate()])
        self.assertEqual(result[1], 1)
        self.assertEqual(codex.call_count, 1)

    def test_a_retry_prompt_carries_the_rejection_reasons(self):
        prompts = []

        def fake(prompt):
            prompts.append(prompt)
            return candidate(duration_minutes=30) if len(prompts) == 1 else candidate()

        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(gp, "run_model", side_effect=fake):
            gp.select_candidate([], "2026-09-21")
        self.assertNotIn("deterministic_validation_errors", prompts[0])
        self.assertIn("deterministic_validation_errors", prompts[1])


class MessageLayout(unittest.TestCase):
    def test_the_message_uses_the_emoji_layout_and_a_plain_source_url(self):
        text = gp.render_message(candidate())
        lines = text.split("\n")
        self.assertTrue(lines[0].startswith("🎲 Игра дня: «"))
        for marker in ("⏱ ", "🎯 Цель: ", "🤲 Навык: ", "🧸 Нужно: ", "Как играть:", "✅ Безопасность: ", "Источник: "):
            self.assertIn(marker, text)
        self.assertEqual(lines[-1], URL)
        self.assertEqual(gp.validate_candidate({**candidate(), "message_text": text}, [], 12), [])

    def test_steps_are_numbered_and_skill_is_in_russian(self):
        text = gp.render_message(candidate(steps=["Первый.", "Второй."], skill="fine_motor"))
        self.assertIn("1. Первый.\n2. Второй.", text)
        self.assertIn("🤲 Навык: мелкая моторика.", text)

    def test_minutes_agree_with_the_number(self):
        self.assertEqual([gp.minutes_word(n) for n in (1, 2, 5, 7, 11, 21, 22)],
                         ["минута", "минуты", "минут", "минут", "минут", "минута", "минуты"])

    def test_the_model_message_is_replaced_but_a_broken_candidate_is_left_for_validation(self):
        rendered = gp.with_rendered_message(candidate(message_text="как угодно"))
        self.assertTrue(rendered["message_text"].startswith("🎲 Игра дня"))
        broken = {"title": "x"}
        self.assertIs(gp.with_rendered_message(broken), broken)


class ModelBackend(unittest.TestCase):
    def envelope(self, **overrides):
        data = {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": candidate()}
        data.update(overrides)
        return mock.Mock(returncode=0, stdout=json.dumps(data), stderr="")

    def run_claude(self, completed):
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(gp.subprocess, "run", return_value=completed) as run:
            return gp.run_claude("prompt"), run

    def test_claude_structured_output_is_the_candidate(self):
        result, run = self.run_claude(self.envelope())
        self.assertEqual(result, candidate())
        cmd = run.call_args.args[0]
        self.assertIn("--json-schema", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertIn("--strict-mcp-config", cmd)

    def test_claude_errors_are_failures_not_candidates(self):
        for completed in (
            self.envelope(is_error=True, subtype="error_max_turns"),
            self.envelope(structured_output=None),
            mock.Mock(returncode=0, stdout="not json", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="auth"),
        ):
            with self.subTest(completed=completed), self.assertRaises(RuntimeError):
                self.run_claude(completed)

    def test_backend_is_selected_by_environment(self):
        with mock.patch.object(gp, "run_claude", return_value="c") as claude, \
                mock.patch.object(gp, "run_codex", return_value="x") as codex:
            with mock.patch.dict(os.environ, {"PREPARATION_BACKEND": "codex"}):
                self.assertEqual(gp.run_model("p"), "x")
            with mock.patch.dict(os.environ, {"PREPARATION_BACKEND": "claude"}):
                self.assertEqual(gp.run_model("p"), "c")
            with mock.patch.dict(os.environ, {"PREPARATION_BACKEND": "gpt"}), self.assertRaises(ValueError):
                gp.run_model("p")
        claude.assert_called_once()
        codex.assert_called_once()


class ChildAge(unittest.TestCase):
    def test_age_counts_completed_months(self):
        self.assertEqual(gp.child_age_months("2026-09-21", "2025-09-15"), 12)
        self.assertEqual(gp.child_age_months("2026-09-14", "2025-09-15"), 11)
        self.assertEqual(gp.child_age_months("2026-11-08", "2025-09-15"), 13)

    def test_a_month_only_birth_date_means_the_first(self):
        self.assertEqual(gp.child_age_months("2026-09-01", "2025-09"), 12)
        self.assertEqual(gp.child_age_months("2026-08-31", "2025-09"), 11)

    def test_age_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"CHILD_BIRTH_DATE": "2025-09-01"}):
            self.assertEqual(gp.child_age_months("2027-03-01"), 18)

    def test_missing_or_invalid_birth_date_is_an_error_not_a_guess(self):
        for raw in ("", "September 2025", "2025-13-01"):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"CHILD_BIRTH_DATE": raw}):
                with self.assertRaises(ValueError):
                    gp.child_age_months("2026-09-21")

    def test_a_birth_date_after_the_delivery_date_is_rejected(self):
        with self.assertRaises(ValueError):
            gp.child_age_months("2025-01-01", "2025-09-01")

    def test_the_prompt_carries_the_computed_age(self):
        prompt = gp.build_prompt([], 14, today="2026-11-08")
        self.assertIn('"child_age_months": 14', prompt)
        self.assertNotIn("12-month", prompt)

    def test_a_candidate_for_another_age_is_rejected(self):
        errors = gp.validate_candidate(candidate(), [], 14)
        self.assertIn("age_months must be exactly 14", errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
