"""Tests for the owner-only Telegram control (Controller); no network, no delivery CLI, no systemd."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops import manual_bot as bot  # noqa: E402

OWNER = 1001
NOW = 1_000_000.0


class Harness:
    def __init__(self, state="pending"):
        self.state = state
        self.runs = []
        self.started = []
        self.clock = NOW
        self.codes = iter(["4242", "5353", "6464"])
        self.controller = bot.Controller(
            OWNER,
            run=self.run,
            start=self.start,
            now=lambda: self.clock,
            new_code=lambda: next(self.codes),
        )

    def run(self, *args):
        self.runs.append(args)
        return self.state if args == ("state",) else f"<{' '.join(args)}>"

    def start(self, unit):
        self.started.append(unit)
        return f"started {unit}"

    def say(self, text, chat_id=OWNER, sender_id=OWNER, chat_type="private", age=1):
        return self.controller.handle({
            "update_id": 1,
            "message": {
                "date": self.clock - age,
                "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": sender_id},
                "text": text,
            },
        })


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
                for command in ("/send", "/retry", "/status", "/send 4242", "/help"):
                    self.assertIsNone(h.say(command, **kwargs))
        self.assertEqual((h.runs, h.started), ([], []), "an unauthorised message must trigger nothing")

    def test_stale_messages_are_ignored(self):
        h = Harness()
        self.assertIsNone(h.say("/send", age=bot.MAX_MESSAGE_AGE_SECONDS + 5))
        self.assertEqual((h.runs, h.started), ([], []))

    def test_updates_without_a_message_or_text_are_ignored(self):
        h = Harness()
        self.assertIsNone(h.controller.handle({"update_id": 1, "edited_message": {}}))
        self.assertIsNone(h.say(""))


class Commands(unittest.TestCase):
    def test_status_shows_the_delivery_status_only(self):
        h = Harness()
        self.assertIn("<status>", h.say("/status"))
        self.assertEqual((h.runs, h.started), ([("status",)], []))

    def test_the_bot_name_suffix_is_accepted(self):
        self.assertIn("<status>", Harness().say("/status@game_bot"))

    def test_unknown_text_gets_help_and_triggers_nothing(self):
        h = Harness()
        self.assertEqual(h.say("привет"), bot.HELP)
        self.assertEqual(h.say("/rm -rf"), bot.HELP)
        self.assertEqual((h.runs, h.started), ([], []))


class SendFlow(unittest.TestCase):
    def test_send_without_a_code_starts_nothing(self):
        h = Harness()
        reply = h.say("/send")
        self.assertIn("/send 4242", reply)
        self.assertEqual(h.started, [])

    def test_the_correct_code_starts_the_normal_unit_and_verifies_the_state(self):
        h = Harness()
        h.say("/send")
        reply = h.say("/send 4242")
        self.assertEqual(h.started, ["game-delivery.service"])
        self.assertIn("Проверка", reply)
        self.assertIn("<status>", reply)

    def test_a_code_is_single_use(self):
        h = Harness()
        h.say("/send")
        h.say("/send 4242")
        self.assertIn("неверен или истёк", h.say("/send 4242"))
        self.assertEqual(h.started, ["game-delivery.service"])

    def test_a_wrong_code_starts_nothing_and_says_so(self):
        h = Harness()
        h.say("/send")
        self.assertIn("неверен или истёк", h.say("/send 0000"))
        self.assertEqual(h.started, [])
        # the real code no longer works either: the challenge was invalidated
        self.assertIn("неверен или истёк", h.say("/send 4242"))
        self.assertEqual(h.started, [])

    def test_an_expired_code_starts_nothing(self):
        h = Harness()
        h.say("/send")
        h.clock += bot.CONFIRM_TTL_SECONDS + 1
        self.assertIn("неверен или истёк", h.say("/send 4242"))
        self.assertEqual(h.started, [])

    def test_a_code_without_a_prior_request_does_nothing(self):
        h = Harness()
        self.assertIn("неверен или истёк", h.say("/send 4242"))
        self.assertEqual(h.started, [])

    def test_a_pending_code_is_lost_with_the_process(self):
        h = Harness()
        h.say("/send")
        restarted = Harness()
        restarted.say("/send 4242")
        self.assertEqual(restarted.started, [])


class StateGuards(unittest.TestCase):
    def test_send_is_refused_unless_the_delivery_is_pending(self):
        for state in ("sent", "none", "sending", "ambiguous", "weird"):
            with self.subTest(state=state):
                h = Harness(state)
                self.assertNotIn("/send ", h.say("/send"))
                self.assertNotIn("неверен", h.say("/send 4242") or "")
                self.assertEqual(h.started, [])

    def test_a_failed_delivery_is_not_retried_by_send(self):
        h = Harness("failed")
        self.assertIn("/retry", h.say("/send"))
        self.assertEqual(h.started, [])

    def test_retry_is_refused_unless_the_delivery_failed(self):
        for state in ("pending", "sent", "none", "sending"):
            with self.subTest(state=state):
                h = Harness(state)
                h.say("/retry")
                h.say("/retry 4242")
                self.assertEqual(h.started, [])

    def test_retry_uses_its_own_confirmation_and_unit(self):
        h = Harness("failed")
        reply = h.say("/retry")
        self.assertIn("/retry 4242", reply)
        self.assertEqual(h.started, [])
        h.say("/retry 4242")
        self.assertEqual(h.started, ["game-delivery-retry.service"])

    def test_a_send_code_cannot_confirm_a_retry(self):
        h = Harness("pending")
        h.say("/send")
        h.state = "failed"
        self.assertIn("неверен или истёк", h.say("/retry 4242"))
        self.assertEqual(h.started, [])

    def test_the_state_is_rechecked_when_the_code_is_used(self):
        h = Harness("pending")
        h.say("/send")
        h.state = "sent"          # something else delivered in the meantime
        h.say("/send 4242")
        self.assertEqual(h.started, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
