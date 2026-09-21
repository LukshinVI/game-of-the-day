"""Regression tests for the candidate validation and the attempt loop of game preparation.

Pure functions and mocked model calls only: nothing here touches a database, the
network or the notifier.
"""

import contextlib
import io
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
    return [e for e in gp.validate_candidate(candidate(**overrides), []) if "dangerous-term" in e]


class ValidCandidate(unittest.TestCase):
    def test_a_well_formed_candidate_passes(self):
        self.assertEqual(gp.validate_candidate(candidate(), []), [])

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
        errors = gp.validate_candidate(candidate(message_text=f"Игра\n\n[Pathways.org]({URL})"), [])
        self.assertIn("Текст для отправки не должен содержать Markdown-ссылки", errors)

    def test_the_plain_source_url_must_be_in_the_message(self):
        errors = gp.validate_candidate(candidate(message_text="Игра без ссылки"), [])
        self.assertIn("Текст для отправки должен содержать чистую ссылку source_url", errors)

    def test_duplicates_of_history_are_rejected(self):
        history = [{"title": "Толкни мяч", "mechanics": "другое"}]
        errors = gp.validate_candidate(candidate(), history)
        self.assertTrue(any(e.startswith("duplicate title") for e in errors))


class Prompt(unittest.TestCase):
    def test_the_selector_prompt_states_the_rules_the_validator_enforces(self):
        text = gp.PROMPT_PATH.read_text()
        self.assertIn("plain URL exactly equal to source_url", text)
        self.assertIn("never use Markdown link syntax", text)
        self.assertIn("put every prohibition in the safety field", text)


class AttemptLoop(unittest.TestCase):
    def run_select(self, candidates):
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(gp, "run_codex", side_effect=candidates) as codex, \
                mock.patch.object(gp, "send_failure_alert") as alert:
            try:
                result = gp.select_candidate([], "2026-09-21")
            except RuntimeError as exc:
                result = exc
        return result, codex, alert

    def test_a_valid_third_attempt_saves_the_day(self):
        markdown = candidate(message_text=f"Игра\n\n[Pathways.org]({URL})")
        hazard = candidate(steps=["Возьмите нож.", "Повторите."])
        result, codex, alert = self.run_select([markdown, hazard, candidate()])
        self.assertEqual(result[1], 3)
        self.assertEqual(codex.call_count, 3)
        alert.assert_not_called()

    def test_all_attempts_failing_raises_and_alerts_once(self):
        bad = candidate(message_text="без ссылки")
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
            return candidate(message_text="без ссылки") if len(prompts) == 1 else candidate()

        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(gp, "run_codex", side_effect=fake):
            gp.select_candidate([], "2026-09-21")
        self.assertNotIn("deterministic_validation_errors", prompts[0])
        self.assertIn("deterministic_validation_errors", prompts[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
