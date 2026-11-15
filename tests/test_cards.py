"""Game cards: a skill picture above the text, never a reason to miss a day."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.delivery import cards  # noqa: E402
from src.delivery import game_delivery as gd  # noqa: E402

SAVED = [{"owner_id": -241213337, "id": 457239100, "access_key": "abc"}]


def vk_ok(method, token, **params):
    if method == "photos.getMessagesUploadServer":
        return {"upload_url": "https://upload.example/"}
    return SAVED


class Cards(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GAME_CARDS": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_skill_has_a_picture(self):
        from src.preparation import skills
        for key in skills.SKILLS:
            self.assertIsNotNone(cards.picture(key), key)

    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {"GAME_CARDS": ""}):
            self.assertIsNone(cards.picture("speech"))
            self.assertEqual(cards.telegram_preview("speech"), {"is_disabled": True})

    def test_telegram_shows_the_skill_picture_above_the_text(self):
        preview = cards.telegram_preview("speech")
        self.assertTrue(preview["url"].endswith("/skill_speech.jpg"))
        self.assertTrue(preview["show_above_text"] and preview["prefer_large_media"])

    def test_unknown_or_missing_skill_means_no_picture(self):
        self.assertEqual(cards.telegram_preview(None), {"is_disabled": True})
        self.assertEqual(cards.telegram_preview("no_such_skill"), {"is_disabled": True})
        self.assertIsNone(cards.vk_attachment("no_such_skill", "1", "t", vk=vk_ok, upload=mock.Mock()))

    def test_vk_attachment_is_uploaded_for_the_dialog(self):
        upload = mock.Mock(return_value={"photo": "p", "server": 1, "hash": "h"})
        calls = []

        def vk(method, token, **params):
            calls.append((method, params))
            return vk_ok(method, token, **params)

        self.assertEqual(cards.vk_attachment("speech", "777", "t", vk=vk, upload=upload),
                         "photo-241213337_457239100_abc")
        self.assertEqual(calls[0], ("photos.getMessagesUploadServer", {"peer_id": "777"}))

    def test_an_occasion_shows_its_greeting_picture_when_there_is_one(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cards, "ASSETS", Path(tmp)):
            (Path(tmp) / "skill_speech.jpg").write_bytes(b"x")
            self.assertEqual(cards.picture("speech", "mothers_day").name, "skill_speech.jpg")
            (Path(tmp) / "greet_mothers_day.jpg").write_bytes(b"x")
            self.assertEqual(cards.picture("speech", "mothers_day").name, "greet_mothers_day.jpg")
            self.assertEqual(cards.picture("speech").name, "skill_speech.jpg")

    def test_a_failed_upload_falls_back_to_no_picture(self):
        def broken(method, token, **params):
            raise RuntimeError("VK down")
        with mock.patch("sys.stdout"):
            self.assertIsNone(cards.vk_attachment("speech", "777", "t", vk=broken, upload=mock.Mock(),
                                                  sleep=lambda s: None))

    def test_a_flaky_upload_is_retried(self):
        upload = mock.Mock(side_effect=[RuntimeError("HTTP Error 405"), {"photo": "p", "server": 1, "hash": "h"}])
        with mock.patch("sys.stdout"):
            self.assertEqual(cards.vk_attachment("speech", "777", "t", vk=vk_ok, upload=upload, sleep=lambda s: None),
                             "photo-241213337_457239100_abc")
        self.assertEqual(upload.call_count, 2)


class SendMessage(unittest.TestCase):
    def game(self, channel, recipient="777"):
        return {"channel": channel, "recipient": recipient, "message_text": "игра", "skill": "speech"}

    def test_vk_sends_one_message_with_the_picture(self):
        with mock.patch.object(gd.cards, "vk_attachment", return_value="photo-1_2"), \
                mock.patch.object(gd, "vk_send", return_value=5) as send, \
                mock.patch.dict(os.environ, {"VK_TOKEN": "t"}):
            self.assertEqual(gd.send_message(self.game("vk"), 42), 5)
        send.assert_called_once_with("игра", 42, peer_id="777", attachment="photo-1_2")

    def test_vk_without_a_picture_is_the_plain_message(self):
        with mock.patch.object(gd.cards, "vk_attachment", return_value=None), \
                mock.patch.object(gd, "vk_send", return_value=5) as send:
            gd.send_message(self.game("vk"), 42)
        send.assert_called_once_with("игра", 42, peer_id="777")

    def test_telegram_passes_the_preview(self):
        with mock.patch.object(gd, "telegram_send", return_value=9) as send, \
                mock.patch.dict(os.environ, {"GAME_CARDS": "1"}):
            gd.send_message(self.game("telegram"), 42)
        self.assertTrue(send.call_args.kwargs["preview"]["url"].endswith("skill_speech.jpg"))


if __name__ == "__main__":
    unittest.main()
