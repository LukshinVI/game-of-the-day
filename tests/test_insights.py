"""Feedback insights: grouping, caps, verbatim-evidence validation, profile, reply, prompt."""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import children as ch  # noqa: E402
from src.feedback import insights, profile  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


class InsightsSetup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        con = ch.connect_db(self.db)
        self.child = ch.add_child(con, "Всеволод", "2025-09-03")
        self.game = con.execute(
            "INSERT INTO games(legacy_id, game_date, age_months, title, skill, mechanics) "
            "VALUES ('1', '2026-11-09', 14, 'Башенка', 'fine_motor', 'ставит кубик на кубик')").lastrowid
        con.execute("INSERT INTO deliveries(game_id, channel, recipient, status, idempotency_key) "
                    "VALUES (?, 'vk', '2488584', 'sent', 'k')", (self.game,))
        con.commit()
        con.close()
        self.prompts, self.replies = [], []

    def feedback(self, text, minutes_ago, channel="vk", event=None, game=None):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO feedback(game_id, channel, external_event_id, raw_text, received_at) VALUES (?, ?, ?, ?, ?)",
                    (game or self.game, channel, event or f"e{text[:5]}{minutes_ago}", text,
                     (NOW - timedelta(minutes=minutes_ago)).isoformat()))
        con.commit()
        con.close()

    def model(self, observations):
        def run(prompt):
            self.prompts.append(prompt)
            return {"observations": observations}
        return run

    def run_insights(self, observations, now=NOW):
        return insights.run(self.db, now=now, model=self.model(observations),
                            sender=lambda con, group, text: self.replies.append(text))

    def rows(self, sql):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()


class Insights(InsightsSetup):
    def test_close_messages_are_merged_and_a_fresh_one_waits(self):
        self.feedback("Башенку построил сам!", 30)
        self.feedback("Даже слишком легко было", 25)
        self.feedback("Новое сообщение", 2)  # still settling
        obs = [{"kind": "difficulty", "value": "too_easy", "skill": "fine_motor", "evidence": "слишком легко"},
               {"kind": "skill", "value": "went_well", "skill": "fine_motor", "evidence": "Башенку построил сам"}]
        self.assertEqual(self.run_insights(obs), 1)
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("Башенку построил сам!\\nДаже слишком легко было", self.prompts[0])
        self.assertEqual(self.rows("SELECT count(*) FROM feedback WHERE insights_at IS NULL"), [(1,)])
        self.assertIn("мелкая моторика: было легко → дальше дадим посложнее", self.replies[0])

    def test_quotes_must_be_verbatim_and_fields_valid(self):
        self.feedback("Не понравилось, плакал", 30)
        obs = [{"kind": "outcome", "value": "disliked", "skill": None, "evidence": "Не понравилось"},
               {"kind": "dislike", "value": "громкие звуки", "skill": None, "evidence": "боится громких звуков"},
               {"kind": "difficulty", "value": "impossible", "skill": "fine_motor", "evidence": "плакал"}]
        self.run_insights(obs)
        self.assertEqual(self.rows("SELECT kind, value FROM observations"), [("outcome", "disliked")])

    def test_daily_cap_per_child(self):
        for i in range(5):
            game = sqlite3.connect(self.db)
            gid = game.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, 'speech', ?)",
                               (f"g{i}", f"2026-11-0{i + 1}", f"G{i}", self.child)).lastrowid
            game.commit()
            game.close()
            self.feedback(f"Отзыв номер {i}", 60 - i, game=gid)
        self.run_insights([])
        self.assertEqual(len(self.prompts), insights.PER_CHILD_PER_DAY)
        self.assertEqual(self.rows("SELECT count(*) FROM feedback WHERE insights_at IS NULL"), [(2,)])

    def test_the_profile_drives_difficulty_and_dislikes(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET child_id = ?", (self.child,))
        con.commit()
        con.close()
        self.feedback("Слишком легко, сделал за минуту. Не любит лежать на животе", 30)
        obs = [{"kind": "difficulty", "value": "too_easy", "skill": "fine_motor", "evidence": "Слишком легко"},
               {"kind": "dislike", "value": "лежать на животе", "skill": None, "evidence": "Не любит лежать на животе"}]
        self.run_insights(obs)
        con = ch.connect_db(self.db)
        built = profile.load(con, self.child)
        con.close()
        self.assertEqual(built["next_difficulty"], {"fine_motor": "harder"})
        self.assertEqual(built["dislikes"], ["лежать на животе"])

    def test_profile_decays_and_balances(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        def obs(value, days):
            return {"kind": "difficulty", "value": value, "skill": "speech", "evidence": "x",
                    "created_at": (now - timedelta(days=days)).isoformat()}
        self.assertEqual(profile.build([obs("too_easy", 0), obs("too_hard", 0)], now), {})
        self.assertEqual(profile.build([obs("too_easy", 1)], now), {"next_difficulty": {"speech": "harder"}})
        self.assertEqual(profile.build([obs("too_easy", 30)], now), {})   # old signal fades below the threshold
        self.assertEqual(profile.build([obs("too_easy", 90)], now), {})   # outside the window

    def test_the_selection_prompt_carries_the_profile(self):
        from src.preparation import game_preparation as gp
        gp.PROFILE_CONTEXT = {"next_difficulty": {"fine_motor": "harder"}}
        try:
            context = json.loads(gp.build_prompt([], 14, today="2026-11-09").split("INPUT CONTEXT:\n", 1)[1])
        finally:
            gp.PROFILE_CONTEXT = {}
        self.assertEqual(context["child_profile"]["next_difficulty"], {"fine_motor": "harder"})
        context = json.loads(gp.build_prompt([], 14, today="2026-11-09").split("INPUT CONTEXT:\n", 1)[1])
        self.assertNotIn("child_profile", context)

    def test_a_model_failure_leaves_feedback_pending(self):
        self.feedback("Понравилось", 30)

        def broken(prompt):
            raise RuntimeError("down")
        insights.run(self.db, now=NOW, model=broken, sender=lambda *a: None)
        self.assertEqual(self.rows("SELECT count(*) FROM feedback WHERE insights_at IS NULL"), [(1,)])

    def test_old_feedback_is_analysed_without_a_reply_and_ages_from_when_it_was_written(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET child_id = ?", (self.child,))
        con.commit()
        con.close()
        self.feedback("Понравилось очень", 60 * 24 * 90)
        self.run_insights([{"kind": "outcome", "value": "liked", "skill": None, "evidence": "Понравилось"}])
        self.assertEqual(self.rows("SELECT count(*) FROM observations"), [(1,)])
        self.assertEqual(self.replies, [])
        con = ch.connect_db(self.db)
        self.assertEqual(profile.load(con, self.child), {})  # written 90 days ago: outside the window
        con.close()

    def test_the_reply_with_nothing_extracted_is_still_polite(self):
        self.assertIn("Сохранили его", insights.reply_text("Башенка", []))


class ReactionsInVk(InsightsSetup):
    def test_a_vk_sticker_gets_one_nudge_and_no_model_call(self):
        self.feedback("[стикер]", 30, event="s1")
        self.feedback("[стикер]", 5 * 60, event="s0")
        self.run_insights([{"kind": "outcome", "value": "liked", "skill": None, "evidence": "x"}])
        self.assertEqual(self.prompts, [])
        self.assertEqual(len(self.replies), 1)
        self.assertIn("пару слов текстом", self.replies[0])
        self.assertEqual(self.rows("SELECT DISTINCT insights_status FROM feedback"), [("reaction",)])

    def test_text_with_a_sticker_is_analysed_without_the_label(self):
        self.feedback("Понравилось [стикер]", 30)
        self.run_insights([{"kind": "outcome", "value": "liked", "skill": None, "evidence": "Понравилось"}])
        self.assertNotIn("[стикер]", self.prompts[0].split("INPUT:", 1)[1])
        self.assertEqual(self.rows("SELECT kind FROM observations"), [("outcome",)])
