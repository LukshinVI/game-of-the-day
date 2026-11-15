"""Reaction buttons: one tap becomes an observation on the exact game, no model."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
# storage reads its path at import: never let it point at a real database.
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp()) / "import-guard.db"))

from src.bot import product_bot as pb  # noqa: E402
from src.db import children as ch  # noqa: E402
from src.delivery import game_delivery as gd  # noqa: E402
from src.feedback import buttons, profile, vk_inbound  # noqa: E402

SCHEMA = PROJECT_ROOT / "src" / "db" / "schema.sql"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA.read_text())
        con.close()
        con = ch.connect_db(self.db)
        ch.migrate(con)
        self.child = ch.add_child(con, "Тёма", "2025-09-07")
        for gid, skill in ((10, "fine_motor"), (11, "speech")):
            con.execute("INSERT INTO games (id, game_date, title, skill, child_id, message_text) "
                        "VALUES (?, '2026-11-1' || ?, 'Игра', ?, ?, 'текст')", (gid, gid - 9, skill, self.child))
        con.execute("INSERT INTO deliveries (game_id, channel, recipient, status) VALUES (10, 'telegram', '555', 'sent')")
        con.execute("INSERT INTO deliveries (game_id, channel, recipient, status) VALUES (11, 'vk', '777', 'sent')")
        con.commit()
        con.close()

    def con(self):
        con = ch.connect_db(self.db)
        self.addCleanup(con.close)
        return con

    def rows(self, sql, params=()):
        return [tuple(r) for r in self.con().execute(sql, params).fetchall()]


class Record(Base):
    def test_easy_makes_the_next_game_of_that_skill_harder_without_a_model(self):
        con = self.con()
        reply = buttons.record(con, 10, "easy", "telegram", "555")
        con.commit()
        self.assertIn("моторику будет чуть посложнее", reply)
        self.assertEqual(self.rows("SELECT kind, value, skill, game_id FROM observations"),
                         [("difficulty", "too_easy", "fine_motor", 10)])
        self.assertEqual(self.rows("SELECT insights_status, raw_text FROM feedback"), [("button", "[кнопка] 🪶 Легко")])
        self.assertEqual(profile.load(self.con(), self.child)["next_difficulty"], {"fine_motor": "harder"})

    def test_one_answer_per_row_a_second_tap_replaces_it(self):
        con = self.con()
        buttons.record(con, 10, "easy", "telegram", "555")
        buttons.record(con, 10, "hard", "telegram", "555")
        buttons.record(con, 10, "liked", "telegram", "555")
        con.commit()
        self.assertEqual(sorted(self.rows("SELECT kind, value FROM observations")),
                         [("difficulty", "too_hard"), ("outcome", "liked")])
        self.assertEqual(buttons.chosen(self.con(), 10, "telegram", "555"), {"hard", "liked"})

    def test_a_game_never_sent_to_this_chat_is_refused(self):
        con = self.con()
        self.assertIsNone(buttons.record(con, 10, "liked", "telegram", "999"))
        self.assertIsNone(buttons.record(con, 10, "liked", "vk", "555"))
        self.assertEqual(self.rows("SELECT count(*) FROM feedback"), [(0,)])

    def test_the_insights_model_never_sees_a_press(self):
        con = self.con()
        buttons.record(con, 10, "liked", "telegram", "555")
        con.commit()
        self.assertEqual(self.rows("SELECT count(*) FROM feedback WHERE insights_at IS NULL"), [(0,)])


class Telegram(Base):
    def test_a_press_answers_and_marks_the_button(self):
        bot = pb.ProductBot(self.db, "1")
        update = {"update_id": 1, "callback_query": {"id": "q", "data": "r:10:liked",
                  "message": {"message_id": 5, "chat": {"id": 555, "type": "private"}}}}
        calls = []
        pb.answer_callback("t", bot, update, call=lambda token, method, payload, timeout: calls.append((method, payload)))
        self.assertEqual(calls[0], ("answerCallbackQuery", {"callback_query_id": "q", "text": "Спасибо, учли 💛"}))
        method, payload = calls[1]
        self.assertEqual(method, "editMessageReplyMarkup")
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["text"], "✓ 👍 Понравилось")

    def test_a_difficulty_press_explains_itself_in_the_chat(self):
        bot = pb.ProductBot(self.db, "1")
        update = {"update_id": 2, "callback_query": {"id": "q", "data": "r:10:hard",
                  "message": {"message_id": 5, "chat": {"id": 555, "type": "private"}}}}
        calls = []
        pb.answer_callback("t", bot, update, call=lambda token, method, payload, timeout: calls.append((method, payload)))
        self.assertEqual(calls[0][1]["text"], "Спасибо, учли 💛")
        self.assertEqual([m for m, _ in calls], ["answerCallbackQuery", "editMessageReplyMarkup", "sendMessage"])
        self.assertIn("попроще", calls[2][1]["text"])
        self.assertEqual(calls[2][1]["chat_id"], 555)

    def test_the_owner_tries_a_family_game_without_writing(self):
        bot = pb.ProductBot(self.db, "1")
        answer, markup, note = bot.handle_callback({"callback_query": {"data": "r:10:hard",
                                              "message": {"chat": {"id": 1, "type": "private"}}}})
        self.assertEqual(answer, "Тест, не записано 💛")
        self.assertTrue(note.startswith("Тест, не записано. Спасибо, учли"))
        self.assertEqual(markup["inline_keyboard"][1][2]["text"], "✓ 🧗 Сложно")
        self.assertEqual(self.rows("SELECT count(*) FROM observations"), [(0,)])
        answer, _, _ = pb.ProductBot(self.db, "1").handle_callback({"callback_query": {"data": "r:10:hard",
                                              "message": {"chat": {"id": 999, "type": "private"}}}})
        self.assertEqual(answer, "Эта кнопка больше не работает.")

    def test_foreign_data_and_group_chats_are_ignored(self):
        bot = pb.ProductBot(self.db, "1")
        self.assertIsNone(bot.handle_callback({"callback_query": {"data": "x", "message": {"chat": {"type": "private"}}}}))
        self.assertIsNone(bot.handle_callback({"callback_query": {"data": "r:10:liked",
                                                                  "message": {"chat": {"id": 555, "type": "group"}}}}))


class Reasons(Base):
    def test_a_disliked_game_asks_why_once_and_the_reason_steers_the_profile(self):
        bot = pb.ProductBot(self.db, "1")
        press = lambda code: bot.handle_callback({"callback_query": {"data": f"r:10:{code}",  # noqa: E731
                                                  "message": {"chat": {"id": 555, "type": "private"}}}})
        toast, _, note = press("disliked")
        self.assertEqual(toast, "Спасибо, учли 💛")
        text, markup = note
        self.assertEqual(text, buttons.QUESTION)
        self.assertEqual([b["callback_data"] for row in markup["inline_keyboard"] for b in row],
                         ["r:10:bored", "r:10:no_items", "r:10:prep", "r:10:age"])
        self.assertIsNone(press("disliked")[2])  # asked once per game
        toast, markup, note = press("no_items")
        self.assertEqual(markup["inline_keyboard"][0][1]["text"], "✓ 🧸 Не было нужного дома")
        self.assertIn("любом доме", note)
        self.assertEqual(profile.load(self.con(), self.child)["why_not"], {"no_materials": ["Игра"]})
        press("prep")  # one reason per game: a second tap replaces it
        self.assertEqual(profile.load(self.con(), self.child)["why_not"], {"prep_heavy": ["Игра"]})
        press("liked")
        self.assertIsNone(press("disliked")[2])  # a reason is in: not asked again

    def test_not_for_age_counts_as_too_hard(self):
        con = self.con()
        reply = buttons.record(con, 10, "age", "telegram", "555")
        con.commit()
        self.assertIn("моторику будет чуть попроще", reply)
        self.assertEqual(profile.load(self.con(), self.child)["next_difficulty"], {"fine_motor": "easier"})
        self.assertNotIn("why_not", profile.load(self.con(), self.child))

    def test_missing_things_matter_for_a_week_only(self):
        rows = [{"kind": "reason", "value": "no_materials", "evidence": "x", "game_title": "Игра",
                 "created_at": "2026-11-01T10:00:00+00:00"},
                {"kind": "reason", "value": "not_interested", "evidence": "x", "game_title": "Пазл",
                 "created_at": "2026-11-01T10:00:00+00:00"}]
        built = profile.build(rows, profile.datetime(2026, 11, 15, tzinfo=profile.timezone.utc))
        self.assertEqual(built["why_not"], {"not_interested": ["Пазл"]})

    def test_vk_asks_with_buttons_or_in_words_when_vk_refuses_them(self):
        sent = []
        send = lambda peer, text, rid, **kw: sent.append((text, kw.get("keyboard")))  # noqa: E731
        vk_inbound.press("e1", "777", 11, "disliked", send, db_path=self.db)
        text, keyboard = sent[0]
        self.assertEqual(text, buttons.QUESTION)
        self.assertEqual(json.loads(json.loads(keyboard)["buttons"][1][1]["action"]["payload"]), {"r": "age", "g": 11})
        for name in ("CF_ACCOUNT_ID", "CF_STORAGE_QUEUE_ID", "CF_QUEUE_API_TOKEN"):
            os.environ.setdefault(name, "dummy")
        from src.feedback import queue_consumer as qc
        calls = []

        def vk_send(text, rid, peer_id=None, keyboard=None):
            calls.append((text, keyboard))
            if keyboard:
                raise RuntimeError("VK error 912: This is a chat bot feature")
        with mock.patch.object(qc.game_delivery, "vk_send", vk_send):
            qc.vk_reply("777", buttons.QUESTION, 5, keyboard=keyboard)
        self.assertEqual(calls[-1], (buttons.QUESTION_PLAIN, None))


class Vk(Base):
    def test_a_press_is_recorded_once_and_answered(self):
        sent = []
        send = lambda peer, text, rid: sent.append((peer, text, rid))  # noqa: E731
        vk_inbound.press("e1", "777", 11, "liked", send, db_path=self.db)
        vk_inbound.press("e1", "777", 11, "liked", send, db_path=self.db)  # redelivered
        self.assertEqual(self.rows("SELECT count(*) FROM observations"), [(1,)])
        self.assertEqual(len({rid for _, _, rid in sent}), 1)
        self.assertEqual(sent[0][1], "Спасибо, учли 💛")

    def test_payload_parsing(self):
        self.assertEqual(buttons.parse_vk('{"r": "hard", "g": 11}'), (11, "hard"))
        for bad in (None, "", "{oops", '{"r": "x", "g": 11}', '{"r": "hard", "g": "11"}', '{"command":"start"}'):
            self.assertIsNone(buttons.parse_vk(bad), bad)

    def test_the_keyboard_carries_the_game(self):
        keyboard = json.loads(buttons.vk_keyboard(11))
        self.assertTrue(keyboard["inline"])
        payloads = [json.loads(b["action"]["payload"]) for row in keyboard["buttons"] for b in row]
        self.assertEqual({p["g"] for p in payloads}, {11})
        self.assertEqual(len(payloads), 5)


class Delivery(unittest.TestCase):
    def test_buttons_only_with_the_flag(self):
        game = {"channel": "telegram", "recipient": "555", "message_text": "игра", "skill": "speech", "game_id": 10}
        with mock.patch.object(gd, "telegram_send", return_value=1) as send, \
                mock.patch.dict(os.environ, {"GAME_BUTTONS": "", "GAME_CARDS": ""}):
            gd.send_message(game, 1)
        self.assertNotIn("markup", send.call_args.kwargs)
        with mock.patch.object(gd, "telegram_send", return_value=1) as send, \
                mock.patch.dict(os.environ, {"GAME_BUTTONS": "1", "GAME_CARDS": ""}):
            gd.send_message(game, 1)
        self.assertEqual(send.call_args.kwargs["markup"]["inline_keyboard"][1][0]["callback_data"], "r:10:easy")

    def test_vk_gets_the_keyboard_with_the_flag(self):
        game = {"channel": "vk", "recipient": "777", "message_text": "игра", "skill": "speech", "game_id": 11}
        with mock.patch.object(gd, "vk_send", return_value=1) as send, \
                mock.patch.dict(os.environ, {"GAME_BUTTONS": "1", "GAME_CARDS": ""}):
            gd.send_message(game, 1)
        self.assertIn('\\"g\\": 11', send.call_args.kwargs["keyboard"])

    def test_vk_without_bot_features_still_gets_the_game(self):
        game = {"channel": "vk", "recipient": "777", "message_text": "игра", "skill": "speech", "game_id": 11}
        send = mock.Mock(side_effect=[RuntimeError("VK error 912: This is a chat bot feature"), 7])
        with mock.patch.object(gd, "vk_send", send), mock.patch("sys.stdout"), \
                mock.patch.dict(os.environ, {"GAME_BUTTONS": "1", "GAME_CARDS": ""}):
            self.assertEqual(gd.send_message(game, 42), 7)
        self.assertIn("keyboard", send.call_args_list[0].kwargs)
        self.assertNotIn("keyboard", send.call_args_list[1].kwargs)
        self.assertEqual(send.call_args_list[1].args[1], 42)

    def test_other_vk_errors_still_fail_the_delivery(self):
        game = {"channel": "vk", "recipient": "777", "message_text": "игра", "skill": "speech", "game_id": 11}
        with mock.patch.object(gd, "vk_send", side_effect=RuntimeError("VK error 901: can't send")), \
                mock.patch.dict(os.environ, {"GAME_BUTTONS": "1", "GAME_CARDS": ""}):
            with self.assertRaises(RuntimeError):
                gd.send_message(game, 42)

    def test_the_footer_invites_a_tap(self):
        with mock.patch.dict(os.environ, {"GAME_BUTTONS": "1"}):
            self.assertIn("Нажмите кнопку", gd.feedback_footer("игра"))
        with mock.patch.dict(os.environ, {"GAME_BUTTONS": ""}):
            self.assertNotIn("Нажмите кнопку", gd.feedback_footer("игра"))


if __name__ == "__main__":
    unittest.main()
