"""Mechanic tags: the same idea under a new name is rejected, by the gate and by the library."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.preparation import fallback, mechanics  # noqa: E402
from src.preparation import game_preparation as gp  # noqa: E402


class Tags(unittest.TestCase):
    def test_a_recent_tag_is_rejected_an_old_one_is_fine(self):
        history = [{"mechanic_tag": "ball_throw_roll"}] + [{"mechanic_tag": "naming_pointing"}] * 7
        self.assertEqual(mechanics.violations("ball_throw_roll", history), [])  # 8 games ago
        self.assertIn("last 7 games", mechanics.violations("naming_pointing", history)[0])
        self.assertEqual(mechanics.violations("juggling", []), ["invalid mechanic_tag: juggling"])
        self.assertEqual(mechanics.violations(None, history), [])

    def test_the_prompt_offers_only_fresh_tags_and_the_schema_requires_one(self):
        prompt = gp.build_prompt([{"title": "Мяч", "skill": "coordination", "mechanic_tag": "ball_throw_roll"}], 14,
                                 today="2026-11-09")
        context = json.loads(prompt.split("INPUT CONTEXT:\n", 1)[1])
        self.assertNotIn("ball_throw_roll", context["mechanic_tags"])
        self.assertEqual(context["recent_mechanic_tags"], ["ball_throw_roll"])
        schema = json.loads(gp.SCHEMA_PATH.read_text())
        self.assertIn("mechanic_tag", schema["required"])
        self.assertEqual(set(schema["properties"]["mechanic_tag"]["enum"]), set(mechanics.TAGS))

    def test_the_library_skips_a_recently_used_mechanic(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        for title, tag in (("Мяч в корзину", "ball_throw_roll"), ("Ку-ку", "hide_and_find")):
            con.execute("INSERT INTO games(legacy_id, game_date, age_months, title, skill, mechanics, source_url, "
                        "message_text, mechanic_tag) VALUES (?, '2026-10-01', 14, ?, 'coordination', ?, 'https://x', 't', ?)",
                        (title, title, title, tag))
        history = [{"title": "Где мячик? В корзине!", "skill": "speech", "mechanic_tag": "ball_throw_roll"}]
        self.assertEqual(fallback.library_candidate(con, history, 14, "2026-11-09")["title"], "Ку-ку")
