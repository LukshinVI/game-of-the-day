"""Album of the month: what it collects, when it is sent, and that it renders."""

import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import children as ch  # noqa: E402
from src.ops import monthly_album as album  # noqa: E402


class Album(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.close()
        con = ch.connect_db(self.db)
        self.child = ch.add_child(con, "Тёма", "2025-09-07")
        ch.add_recipient(con, self.child, "vk", "3000001")
        self.test = ch.add_child(con, "Тест", "2025-09-07", is_test=True)
        ch.add_recipient(con, self.test, "vk", "300000002")
        self.games = []
        start = date(2026, 11, 1)
        skills = ["speech", "fine_motor", "coordination"]
        for i in range(20):
            for child, peer in ((self.child, "3000001"), (self.test, "300000002")):
                game = con.execute(
                    "INSERT INTO games(legacy_id, game_date, title, skill, child_id) VALUES (?, ?, ?, ?, ?)",
                    (f"{child}-{i}", (start + timedelta(days=i)).isoformat(), f"Игра {i}", skills[i % 3], child)).lastrowid
                con.execute("INSERT INTO deliveries(game_id, channel, recipient, idempotency_key, status) "
                            "VALUES (?, 'vk', ?, ?, 'sent')", (game, peer, f"k{game}"))
                if child == self.child:
                    self.games.append(game)
        fb = con.execute("INSERT INTO feedback(game_id, channel, external_event_id, raw_text, received_at) "
                         "VALUES (?, 'vk', 'e1', 'Башенку построил сам! Очень понравилось', '2026-11-09T10:00:00')",
                         (self.games[4],)).lastrowid
        con.execute("INSERT INTO observations(feedback_id, child_id, game_id, kind, value, skill, evidence) "
                    "VALUES (?, ?, ?, 'skill', 'went_well', 'fine_motor', 'Башенку построил сам')",
                    (fb, self.child, self.games[4]))
        con.execute("INSERT INTO observations(feedback_id, child_id, game_id, kind, value, evidence) "
                    "VALUES (?, ?, ?, 'outcome', 'liked', 'Очень понравилось')", (fb, self.child, self.games[4]))
        con.commit()
        con.close()
        self.sent = []
        patcher = mock.patch.dict(os.environ, {"GAME_ALBUM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def con(self):
        return album.connect(self.db)

    def test_collects_facts_and_the_parents_words(self):
        con = self.con()
        child = con.execute("SELECT id, name, birth_date FROM children WHERE id = ?", (self.child,)).fetchone()
        data = album.collect(con, child, "2026-11")
        con.close()
        self.assertEqual(data["received"], 20)
        self.assertEqual(data["marked"], 1)
        self.assertEqual(data["age"], "1 год и 2 месяца")
        self.assertTrue(data["favourites_liked"])
        self.assertEqual([f["title"] for f in data["favourites"]], ["Игра 4"])
        self.assertEqual(data["quote"], {"text": "Башенку построил сам", "date": "9 ноября"})
        self.assertEqual({k for k, _ in data["areas"]}, {"speech", "fine_motor", "coordination"})

    def test_no_liked_game_shows_the_last_games_under_a_neutral_heading(self):
        con = self.con()
        con.execute("DELETE FROM observations")
        con.commit()
        child = con.execute("SELECT id, name, birth_date FROM children WHERE id = ?", (self.child,)).fetchone()
        data = album.collect(con, child, "2026-11")
        con.close()
        self.assertFalse(data["favourites_liked"])
        self.assertEqual([f["title"] for f in data["favourites"]], ["Игра 19", "Игра 18", "Игра 17"])
        self.assertIsNone(data["quote"])

    def test_a_short_month_gets_no_album(self):
        con = self.con()
        child = con.execute("SELECT id, name, birth_date FROM children WHERE id = ?", (self.child,)).fetchone()
        self.assertIsNone(album.collect(con, child, "2026-10"))
        con.execute("DELETE FROM deliveries WHERE game_id IN (SELECT id FROM games WHERE game_date > '2026-11-10')")
        self.assertIsNone(album.collect(con, child, "2026-11"))
        con.close()

    def run_album(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return album.run(today=date(2026, 12, 1), db_path=self.db, render_fn=lambda data: b"jpeg",
                             sender=lambda *args: self.sent.append(args), **kwargs)

    def test_sent_once_to_families_never_to_test_children(self):
        self.assertEqual(self.run_album(), 1)
        self.assertEqual(self.run_album(), 0)
        channel, address, picture, text, key = self.sent[0]
        self.assertEqual((channel, address, picture), ("vk", "3000001", b"jpeg"))
        self.assertIn("Альбом месяца: ноябрь — Тёма, 1 год и 2 месяца", text)
        self.assertEqual(key, f"album-2026-11-{self.child}-3000001")

    def test_off_without_the_flag(self):
        with mock.patch.dict(os.environ, {"GAME_ALBUM": ""}):
            self.assertEqual(self.run_album(), 0)
        self.assertEqual(self.sent, [])

    @unittest.skipUnless(album.FONT.is_file(), "font missing")
    def test_renders_a_jpeg(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")
        con = self.con()
        child = con.execute("SELECT id, name, birth_date FROM children WHERE id = ?", (self.child,)).fetchone()
        data = album.collect(con, child, "2026-11")
        con.close()
        picture = album.render(data)
        self.assertTrue(picture.startswith(b"\xff\xd8"))
        from PIL import Image
        width, height = Image.open(io.BytesIO(picture)).size
        self.assertEqual(width, 1080)
        self.assertLessEqual(height, album.H)


if __name__ == "__main__":
    unittest.main()
