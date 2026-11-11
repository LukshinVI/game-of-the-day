"""Sunday digest: content from the week's games and feedback, next-week ideas, idempotency."""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import children as ch  # noqa: E402
from src.ops import weekly_digest as wd  # noqa: E402


class Digest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        con = ch.connect_db(self.db)
        self.child = ch.add_child(con, "Маша", "2025-09-03")
        ch.add_recipient(con, self.child, "telegram", "42")
        test = ch.add_child(con, "Тест", "2025-09-03", is_test=True)
        ch.add_recipient(con, test, "vk", "1")
        for day, title, skill in (("2026-11-09", "Башенка", "fine_motor"), ("2026-11-10", "Ку-ку", "speech"),
                                  ("2026-11-11", "Мяч", "fine_motor")):
            gid = con.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, ?, ?)",
                              (title, day, title, skill, self.child)).lastrowid
            con.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, ?, ?)",
                        (title + "t", day, title, skill, test))
            if title == "Башенка":
                con.execute("INSERT INTO feedback(id, game_id, raw_text) VALUES (1, ?, 'x')", (gid,))
                con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, evidence) "
                            "VALUES (1, ?, 'outcome', 'liked', 'понравилось')", (gid,))
                con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, skill, evidence) "
                            "VALUES (1, ?, 'skill', 'went_well', 'fine_motor', 'строил сам')", (gid,))
        con.commit()
        con.close()
        self.sent = []

    def run_digest(self, day):
        return wd.run(day, db_path=self.db, sender=lambda ch_, addr, text, key: self.sent.append((ch_, addr, text)))

    def test_content_and_once_per_week(self):
        self.assertEqual(self.run_digest(date(2026, 11, 15)), 1)   # the test child gets nothing
        channel, address, text = self.sent[0]
        self.assertEqual((channel, address), ("telegram", "42"))
        self.assertIn("Итоги недели — Маша: 3 игры", text)
        self.assertIn("мелкая моторика ×2", text)
        self.assertIn("❤️ Понравилось: «Башенка»", text)
        self.assertIn("🌱 Получалось: строил сам", text)
        self.assertIn("✨ Что попробовать на следующей неделе:", text)
        self.assertIn("• Повторите «Башенка»", text)
        ideas = text.split("✨ Что попробовать на следующей неделе:")[1].split("\n\n")[0]
        self.assertEqual(ideas.count("• "), wd.MAX_IDEAS)
        self.assertNotIn("мелкая моторика:", ideas)   # played twice this week, not suggested
        self.assertNotIn("чего не хватает", text)
        self.assertEqual(self.run_digest(date(2026, 11, 15)), 0)   # idempotent

    def test_the_first_sunday_of_a_month_asks_what_is_missing(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET game_date = date('2026-12-06', '-' || (id % 3) || ' days')")
        con.commit()
        con.close()
        self.run_digest(date(2026, 12, 6))
        self.assertIn("чего не хватает", self.sent[0][2])

    def test_no_age_norms_anywhere(self):
        """Parents must never read what their child "should" do by an age."""
        banned = ("обычно", "должен", "должна", "норм", "к 1", "к 2", "к 3", "мес.", "отста", "CDC")
        for skill, bands in wd.IDEAS.items():
            for _, idea in bands:
                for word in banned:
                    self.assertNotIn(word, idea, f"{skill}: {idea}")
        for age in range(0, 37):
            games = [{"skill": "speech"}] * 3
            for idea in wd.next_week_ideas(age, games, [], date(2026, 11, 15)):
                for word in banned:
                    self.assertNotIn(word, idea)

    def test_ideas_follow_the_age(self):
        today = date(2026, 11, 15)
        games = [{"skill": "speech"}] * 3
        for age in range(0, 37):
            ideas = wd.next_week_ideas(age, games, [], today)
            self.assertEqual(len(ideas), wd.MAX_IDEAS, age)
            for idea in ideas:
                label = idea.split(":")[0]
                key = next(k for k, v in wd.skills.LABELS.items() if v == label)
                self.assertIn(key, wd.skills.for_age(age), (age, idea))
        self.assertIsNone(wd.idea_for("imagination", 12))
        self.assertIsNone(wd.idea_for("fine_motor", 2))

    def test_ideas_rotate_between_weeks(self):
        games = [{"skill": "speech"}] * 3
        weeks = {tuple(wd.next_week_ideas(20, games, [], date(2026, 11, 15) + timedelta(weeks=w)))
                 for w in range(4)}
        self.assertGreater(len(weeks), 1)

    def test_too_few_games_no_digest(self):
        self.assertEqual(self.run_digest(date(2027, 1, 10)), 0)
        con = sqlite3.connect(self.db)
        con.execute("UPDATE games SET game_date = '2026-10-01' WHERE title = 'Мяч'")
        con.commit()
        con.close()
        self.assertEqual(self.run_digest(date(2026, 11, 15)), 0)  # two games are not a week


class StarRequest(unittest.TestCase):
    """The one-time GitHub star request: after a month, only when feedback is positive."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {wd.STAR_ENABLED_ENV: "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        con = ch.connect_db(self.db)
        self.child = ch.add_child(con, "Маша", "2025-09-03")
        ch.add_recipient(con, self.child, "telegram", "42")
        con.commit()
        con.close()
        self.sent = []

    def game(self, day, outcome=None, delivered=True):
        con = ch.connect_db(self.db)
        gid = con.execute("INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, ?, ?)",
                          (day, day, "Игра " + day, "speech", self.child)).lastrowid
        if delivered:
            con.execute("INSERT INTO deliveries(game_id, channel, recipient, status, idempotency_key, sent_at) "
                        "VALUES (?, 'telegram', '42', 'sent', ?, ?)", (gid, "k" + day, day + "T10:00:00+03:00"))
        if outcome:
            con.execute("INSERT INTO feedback(game_id, raw_text) VALUES (?, 'x')", (gid,))
            fid = con.execute("SELECT max(id) FROM feedback").fetchone()[0]
            con.execute("INSERT INTO observations(feedback_id, game_id, kind, value, evidence) "
                        "VALUES (?, ?, 'outcome', ?, 'x')", (fid, gid, outcome))
        con.commit()
        con.close()

    def week(self, outcome="liked"):
        for i, day in enumerate(("2026-12-09", "2026-12-10", "2026-12-11")):
            self.game(day, outcome if i == 0 else None)

    def run_digest(self, day):
        wd.run(day, db_path=self.db, sender=lambda ch_, addr, text, key: self.sent.append(text))
        return self.sent[-1] if self.sent else ""

    def test_asked_once_after_a_month_of_positive_feedback(self):
        self.game("2026-11-08")
        self.week()
        self.assertIn("github.com/LukshinVI/game-of-the-day", self.run_digest(date(2026, 12, 13)))
        self.game("2026-12-16", "liked")
        self.game("2026-12-17")
        self.game("2026-12-18")
        self.assertNotIn("github.com", self.run_digest(date(2026, 12, 20)))  # never twice

    def test_off_by_default(self):
        self.game("2026-11-08")
        self.week()
        with mock.patch.dict(os.environ, {wd.STAR_ENABLED_ENV: ""}):
            self.assertNotIn("github.com", self.run_digest(date(2026, 12, 13)))

    def test_not_before_a_month(self):
        self.game("2026-11-20")
        self.week()
        self.assertNotIn("github.com", self.run_digest(date(2026, 12, 13)))

    def test_not_without_positive_feedback(self):
        self.game("2026-11-08")
        self.week(outcome=None)
        self.assertNotIn("github.com", self.run_digest(date(2026, 12, 13)))

    def test_not_when_dislikes_outweigh(self):
        self.game("2026-11-08")
        self.week()
        self.game("2026-12-12", "disliked")
        self.game("2026-12-13", "disliked")
        self.assertNotIn("github.com", self.run_digest(date(2026, 12, 13)))

    def test_not_in_the_digest_with_the_monthly_question(self):
        self.game("2026-10-01")
        for day in ("2026-12-01", "2026-12-02", "2026-12-03"):
            self.game(day, "liked")
        text = self.run_digest(date(2026, 12, 6))
        self.assertIn("чего не хватает", text)
        self.assertNotIn("github.com", text)

    def test_a_failed_send_does_not_use_up_the_request(self):
        self.game("2026-11-08")
        self.week()

        def failing(*_):
            raise RuntimeError("down")

        wd.run(date(2026, 12, 13), db_path=self.db, sender=failing)
        self.assertIn("github.com", self.run_digest(date(2026, 12, 13)))


class News(Digest):
    def news(self, items):
        path = Path(self.db).parent / "news.json"
        import json
        path.write_text(json.dumps(items, ensure_ascii=False))
        return mock.patch.object(wd, "NEWS_PATH", path)

    def test_news_of_the_week_ride_on_the_digest(self):
        items = [{"date": "2026-11-11", "text": "игры станут короче, если малыш отвлекается"},
                 {"date": "2026-11-01", "text": "старая новость"},
                 {"date": "bad", "text": "битая запись"}]
        with self.news(items), mock.patch.dict(os.environ, {"GAME_NEWS_LINK": "https://vk.com/wall-1_2"}):
            self.run_digest(date(2026, 11, 15))
        text = self.sent[0][2]
        self.assertIn("🆕 Что нового:\n• Игры станут короче, если малыш отвлекается", text)
        self.assertIn("Подробнее — в нашей группе: https://vk.com/wall-1_2", text)
        self.assertNotIn("старая новость", text)

    def test_no_news_no_section_and_a_broken_file_is_ignored(self):
        with self.news([]):
            self.run_digest(date(2026, 11, 15))
        self.assertNotIn("Что нового", self.sent[0][2])
        with mock.patch.object(wd, "NEWS_PATH", Path("/nonexistent/news.json")):
            self.assertEqual(wd.news_for(date(2026, 11, 15)), [])

    def test_the_shipped_news_file_is_valid(self):
        import json
        for item in json.loads(wd.NEWS_PATH.read_text(encoding="utf-8")):
            date.fromisoformat(item["date"])
            self.assertTrue(item["text"].strip())
            self.assertLessEqual(len(item["text"]), 200)
