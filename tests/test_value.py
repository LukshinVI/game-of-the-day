"""Product value: retention cohorts (src/ops/cohorts.py) and «полезно ли?» (src/ops/pulse.py)."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp(prefix="value-import-")) / "import.db"))

from src.bot import product_bot as pb  # noqa: E402
from src.bot import vk_dialog as vd  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.feedback import storage, vk_inbound  # noqa: E402
from src.ops import cohorts, product_metrics, pulse, weekly_digest  # noqa: E402

SUNDAY_3 = date(2026, 11, 15)  # third-week Sunday
NOW = datetime(2026, 11, 15, 20, 0, tzinfo=pb.MOSCOW)
TG, VK = "555", "777"


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((ROOT / "src/db/schema.sql").read_text())
    return con


def games(con, child, first, days, channel="telegram", address=TG, start_id=None):
    for i in range(days):
        day = (first + timedelta(days=i)).isoformat()
        gid = con.execute("INSERT INTO games (game_date, title, child_id, skill, message_text) "
                          "VALUES (?, ?, ?, 'speech', 't')", (day, f"Игра {child}-{i}", child)).lastrowid
        con.execute("INSERT INTO deliveries (game_id, channel, recipient, status, external_message_id) "
                    "VALUES (?, ?, ?, 'sent', ?)", (gid, channel, address, f"m{gid}"))


class Cohorts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.con = make_db(Path(tmp.name) / "c.db")
        self.addCleanup(self.con.close)

    def test_retention_by_month_counts_departures_and_skips_young_cohorts_and_tests(self):
        con = self.con
        for cid, name in ((1, "A"), (2, "B"), (3, "C"), (4, "T")):
            con.execute("INSERT INTO children (id, name, birth_date, is_test) VALUES (?, ?, '2025-01-01', ?)",
                        (cid, name, int(cid == 4)))
        games(con, 1, date(2026, 9, 1), 75)     # stayed
        games(con, 2, date(2026, 9, 10), 10)    # left after 10 days (paused)
        games(con, 3, date(2026, 11, 10), 5)    # too young for any horizon
        games(con, 4, date(2026, 9, 1), 2)      # test child: never counted
        con.execute("INSERT INTO erasures (channel, address, status, children, first_game, last_game) "
                    "VALUES ('vk', 'sha256:x', 'purged', 1, '2026-09-05', '2026-10-20')")
        rows = cohorts.table(con, date(2026, 11, 15))
        self.assertEqual(rows["2026-09"]["n"], 3)
        self.assertEqual(rows["2026-09"][7], [3, 3])
        self.assertEqual(rows["2026-09"][30], [2, 3])
        self.assertEqual(rows["2026-09"][60], [1, 3])
        self.assertEqual(rows["2026-11"][7], [0, 0])
        text = cohorts.report(con, date(2026, 11, 15))
        self.assertIn("2026-09, детей 3: 100% (3/3) · 66% (2/3) · 33% (1/3)", text)
        self.assertIn("2026-11, детей 1: — · — · —", text)

    def test_talking_counts_families_who_answered_in_30_days(self):
        con = self.con
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'A', '2025-01-01'), (2, 'B', '2025-01-01')")
        games(con, 1, date(2026, 11, 1), 10)
        games(con, 2, date(2026, 11, 1), 10, address="556")
        gid = con.execute("SELECT id FROM games WHERE child_id = 1 LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO feedback (game_id, raw_text) VALUES (?, 'ок')", (gid,))
        self.assertEqual(cohorts.talking(con, date(2026, 11, 15)), (2, 1))


class Pulse(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "p.db"
        con = make_db(self.db)
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'Сева', '2025-05-01')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'telegram', ?)", (TG,))
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', ?)", (VK,))
        con.execute("INSERT INTO members (chat_id) VALUES (?)", (TG,))
        games(con, 1, SUNDAY_3 - timedelta(days=20), 21)
        con.commit()
        con.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot = pb.ProductBot(self.db, "1", "gotd_bot", now=lambda: NOW)
        self.dialog = vd.VkDialog(now=lambda: NOW)
        self.n, self.sent = 0, []

    def digest(self, day=SUNDAY_3, enabled=True):
        out = []
        with mock.patch.dict(os.environ, {pulse.ENABLED_ENV: "1" if enabled else "0"}):
            weekly_digest.run(today=day, db_path=self.db, sender=lambda c, a, t, k: out.append((c, t)))
        return out

    def tg(self, text):
        self.n += 1
        return self.bot.handle({"message": {"message_id": self.n, "text": text,
                                            "chat": {"id": int(TG), "type": "private"},
                                            "from": {"id": int(TG), "first_name": "Мама"}}})

    def q(self, sql):
        con = ch.connect_db(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def test_off_by_default_and_only_on_the_third_sunday(self):
        self.assertFalse(any(pulse.QUESTION in t for _, t in self.digest(enabled=False)))
        self.assertFalse(any(pulse.QUESTION in t for _, t in self.digest(day=date(2026, 11, 8))))

    def test_asked_once_a_month_per_recipient_never_with_the_star_request(self):
        with mock.patch.dict(os.environ, {weekly_digest.STAR_ENABLED_ENV: "1"}):
            sent = self.digest()
        self.assertEqual(len(sent), 2)
        for _, text in sent:
            self.assertIn(pulse.QUESTION, text)
            self.assertNotIn("звёздочку", text)
        self.assertEqual(self.q("SELECT count(*) FROM pulses")[0][0], 2)

    def test_a_digit_answers_a_word_stays_feedback(self):
        self.digest()
        self.assertIn("Спасибо", self.tg("4 — нравится, но хочется больше на улице"))
        row = self.q("SELECT score, comment FROM pulses WHERE channel = 'telegram'")[0]
        self.assertEqual(tuple(row), (4, "нравится, но хочется больше на улице"))
        self.assertIn("записал", self.tg("5 минут поиграли, понравилось"))  # "5 минут": a second answer is feedback
        note = self.q("SELECT text FROM user_events WHERE kind = 'pulse'")[0][0]
        self.assertIn("4/5", note)

    def test_a_low_score_without_words_asks_what_is_wrong(self):
        self.digest()
        self.assertIn("что не так", self.tg("2"))

    def test_vk_answers_too_and_without_a_question_a_digit_is_feedback(self):
        out = []
        vk_inbound.route("e0", VK, "5", lambda p, t, r: out.append(t), dialog=self.dialog)
        self.assertFalse(any("Спасибо" in t for t in out))  # no open question: not a score
        self.digest()
        vk_inbound.route("e1", VK, "5", lambda p, t, r: out.append(t), dialog=self.dialog)
        self.assertIn("Спасибо", out[-1])

    def test_a_time_is_not_a_score(self):
        self.digest()
        self.assertIsNone(pulse.SCORE.match("3.5 часа спал"))
        self.assertNotIn("Спасибо! 🙏 Это", self.tg("3:15 начали играть"))

    def test_metrics_show_the_average(self):
        self.digest()
        self.tg("5")
        text = product_metrics.report(self.db, today=SUNDAY_3)
        self.assertIn("Полезность (1–5, за 90 дн.): в среднем 5.0, ответили 1 из 2 (5×1)", text)
        self.assertIn("Удержание по когортам", text)


if __name__ == "__main__":
    unittest.main()
