"""Tests for the owner-only Telegram control (Controller); no network, no delivery CLI."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops import manual_bot as bot  # noqa: E402

OWNER = 1001
NOW = 1_000_000.0


class Harness:
    def __init__(self):
        self.calls = []
        self.clock = NOW
        self.controller = bot.Controller(
            OWNER,
            run=self.run,
            now=lambda: self.clock,
            new_code=lambda: "4242",
        )

    def run(self, *args):
        self.calls.append(args)
        return f"<{' '.join(args)}>"

    def say(self, text, chat_id=OWNER, sender_id=OWNER, chat_type="private", age=1, update_id=1):
        update = {
            "update_id": update_id,
            "message": {
                "date": self.clock - age,
                "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": sender_id},
                "text": text,
            },
        }
        return self.controller.handle(update)


class Authorization(unittest.TestCase):
    def test_only_the_owner_in_a_private_chat_is_served(self):
        h = Harness()
        for label, kwargs in (
            ("stranger", {"chat_id": 5, "sender_id": 5}),
            ("owner id in a group", {"chat_type": "group"}),
            ("group with another sender", {"chat_type": "group", "sender_id": 5}),
            ("owner chat but another sender", {"sender_id": 5}),
        ):
            with self.subTest(label):
                self.assertIsNone(h.say("/send", **kwargs))
                self.assertIsNone(h.say("/status", **kwargs))
        self.assertEqual(h.calls, [], "an unauthorised message must never reach the delivery CLI")

    def test_stale_messages_are_ignored(self):
        h = Harness()
        self.assertIsNone(h.say("/send", age=bot.MAX_MESSAGE_AGE_SECONDS + 5))
        self.assertEqual(h.calls, [])

    def test_updates_without_a_message_or_text_are_ignored(self):
        h = Harness()
        self.assertIsNone(h.controller.handle({"update_id": 1, "edited_message": {}}))
        self.assertIsNone(h.say(""))


class Commands(unittest.TestCase):
    def test_status_runs_the_status_command_only(self):
        h = Harness()
        self.assertIn("<status>", h.say("/status"))
        self.assertEqual(h.calls, [("status",)])

    def test_the_bot_name_suffix_is_accepted(self):
        h = Harness()
        self.assertIn("<status>", h.say("/status@game_bot"))

    def test_unknown_text_gets_help_and_runs_nothing(self):
        h = Harness()
        self.assertEqual(h.say("привет"), bot.HELP)
        self.assertEqual(h.say("/rm -rf"), bot.HELP)
        self.assertEqual(h.calls, [])


class SendConfirmation(unittest.TestCase):
    def test_send_without_a_code_only_shows_status_and_a_code(self):
        h = Harness()
        reply = h.say("/send")
        self.assertIn("<status>", reply)
        self.assertIn("/send 4242", reply)
        self.assertEqual(h.calls, [("status",)], "nothing may be sent before confirmation")

    def test_the_correct_code_sends_with_retry_and_verifies_the_result(self):
        h = Harness()
        h.say("/send")
        h.calls.clear()
        reply = h.say("/send 4242")
        self.assertEqual(h.calls, [("send-today", "--retry-failed"), ("status",)])
        self.assertIn("<send-today --retry-failed>", reply)
        self.assertIn("Проверка", reply)

    def test_a_code_is_single_use(self):
        h = Harness()
        h.say("/send")
        h.say("/send 4242")
        h.calls.clear()
        h.say("/send 4242")
        self.assertNotIn(("send-today", "--retry-failed"), h.calls)

    def test_a_wrong_code_does_not_send(self):
        h = Harness()
        h.say("/send")
        h.calls.clear()
        h.say("/send 0000")
        self.assertNotIn(("send-today", "--retry-failed"), h.calls)

    def test_an_expired_code_does_not_send(self):
        h = Harness()
        h.say("/send")
        h.clock += bot.CONFIRM_TTL_SECONDS + 1
        h.calls.clear()
        reply = h.say("/send 4242")
        self.assertNotIn(("send-today", "--retry-failed"), h.calls)
        self.assertIn("/send 4242", reply, "a fresh code is issued instead")

    def test_a_code_without_a_prior_send_does_nothing(self):
        h = Harness()
        h.say("/send 4242")
        self.assertNotIn(("send-today", "--retry-failed"), h.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
