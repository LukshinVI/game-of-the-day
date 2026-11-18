"""Multi-child support (issues #20, #21): schema migration, per-child preparation,
per-recipient delivery over VK and Telegram, outcome check and feedback scoping.

Everything runs against temporary databases; model, VK and Telegram are mocked.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import children as ch  # noqa: E402
from src.delivery import game_delivery as gd  # noqa: E402
from src.ops import day_outcome_check as doc  # noqa: E402
from src.preparation import game_preparation as gp  # noqa: E402
from src.preparation import alerts  # noqa: E402
from src.preparation import store  # noqa: E402
from src.preparation import chain  # noqa: E402
from src.preparation import layout  # noqa: E402


def born(months):
    """A birth date that many months before today (Moscow): children in the fixtures keep
    their age whatever the date, so the tests do not expire (the shifted-clock CI run)."""
    today = datetime.now(chain.MOSCOW).date()
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return today.replace(year=year, month=month + 1, day=min(today.day, 28)).isoformat()


OLDER = born(14)
YOUNGER = born(8)

SCHEMA = PROJECT_ROOT / "src" / "db" / "schema.sql"
SCHEMA_V1 = PROJECT_ROOT / "tests" / "fixtures" / "schema_v1.sql"
URL = "https://pathways.org/activities/kickin-back"


def candidate(age, title="Толкни мяч"):
    data = {
        "title": title,
        "age_months": age,
        "duration_minutes": 5,
        "goal": "Тренировать движения ног.",
        "skill": "coordination",
        "mechanics": f"{title}: ребёнок толкает мяч стопами",
        "materials": ["Большой мягкий мяч"],
        "steps": ["Положите мяч.", "Покажите движение."],
        "safety": "Под присмотром взрослого.",
        "source_name": "Pathways.org",
        "source_url": URL,
    }
    return layout.with_rendered_message(data)


class TempDb(unittest.TestCase):
    schema = SCHEMA

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "test.db"
        con = sqlite3.connect(self.db)
        con.executescript(self.schema.read_text())
        con.close()
        for module in (store, gd, ch):
            patcher = mock.patch.object(module, "DB_PATH", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.today = datetime.now(gd.MOSCOW).date().isoformat()

    def con(self):
        con = ch.connect_db(self.db)
        self.addCleanup(con.close)
        return con

    def query(self, sql, params=()):
        return [tuple(row) for row in self.con().execute(sql, params).fetchall()]


class Migration(TempDb):
    schema = SCHEMA_V1

    def test_migration_adds_children_without_touching_existing_games(self):
        con = self.con()
        con.execute("INSERT INTO games(id, legacy_id, game_date, title) VALUES (1, '001', '2026-09-01', 'A')")
        con.commit()
        self.assertFalse(ch.is_migrated(con))
        self.assertEqual(ch.active_children(con), [])  # single-child mode before migration

        ch.migrate(con)
        ch.migrate(con)  # idempotent
        self.assertTrue(ch.is_migrated(con))
        self.assertEqual(self.query("SELECT id, child_id FROM games"), [(1, None)])
        self.assertEqual(self.query("SELECT value FROM migration_meta WHERE key='schema_version'"), [(str(ch.SCHEMA_VERSION),)])

    def test_adopt_existing_attaches_the_history_to_the_first_child(self):
        con = self.con()
        con.execute("INSERT INTO games(id, legacy_id, game_date, title) VALUES (1, '001', '2026-09-01', 'A')")
        con.commit()
        ch.migrate(con)
        child = ch.add_child(con, "Старший", OLDER, adopt_existing=True)
        self.assertEqual(self.query("SELECT child_id FROM games"), [(child,)])

    def test_schema_13_allows_attention_and_keeps_observations(self):
        con = self.con()
        con.execute("INSERT INTO games(id, legacy_id, game_date, title) VALUES (1, '001', '2026-09-01', 'A')")
        con.commit()
        ch.migrate(con)
        con.execute("DROP TABLE observations")  # back to the schema 12 table
        con.executescript(ch.MIGRATION_7.replace("'attention', 'like', 'dislike', 'reason'", "'like', 'dislike'"))
        con.execute("INSERT INTO feedback(id, game_id, channel, raw_text) VALUES (1, 1, 'vk', 'x')")
        con.execute("INSERT INTO observations(id, feedback_id, game_id, kind, value, evidence) "
                    "VALUES (7, 1, 1, 'outcome', 'liked', 'x')")
        con.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO observations(feedback_id, kind, value, evidence) VALUES (1, 'attention', 'short', 'x')")
        ch.migrate(con)
        ch.migrate(con)  # idempotent
        con.execute("INSERT INTO observations(feedback_id, kind, value, evidence) VALUES (1, 'attention', 'short', 'x')")
        con.commit()
        self.assertEqual(self.query("SELECT id, kind FROM observations ORDER BY id"), [(7, "outcome"), (8, "attention")])
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name LIKE 'observations_%'"), [])

    def test_schema_19_allows_reason_and_keeps_observations(self):
        con = self.con()
        con.execute("INSERT INTO games(id, legacy_id, game_date, title) VALUES (1, '001', '2026-09-01', 'A')")
        con.commit()
        ch.migrate(con)
        con.execute("DROP TABLE observations")  # back to the schema 18 table
        con.executescript(ch.MIGRATION_7.replace(", 'reason'", ""))
        con.execute("INSERT INTO feedback(id, game_id, channel, raw_text) VALUES (1, 1, 'vk', 'x')")
        con.execute("INSERT INTO observations(id, feedback_id, game_id, kind, value, evidence) "
                    "VALUES (7, 1, 1, 'attention', 'short', 'x')")
        con.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO observations(feedback_id, kind, value, evidence) VALUES (1, 'reason', 'prep_heavy', 'x')")
        ch.migrate(con)
        con.execute("INSERT INTO observations(feedback_id, kind, value, evidence) VALUES (1, 'reason', 'prep_heavy', 'x')")
        con.commit()
        self.assertEqual(self.query("SELECT id, kind FROM observations ORDER BY id"), [(7, "attention"), (8, "reason")])

    def test_the_fresh_schema_and_the_migrated_one_have_the_same_tables(self):
        con = self.con()
        ch.migrate(con)
        fresh = sqlite3.connect(":memory:")
        fresh.executescript(SCHEMA.read_text())
        tables = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        self.assertEqual(
            [r[0] for r in con.execute(tables)], [r[0] for r in fresh.execute(tables)]
        )
        self.assertEqual(ch.columns(con, "games"), ch.columns(fresh, "games"))


class Recipients(TempDb):
    def test_invalid_input_is_rejected(self):
        con = self.con()
        with self.assertRaises(ValueError):
            ch.add_child(con, "X", "2025-13-01")
        child = ch.add_child(con, "Младший", YOUNGER)
        with self.assertRaises(ValueError):
            ch.add_recipient(con, child, "whatsapp", "1")
        with self.assertRaises(ValueError):
            ch.add_recipient(con, child, "telegram", "@name")
        with self.assertRaises(ValueError):
            ch.add_recipient(con, 999, "vk", "1")
        ch.add_recipient(con, child, "telegram", "-100123")
        with self.assertRaises(sqlite3.IntegrityError):
            ch.add_recipient(con, child, "telegram", "-100123")


class TwoChildren(TempDb):
    def setUp(self):
        super().setUp()
        con = self.con()
        self.older = ch.add_child(con, "Старший", OLDER)
        self.younger = ch.add_child(con, "Младший", YOUNGER)
        ch.add_recipient(con, self.older, "vk", "3000001")
        ch.add_recipient(con, self.younger, "telegram", "555")
        ch.add_recipient(con, self.younger, "vk", "777")
        for module, name in ((alerts, "send_preparation_success_alert"), (chain, "send_failure_alert")):
            patcher = mock.patch.object(module, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(gd, "send_ops_notification")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(alerts, "notify_now")
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self, select):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with mock.patch.object(chain, "select_candidate", side_effect=select):
                try:
                    gp.prepare_run(self.today)
                    return None
                except RuntimeError as exc:
                    return exc

    def by_age(self, history, target_date, birth_date, run=None):
        age = chain.child_age_months(target_date, birth_date)
        return candidate(age, title=f"Игра для {birth_date}"), 1

    def test_each_child_gets_a_game_for_its_own_age_and_one_delivery_per_recipient(self):
        self.assertIsNone(self.prepare(self.by_age))
        games = self.query("SELECT child_id, age_months FROM games ORDER BY child_id")
        expected = [
            (self.older, chain.child_age_months(self.today, OLDER)),
            (self.younger, chain.child_age_months(self.today, YOUNGER)),
        ]
        self.assertEqual(games, expected)
        deliveries = self.query(
            "SELECT g.child_id, d.channel, d.recipient, d.status FROM deliveries d "
            "JOIN games g ON g.id = d.game_id ORDER BY d.id"
        )
        self.assertEqual(deliveries, [
            (self.older, "vk", "3000001", "pending"),
            (self.younger, "telegram", "555", "pending"),
            (self.younger, "vk", "777", "pending"),
        ])

    def test_history_is_per_child(self):
        con = self.con()
        con.execute(
            "INSERT INTO games(legacy_id, game_date, title, child_id) VALUES ('h1', '2026-01-01', 'Старая', ?)",
            (self.older,),
        )
        con.commit()
        seen = {}

        def capture(history, target_date, birth_date, run=None):
            seen[birth_date] = [g["title"] for g in history]
            return self.by_age(history, target_date, birth_date)

        self.prepare(capture)
        self.assertEqual(seen, {OLDER: ["Старая"], YOUNGER: []})

    def test_a_manual_run_can_be_limited_to_one_child(self):
        calls = []

        def counting(history, target_date, birth_date, run=None):
            calls.append(birth_date)
            return self.by_age(history, target_date, birth_date)

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(chain, "select_candidate", side_effect=counting):
            gp.prepare_run(self.today, only_child=self.younger)
            with self.assertRaises(RuntimeError):
                gp.prepare_run(self.today, only_child=999)
        self.assertEqual(calls, [YOUNGER])

    def test_a_rerun_is_free_and_one_failing_child_does_not_block_the_other(self):
        def younger_fails(history, target_date, birth_date, run=None):
            if birth_date == YOUNGER:
                raise RuntimeError("model down")
            return self.by_age(history, target_date, birth_date)

        error = self.prepare(younger_fails)
        self.assertIn("Младший", str(error))
        self.assertEqual(self.query("SELECT child_id FROM games"), [(self.older,)])

        calls = []

        def counting(history, target_date, birth_date, run=None):
            calls.append(birth_date)
            return self.by_age(history, target_date, birth_date)

        self.assertIsNone(self.prepare(counting))
        self.assertEqual(calls, [YOUNGER])  # the older child's game already existed

    def send(self, vk=None, telegram=None, **kwargs):
        vk = vk or mock.Mock(side_effect=[101, 102, 103])
        telegram = telegram or mock.Mock(return_value=201)
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(gd, "vk_send", vk), mock.patch.object(gd, "telegram_send", telegram):
            try:
                gd.send_today(**kwargs)
                error = None
            except RuntimeError as exc:
                error = exc
        return vk, telegram, error

    def state(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gd.print_state()
        return out.getvalue().strip()

    def test_every_recipient_is_sent_once_on_its_own_channel(self):
        self.prepare(self.by_age)
        vk, telegram, error = self.send()
        self.assertIsNone(error)
        self.assertEqual(sorted(c.kwargs["peer_id"] for c in vk.call_args_list), ["3000001", "777"])
        telegram.assert_called_once()
        self.assertEqual(telegram.call_args.args[1], "555")
        self.assertEqual(self.state(), "sent")
        vk2, telegram2, error = self.send()
        vk2.assert_not_called()
        telegram2.assert_not_called()

    def test_a_telegram_failure_does_not_block_vk_and_needs_its_own_retry(self):
        self.prepare(self.by_age)
        failing = mock.Mock(side_effect=RuntimeError("Telegram error 403: bot was blocked"))
        vk, _, error = self.send(telegram=failing)
        self.assertIn("telegram", str(error))
        self.assertEqual(vk.call_count, 2)
        self.assertEqual(self.state(), "failed")
        self.assertEqual(
            self.query("SELECT channel, status FROM deliveries ORDER BY id"),
            [("vk", "sent"), ("telegram", "failed"), ("vk", "sent")],
        )
        vk, telegram, error = self.send(retry_failed=True)
        self.assertIsNone(error)
        vk.assert_not_called()
        telegram.assert_called_once()
        self.assertEqual(self.state(), "sent")

    def test_an_interrupted_telegram_send_is_reported_not_guessed(self):
        self.prepare(self.by_age)
        con = self.con()
        con.execute(
            "UPDATE deliveries SET status='sending', updated_at=datetime('now','-30 minutes') "
            "WHERE channel='telegram'"
        )
        con.commit()
        finder = mock.Mock()
        with mock.patch.object(gd, "vk_find_sent", finder):
            vk, telegram, error = self.send()
        finder.assert_not_called()
        telegram.assert_not_called()
        self.assertEqual(vk.call_count, 2)
        self.assertEqual(
            self.query("SELECT status, last_error FROM deliveries WHERE channel='telegram'"),
            [("failed", "interrupted while sending; telegram delivery cannot be verified")],
        )

    def test_the_outcome_check_requires_every_delivery(self):
        self.prepare(self.by_age)
        self.send(telegram=mock.Mock(side_effect=RuntimeError("down")))
        rows = doc.todays_rows(self.db, self.today)
        problem = doc.evaluate(rows)
        self.assertIn("Канал: telegram", problem)
        self.assertEqual(problem.count("ID: "), 1)
        self.send(retry_failed=True)
        self.assertIsNone(doc.evaluate(doc.todays_rows(self.db, self.today)))


    def test_a_chat_with_two_children_sees_whose_game_it_is(self):
        ch.add_recipient(self.con(), self.older, "telegram", "555")
        self.prepare(self.by_age)
        vk, telegram, error = self.send(vk=mock.Mock(side_effect=[101, 102]), telegram=mock.Mock(side_effect=[201, 202]))
        self.assertIsNone(error)
        headers = sorted(c.args[0].split("\n")[0] for c in telegram.call_args_list)
        self.assertEqual(headers, ["👶 Младший", "👶 Старший"])
        for call in vk.call_args_list:  # VK dialogs serve one child each: unchanged
            self.assertFalse(call.args[0].startswith("👶"))
        self.assertFalse(any(t.startswith("👶") for (t,) in self.query("SELECT message_text FROM games")))


class SingleChildMode(TempDb):
    """No children configured: behaviour is exactly the pre-#20 one."""

    def test_preparation_uses_the_environment_and_the_legacy_vk_delivery(self):
        def select(history, target_date, birth_date, run=None):
            self.assertIsNone(birth_date)
            return candidate(12), 1

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(chain, "select_candidate", side_effect=select), \
                mock.patch.object(alerts, "send_preparation_success_alert"):
            gp.prepare_run(self.today)
        self.assertEqual(
            self.query("SELECT d.channel, d.recipient, d.idempotency_key LIKE 'vk-%' FROM deliveries d"),
            [("vk", None, 1)],
        )


class PausedChildren(TempDb):
    def test_a_child_without_active_recipients_costs_no_model_call_and_no_fallback(self):
        con = self.con()
        child = ch.add_child(con, "Пауза", OLDER)
        ch.add_recipient(con, child, "telegram", "555")
        con.execute("UPDATE recipients SET active = 0")
        con.commit()
        select = mock.Mock()
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(chain, "select_candidate", select):
            gp.prepare_run(self.today)
        select.assert_not_called()  # in particular, no single-child (env) game either
        self.assertEqual(self.query("SELECT count(*) FROM games"), [(0,)])


class Greetings(TempDb):
    def test_a_birthday_is_put_before_the_game_once_and_not_stored(self):
        today = datetime.now(gd.MOSCOW).date()
        if (today.month, today.day) == (2, 29):
            self.skipTest("no 29 February a year ago")
        con = self.con()
        child = ch.add_child(con, "Маша", today.replace(year=today.year - 1).isoformat())
        ch.add_recipient(con, child, "telegram", "555")
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(chain, "select_candidate", return_value=(candidate(12), 1)), \
                mock.patch.object(alerts, "send_preparation_success_alert"):
            gp.prepare_run(self.today)
        telegram = mock.Mock(return_value=201)
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(gd, "telegram_send", telegram), \
                mock.patch.object(gd, "send_ops_notification"):
            gd.send_today()
            gd.send_today()
        telegram.assert_called_once()
        text = telegram.call_args.args[0]
        self.assertTrue(text.startswith("🎂 "), text[:40])
        self.assertIn("Маша", text.split("\n")[0])
        self.assertIn("🎲", text)
        self.assertFalse(any(t.startswith("🎂") for (t,) in self.query("SELECT message_text FROM games")))


class GrownUp(TempDb):
    def test_a_child_older_than_the_supported_range_is_skipped_not_failed(self):
        con = self.con()
        child = ch.add_child(con, "Большой", "2020-01-01")
        ch.add_recipient(con, child, "telegram", "555")
        select = mock.Mock()
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(chain, "select_candidate", select):
            gp.prepare_run(self.today)
        select.assert_not_called()
        self.assertEqual(chain.MAX_AGE_MONTHS_SUPPORTED, 36)


class TestChild(TempDb):
    def test_unattributed_vk_feedback_skips_the_test_childs_newer_game(self):
        from src.feedback import storage
        con = self.con()
        family = ch.add_child(con, "Тимофей", OLDER)
        test = ch.add_child(con, "Тест", OLDER, is_test=True)
        for child, peer, title in ((family, "3000001", "Семейная"), (test, "300000002", "Тестовая")):
            ch.add_recipient(con, child, "vk", peer)
            game = con.execute(
                "INSERT INTO games(legacy_id, game_date, title, child_id) VALUES (?, ?, ?, ?)",
                (title, self.today, title, child),
            ).lastrowid
            con.execute("INSERT INTO deliveries(game_id, channel, recipient, idempotency_key, status) "
                        "VALUES (?, 'vk', ?, ?, 'sent')", (game, peer, f"k-{title}"))
        con.commit()
        target = storage.resolve_feedback_target(con, "vk")
        self.assertEqual(con.execute("SELECT title FROM games WHERE id = ?", (target,)).fetchone()[0], "Семейная")
        self.assertEqual([c["is_test"] for c in ch.active_children(con)], [0])
        self.assertEqual([c["is_test"] for c in ch.active_children(con, include_test=True)], [0, 1])

    def test_a_second_message_goes_to_the_dialogs_newest_game(self):
        from src.feedback import storage
        con = self.con()
        child = ch.add_child(con, "Тест", OLDER, is_test=True)
        ch.add_recipient(con, child, "vk", "300000002")
        ids = []
        for day, title in (("2026-11-08", "Старая"), ("2026-11-15", "Новая")):
            game = con.execute("INSERT INTO games(legacy_id, game_date, title, child_id) VALUES (?, ?, ?, ?)",
                               (title, day, title, child)).lastrowid
            con.execute("INSERT INTO deliveries(game_id, channel, recipient, idempotency_key, status) "
                        "VALUES (?, 'vk', '300000002', ?, 'sent')", (game, f"k-{title}"))
            con.execute("INSERT INTO feedback(game_id, channel, external_event_id, raw_text, received_at) "
                        "VALUES (?, 'vk', ?, 'было', '2026-11-15T10:00:00')", (game, f"e-{title}"))
            ids.append(game)
        con.commit()
        self.assertEqual(storage.resolve_feedback_target(con, "vk", "300000002"), ids[1])
        with self.assertRaises(storage.StorageError):
            storage.resolve_feedback_target(con, "vk", "999")    # another dialog: nothing to guess
        with self.assertRaises(storage.StorageError):
            storage.resolve_feedback_target(con, "vk")            # no address: no guess

    def test_a_voice_note_after_a_button_tap_stays_on_todays_game(self):
        """Production 17-18 Nov: the tap gave today's game its feedback, and the voice
        note seconds later went to the newest game without feedback, days older."""
        from src.feedback import storage
        con = self.con()
        child = ch.add_child(con, "Маша", OLDER)
        ch.add_recipient(con, child, "vk", "3000001")
        ids = []
        for day, title in (("2026-11-15", "Позавчерашняя"), ("2026-11-16", "Вчерашняя"), ("2026-11-17", "Сегодняшняя")):
            game = con.execute("INSERT INTO games(legacy_id, game_date, title, child_id) VALUES (?, ?, ?, ?)",
                               (title, day, title, child)).lastrowid
            con.execute("INSERT INTO deliveries(game_id, channel, recipient, idempotency_key, status) "
                        "VALUES (?, 'vk', '3000001', ?, 'sent')", (game, f"k-{title}"))
            ids.append(game)
        con.execute("INSERT INTO feedback(game_id, channel, external_event_id, raw_text, received_at) "
                    "VALUES (?, 'vk', 'btn:3000001:x:outcome', '[кнопка] 👍', '2026-11-17T13:14:18')", (ids[2],))
        con.commit()
        con.close()
        with mock.patch.object(storage, "DB_PATH", self.db):
            result = storage.save_raw({"event_id": "voice-1", "channel": "vk", "address": "3000001",
                                       "raw_text": "[голосовое] Отличная игра, всё получилось"})
        con = self.con()
        self.assertEqual(con.execute("SELECT game_id FROM feedback WHERE id = ?", (result["feedback_id"],)).fetchone()[0],
                         ids[2])

    def test_a_vk_reply_to_a_game_lands_on_that_game(self):
        from src.feedback import storage
        con = self.con()
        child = ch.add_child(con, "Маша", OLDER)
        ch.add_recipient(con, child, "vk", "3000001")
        ids = []
        for day, title, message in (("2026-11-14", "Мячики", "133"), ("2026-11-15", "Башня", "160")):
            game = con.execute("INSERT INTO games(legacy_id, game_date, title, child_id) VALUES (?, ?, ?, ?)",
                               (title, day, title, child)).lastrowid
            con.execute("INSERT INTO deliveries(game_id, channel, recipient, idempotency_key, status, "
                        "external_message_id) VALUES (?, 'vk', '3000001', ?, 'sent', ?)", (game, f"k-{title}", message))
            ids.append(game)
        con.commit()
        self.assertEqual(storage.replied_game(con, "vk", "3000001", "133"), ids[0])
        self.assertIsNone(storage.replied_game(con, "vk", "999", "133"))   # someone else's dialog
        self.assertIsNone(storage.replied_game(con, "vk", "3000001", "1"))  # not a game message
        con.close()
        with mock.patch.object(storage, "DB_PATH", self.db):
            result = storage.save_raw({"event_id": "e1", "channel": "vk", "raw_text": "мячики были классные",
                                       "address": "3000001", "reply_to": "133"})
        con = self.con()
        self.assertEqual(con.execute("SELECT game_id FROM feedback WHERE id = ?", (result["feedback_id"],)).fetchone()[0],
                         ids[0])

    def test_the_daily_run_skips_test_children_a_manual_run_reaches_them(self):
        con = self.con()
        family = ch.add_child(con, "Тимофей", OLDER)
        test = ch.add_child(con, "Тест", OLDER, is_test=True)
        ch.add_recipient(con, family, "vk", "1")
        ch.add_recipient(con, test, "vk", "2")
        seen = []

        def select(history, target_date, birth_date, run=None):
            seen.append(len(seen))
            return candidate(chain.child_age_months(target_date, birth_date), title=f"Игра {len(seen)}"), 1

        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(chain, "select_candidate", side_effect=select), \
                mock.patch.object(alerts, "send_preparation_success_alert"):
            gp.prepare_run(self.today)
            self.assertEqual(self.query("SELECT child_id FROM games"), [(family,)])
            gp.prepare_run(self.today, only_child=test)
        self.assertEqual(self.query("SELECT child_id FROM games ORDER BY id"), [(family,), (test,)])
