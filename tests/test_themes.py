"""Seasonal themes: offered sometimes, capped per week, gated for safety."""
import json
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.preparation import themes  # noqa: E402


class Themes(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript((ROOT / "src/db/schema.sql").read_text())
        self.con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'А', '2025-01-01')")
        self.addCleanup(self.con.close)

    def themed_game(self, day, theme="autumn"):
        self.con.execute("INSERT INTO games (game_date, title, child_id, candidate_json) VALUES (?, 'И', 1, ?)",
                         (day, json.dumps({"theme": theme})))

    def offered_day(self, after=date(2026, 11, 1)):
        day = after
        while not themes.offered_today(day, 1):
            day += timedelta(days=1)
        return day

    def test_the_calendar_and_new_year_wins(self):
        self.assertEqual(themes.current(date(2026, 11, 11)), "autumn")
        self.assertEqual(themes.current(date(2026, 12, 31)), "new_year")
        self.assertEqual(themes.current(date(2027, 1, 10)), "new_year")
        self.assertEqual(themes.current(date(2027, 1, 11)), "winter")
        self.assertEqual(themes.current(date(2027, 6, 15)), "summer")

    def test_offered_on_some_days_not_every_day(self):
        days = [date(2026, 11, 1) + timedelta(days=i) for i in range(90)]
        share = sum(themes.offered_today(d, 1) for d in days) / len(days)
        self.assertTrue(0.15 < share < 0.5, share)

    def test_at_most_two_themed_games_a_week(self):
        day = self.offered_day()
        self.assertIsNotNone(themes.context(self.con, 1, day, 14))
        self.themed_game((day - timedelta(days=1)).isoformat())
        self.themed_game((day - timedelta(days=3)).isoformat())
        self.assertIsNone(themes.context(self.con, 1, day, 14))

    def test_young_children_get_words_only(self):
        day = self.offered_day()
        self.assertIn("bring in no new objects", themes.context(self.con, 1, day, 4)["rules"])
        self.assertNotIn("bring in no new objects", themes.context(self.con, 1, day, 14)["rules"])

    def test_gate_blocks_decorations_but_not_lookalike_words(self):
        game = {"theme": "new_year", "title": "Украсим ёлку", "materials": ["Мишура"], "steps": ["а", "б"]}
        self.assertEqual(themes.violations(game), ["theme gate triggered: мишур"])
        winter = {"theme": "winter", "materials": ["Варежка"], "steps": ["Следите за малышом", "Покажите следы"]}
        self.assertEqual(themes.violations(winter), [])
        self.assertTrue(themes.violations({**winter, "steps": ["Положите кубик льда в миску"]}))
        self.assertEqual(themes.violations({"theme": None, "materials": ["Мишура"]}), [])
        self.assertEqual(themes.violations({"theme": "mars"}), ["unknown theme: mars"])

    def test_prompt_lists_plain_words_to_avoid(self):
        day = self.offered_day(date(2027, 1, 12))
        self.assertIn("лед", themes.context(self.con, 1, day, 14)["never_use"])


if __name__ == "__main__":
    unittest.main()
