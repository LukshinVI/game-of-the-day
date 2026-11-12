"""«Удалить наши данные»: archive with a way back, an optional exit survey, purge; and the
nightly retention terms (src/ops/retention.py)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp(prefix="erasure-import-")) / "import.db"))

from src.bot import erasure  # noqa: E402
from src.bot import product_bot as pb  # noqa: E402
from src.bot import vk_dialog as vd  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.feedback import storage, vk_inbound  # noqa: E402
from src.ops import retention  # noqa: E402

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=pb.MOSCOW)
VK = "777"
TG = "555"
OTHER = "999"

FAMILY_TABLES = ("children", "recipients", "games", "deliveries", "feedback", "observations",
                 "child_profiles", "wishes", "members")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        # Family 1: Сева, in VK and Telegram. Family 2: Аня, Telegram only (another chat).
        con.execute("INSERT INTO children (id, name, birth_date, created_at) "
                    "VALUES (1, 'Сева', '2025-05-01', '2026-10-01 08:00:00')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'vk', ?)", (VK,))
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (1, 'telegram', ?)", (TG,))
        con.execute("INSERT INTO members (chat_id, display_name) VALUES (?, 'Мама Севы')", (TG,))
        con.execute("INSERT INTO members (chat_id) VALUES (?)", (f"vk:{VK}",))
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (2, 'Аня', '2025-01-01')")
        con.execute("INSERT INTO recipients (child_id, channel, address) VALUES (2, 'telegram', ?)", (OTHER,))
        con.execute("INSERT INTO members (chat_id) VALUES (?)", (OTHER,))
        for child, game_id in ((1, 10), (2, 20)):
            con.execute("INSERT INTO games (id, game_date, title, child_id, age_months, message_text) "
                        "VALUES (?, '2026-11-11', 'Игра', ?, 18, 'текст')", (game_id, child))
            con.execute("INSERT INTO deliveries (game_id, channel, recipient, status, external_message_id) "
                        "VALUES (?, 'telegram', ?, 'sent', 'm1')", (game_id, TG if child == 1 else OTHER))
            fid = con.execute("INSERT INTO feedback (game_id, channel, raw_text, received_at) "
                              "VALUES (?, 'telegram', 'понравилось, смеялся', '2026-11-11T12:00:00')",
                              (game_id,)).lastrowid
            con.execute("INSERT INTO observations (feedback_id, child_id, game_id, kind, value, evidence) "
                        "VALUES (?, ?, ?, 'like', 'yes', 'смеялся')", (fid, child, game_id))
            con.execute("INSERT INTO child_profiles (child_id, profile_json) VALUES (?, '{}')", (child,))
        con.execute("INSERT INTO wishes (source, child_id, chat_id, text) VALUES ('command', 1, ?, 'больше на улице')",
                    (TG,))
        con.execute("INSERT INTO user_events (chat_id, kind, text) VALUES (?, 'join', '👋 Мама Севы')", (TG,))
        con.execute("INSERT INTO model_runs (target_date, child_id, attempt, outcome, cost_usd) "
                    "VALUES ('2026-11-11', 1, 1, 'pass', 0.1)")
        con.commit()
        con.close()
        patcher = mock.patch.object(storage, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = NOW
        self.dialog = vd.VkDialog(now=lambda: self.now)
        self.bot = pb.ProductBot(self.db, "1", "gotd_bot", now=lambda: self.now)
        self.sent, self.n = [], 0

    def vk(self, text):
        self.n += 1
        before = len(self.sent)
        result = vk_inbound.route(f"e{self.n}", VK, text, lambda p, t, r: self.sent.append(t), dialog=self.dialog)
        return result if result == "feedback" else (self.sent[-1] if len(self.sent) > before else None)

    def tg(self, text, chat=TG):
        self.n += 1
        return self.bot.handle({"message": {"message_id": self.n, "text": text,
                                            "chat": {"id": int(chat), "type": "private"},
                                            "from": {"id": int(chat), "first_name": "Мама"}}})

    def q(self, sql, params=()):
        con = ch.connect_db(self.db)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def count(self, table, where="1", params=()):
        return self.q(f"SELECT count(*) FROM {table} WHERE {where}", params)[0][0]

    def dump(self):
        return {t: [tuple(r) for r in self.q(f"SELECT * FROM {t} ORDER BY 1")] for t in FAMILY_TABLES}


class Erasure(Base):
    def test_the_request_names_the_children_and_needs_a_confirmation(self):
        reply = self.tg("/forget")
        self.assertIn("Сева", reply)
        self.assertIn("второго мессенджера", reply)
        self.assertNotIn("Аня", reply)
        self.assertEqual(self.count("children"), 2)
        self.assertIn("ничего не удаляем", self.tg("отмена"))
        self.assertEqual(self.count("children"), 2)

    def test_another_message_cancels_the_request_and_goes_on_as_usual(self):
        self.tg("/forget")
        self.assertIn("записал", self.tg("сегодня всё получилось"))
        self.assertEqual(self.count("erasures"), 0)

    def test_a_stale_confirmation_does_nothing(self):
        self.tg("/forget")
        self.now = NOW + timedelta(hours=2)
        self.tg("удалить")
        self.assertEqual(self.count("children"), 2)

    def test_confirmed_the_whole_family_leaves_the_live_tables_and_only_it(self):
        self.tg("/forget")
        reply = self.tg("удалить")
        self.assertIn("в архиве до 26.11.2026", reply)
        self.assertIn("не позже 10.12.2026", reply)
        self.assertIn("почему уходите", reply)
        self.assertEqual([r[0] for r in self.q("SELECT name FROM children")], ["Аня"])
        for table in ("recipients", "games", "deliveries", "feedback", "observations", "child_profiles"):
            self.assertEqual(self.count(table), 1, table)
        self.assertEqual(self.count("members", "chat_id IN (?, ?)", (TG, f"vk:{VK}")), 0)
        self.assertEqual(self.count("wishes"), 0)
        # the owner's events lose names; the spend stays measured
        self.assertEqual(self.count("user_events", "text LIKE '%Мама Севы%'"), 0)
        self.assertEqual(self.q("SELECT child_id, cost_usd FROM model_runs")[0][:], (None, 0.1))
        events = [r[0] for r in self.q("SELECT text FROM user_events WHERE kind = 'erasure'")]
        self.assertEqual(len(events), 1)
        self.assertNotIn("Сева", events[0])

    def test_the_other_chat_of_the_family_is_archived_too_and_can_bring_it_back(self):
        self.tg("/forget")
        self.tg("удалить")
        self.assertIn("в архиве", self.vk("привет"))
        self.assertIn("С возвращением", self.vk("вернуть"))
        self.assertEqual(self.count("children"), 2)

    def test_restore_puts_back_every_row(self):
        before = self.dump()
        self.tg("/forget")
        self.tg("удалить")
        self.assertIn("Сева", self.tg("вернуть"))
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.q("SELECT status, archive_json FROM erasures")[0][:], ("restored", None))
        self.assertIn("записал", self.tg("сыграли ещё раз"))

    def test_restore_after_the_ids_were_taken_remaps_them(self):
        self.tg("/forget")
        self.tg("удалить")
        con = ch.connect_db(self.db)
        con.execute("INSERT INTO children (id, name, birth_date) VALUES (1, 'Новый', '2026-01-01')")
        con.execute("INSERT INTO games (id, game_date, title, child_id) VALUES (10, '2026-11-12', 'Чужая', 1)")
        con.commit()
        con.close()
        self.tg("вернуть")
        sева = self.q("SELECT id FROM children WHERE name = 'Сева'")[0][0]
        self.assertNotEqual(sева, 1)
        game = self.q("SELECT id FROM games WHERE child_id = ?", (sева,))[0][0]
        self.assertNotEqual(game, 10)
        self.assertEqual(self.count("feedback", "game_id = ?", (game,)), 1)
        self.assertEqual(self.count("recipients", "child_id = ?", (sева,)), 2)
        self.assertEqual(self.q("SELECT title FROM games WHERE id = 10")[0][0], "Чужая")

    def test_the_survey_is_optional_and_stored_without_the_family(self):
        self.vk("удалить наши данные")
        self.vk("удалить")
        self.assertIn("пару слов", self.vk("5"))
        self.assertIn("Спасибо", self.vk("много сообщений, не успеваем"))
        rows = self.q("SELECT channel, reason, text, days_used FROM exit_surveys")
        self.assertEqual([tuple(r) for r in rows], [("vk", "другое", "много сообщений, не успеваем", 42)])
        self.assertIn("в архиве", self.vk("ещё что-то"))
        self.assertEqual(self.count("exit_surveys"), 1)
        note = self.q("SELECT text FROM user_events WHERE kind = 'exit_survey'")[0][0]
        self.assertIn("много сообщений", note)

    def test_a_number_answers_the_survey(self):
        self.tg("/forget")
        self.tg("удалить")
        self.tg("3")
        self.assertEqual(self.q("SELECT reason FROM exit_surveys")[0][0], "нет времени")

    def test_delete_now_purges_the_archive_and_keeps_only_a_hash(self):
        self.tg("/forget")
        self.tg("удалить")
        self.assertIn("Удалили", self.tg("удалить сейчас"))
        row = self.q("SELECT status, address, archive_json FROM erasures")[0]
        self.assertEqual(row["status"], "purged")
        self.assertTrue(row["address"].startswith("sha256:"))
        self.assertIsNone(row["archive_json"])
        self.assertEqual(self.count("erasure_addresses"), 0)
        self.assertEqual(self.tg("вернуть"), pb.CLOSED)  # nothing to come back to

    def test_nothing_about_the_family_survives_a_purge(self):
        self.tg("/forget")
        self.tg("удалить")
        self.tg("удалить сейчас")
        con = sqlite3.connect(self.db)
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            for row in con.execute(f"SELECT * FROM {table}"):
                blob = json.dumps(row, ensure_ascii=False, default=str)
                for secret in ("Сева", "Мама Севы", TG, VK, "больше на улице"):
                    self.assertNotIn(secret, blob, (table, row))
        con.close()

    def test_a_vk_visitor_without_children_is_forgotten_at_once(self):
        con = ch.connect_db(self.db)
        con.execute("INSERT INTO members (chat_id) VALUES ('vk:123')")
        con.execute("INSERT INTO onboarding (chat_id, step, name) VALUES ('vk:123', 'birth_date', 'Петя')")
        con.commit()
        con.close()
        dialog = vd.VkDialog(now=lambda: NOW)
        out = []
        vk_inbound.route("x1", "123", "удалить наши данные", lambda p, t, r: out.append(t), dialog=dialog)
        vk_inbound.route("x2", "123", "удалить", lambda p, t, r: out.append(t), dialog=dialog)
        self.assertIn("Удалили", out[-1])
        self.assertEqual(self.count("onboarding"), 0)
        self.assertEqual(self.count("members", "chat_id = 'vk:123'"), 0)

    def test_other_families_are_never_touched(self):
        before = self.q("SELECT * FROM feedback WHERE game_id = 20")
        self.tg("/forget", chat=OTHER)
        self.tg("удалить", chat=OTHER)
        self.assertEqual(self.count("children", "name = 'Сева'"), 1)
        self.tg("вернуть", chat=OTHER)
        self.assertEqual([tuple(r) for r in self.q("SELECT * FROM feedback WHERE game_id = 20")],
                         [tuple(r) for r in before])


class Retention(Base):
    def run_at(self, days, dry_run=False):
        return retention.run(self.db, now=NOW + timedelta(days=days), dry_run=dry_run)

    def test_feedback_texts_and_quotes_go_after_their_term_and_the_conclusions_stay(self):
        self.assertEqual(self.run_at(30)["feedback_texts"], 0)
        counts = self.run_at(retention.FEEDBACK_TEXT_DAYS + 1)
        self.assertEqual(counts["feedback_texts"], 2)
        self.assertEqual(self.count("feedback", "raw_text != ''"), 0)
        self.assertEqual(self.count("observations", "evidence != ''"), 0)
        self.assertEqual(self.count("observations", "kind = 'like'"), 2)
        self.assertEqual(self.count("wishes", "chat_id IS NOT NULL"), 0)

    def test_dry_run_changes_nothing(self):
        counts = self.run_at(retention.FEEDBACK_TEXT_DAYS + 1, dry_run=True)
        self.assertEqual(counts["feedback_texts"], 2)
        self.assertEqual(self.count("feedback", "raw_text != ''"), 2)
        self.assertEqual(self.count("user_events", "kind = 'retention'"), 0)

    def test_an_archive_is_purged_after_the_grace_period(self):
        self.tg("/forget")
        self.tg("удалить")
        self.assertEqual(self.run_at(erasure.ERASURE_GRACE_DAYS - 1)["archives"], 0)
        self.assertEqual(self.run_at(erasure.ERASURE_GRACE_DAYS + 1)["archives"], 1)
        self.assertEqual(self.q("SELECT status FROM erasures")[0][0], "purged")
        summary = self.q("SELECT text FROM user_events WHERE kind = 'retention'")[0][0]
        self.assertIn("архивы удалений 1", summary)

    def test_a_paused_family_is_deleted_after_a_year_of_silence_an_active_one_never(self):
        con = ch.connect_db(self.db)
        con.execute("UPDATE recipients SET active = 0 WHERE child_id = 1")
        con.commit()
        con.close()
        self.assertEqual(self.run_at(200)["inactive_families"], 0)
        self.assertEqual(self.run_at(retention.INACTIVE_DAYS + 5)["inactive_families"], 1)
        self.assertEqual([r[0] for r in self.q("SELECT name FROM children")], ["Аня"])

    def test_service_rows_follow_their_terms(self):
        con = ch.connect_db(self.db)
        con.execute("INSERT INTO outbox (channel, address, text, created_at) VALUES ('vk', '1', 'x', '2026-09-01')")
        con.execute("INSERT INTO members (chat_id, joined_at) VALUES ('vk:42', '2026-09-01')")
        con.execute("INSERT INTO applications (channel, address, name, birth_date, status, decided_at) "
                    "VALUES ('vk', '43', 'Петя', '2025-01-01', 'rejected', '2026-09-01')")
        con.commit()
        con.close()
        self.assertGreaterEqual(self.run_at(0)["service"], 3)
        self.assertEqual(self.count("outbox"), 0)
        self.assertEqual(self.count("members", "chat_id = 'vk:42'"), 0)
        self.assertEqual(self.count("applications"), 0)
        self.assertEqual(self.count("members", "chat_id = ?", (f"vk:{VK}",)), 1)  # a family stays

    def test_the_weekly_report_counts_departures_and_reasons_only(self):
        self.assertEqual(retention.exits_report(self.db, now=NOW), "Уходы за 7 дн.: нет")
        self.tg("/forget")
        self.tg("удалить")
        self.tg("2")
        text = retention.exits_report(self.db, now=NOW + timedelta(hours=1))
        self.assertEqual(text, "Уходы за 7 дн.: 1, вернулись 0; причины: ребёнок вырос ×1")


if __name__ == "__main__":
    unittest.main()
