"""Voice feedback: only the text is kept; any failure keeps today's label."""

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
os.environ.setdefault("FEEDBACK_DB_PATH", str(Path(tempfile.mkdtemp()) / "import-guard.db"))

from src.bot import product_bot as pb  # noqa: E402
from src.feedback import voice  # noqa: E402

ON = {"GAME_VOICE": "1", "CF_AI_TOKEN": "t", "CF_ACCOUNT_ID": "acc"}


def heard(text):
    return lambda audio: text


class Voice(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_transcribe_reads_the_model_text(self):
        posted = []

        def post(url, body):
            posted.append((url, json.loads(body)))
            return {"success": True, "result": {"text": "  ему   очень понравилось  "}}
        self.assertEqual(voice.transcribe(b"ogg", post=post), "ему очень понравилось")
        self.assertIn("/accounts/acc/ai/run/@cf/openai/whisper-large-v3-turbo", posted[0][0])
        self.assertEqual(posted[0][1]["language"], "ru")

    def test_telegram_voice_becomes_labelled_text(self):
        get = mock.Mock(side_effect=[json.dumps({"result": {"file_path": "voice/1.oga"}}).encode(), b"ogg"])
        message = {"voice": {"file_id": "F", "duration": 9}}
        self.assertEqual(voice.from_telegram(message, "tok", get=get, transcribe_fn=heard("смеялся")),
                         "[голосовое] смеялся")
        self.assertIn("/file/bottok/voice/1.oga", get.call_args_list[1].args[0])

    def test_anything_wrong_keeps_the_label(self):
        message = {"voice": {"file_id": "F", "duration": 9}}
        broken = mock.Mock(side_effect=OSError("down"))
        with mock.patch("sys.stdout"):
            self.assertEqual(voice.from_telegram(message, "tok", get=broken), voice.LABEL)
        self.assertEqual(voice.from_telegram({"voice": {"file_id": "F", "duration": 600}}, "tok",
                                             get=mock.Mock(), transcribe_fn=heard("x")), voice.LABEL)
        get = mock.Mock(side_effect=[json.dumps({"result": {"file_path": "v"}}).encode(), b"ogg"])
        self.assertEqual(voice.from_telegram(message, "tok", get=get, transcribe_fn=heard("")), voice.LABEL)
        with mock.patch.dict(os.environ, {"GAME_VOICE": ""}):
            self.assertEqual(voice.from_telegram(message, "tok", get=mock.Mock(), transcribe_fn=heard("x")),
                             voice.LABEL)

    def test_vk_links_only_from_vk(self):
        self.assertTrue(voice.vk_url_allowed("https://psv4.userapi.com/c1/a.ogg"))
        for bad in ("http://psv4.userapi.com/a.ogg", "https://evil.example/a.ogg",
                    "https://userapi.com.evil.example/a.ogg", None):
            self.assertFalse(voice.vk_url_allowed(bad), bad)
        self.assertEqual(voice.from_vk("https://evil.example/a.ogg", 5, get=mock.Mock(),
                                       transcribe_fn=heard("x")), voice.LABEL)
        self.assertEqual(voice.from_vk("https://psv4.userapi.com/a.ogg", 5, get=mock.Mock(return_value=b"o"),
                                       transcribe_fn=heard("да")), "[голосовое] да")

    def test_the_faq_says_what_happens_to_voice(self):
        self.assertIn("Голосовые переводим в текст", pb.faq_text())
        self.assertNotIn("голосовые не сохраняем", pb.faq_text())
        with mock.patch.dict(os.environ, {"GAME_VOICE": ""}):
            self.assertIn("Фото, видео и голосовые не сохраняем", pb.faq_text())


class Consumer(unittest.TestCase):
    def setUp(self):
        for name in ("CF_ACCOUNT_ID", "CF_STORAGE_QUEUE_ID", "CF_QUEUE_API_TOKEN"):
            os.environ.setdefault(name, "dummy")
        from src.feedback import queue_consumer
        self.qc = queue_consumer
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "t.db"
        con = sqlite3.connect(self.db)
        con.executescript((PROJECT_ROOT / "src" / "db" / "schema.sql").read_text())
        con.commit()
        con.close()
        patcher = mock.patch.object(queue_consumer, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def payload(self):
        return {"event_id": "e1", "channel": "vk", "raw_text": "[голосовое]", "address": "777",
                "audio_url": "https://psv4.userapi.com/a.ogg", "audio_seconds": 5}

    def test_the_label_is_replaced_by_what_was_said(self):
        with mock.patch.object(self.qc.voice, "from_vk", return_value="[голосовое] понравилось") as heard_vk:
            self.assertEqual(self.qc.with_transcript(self.payload())["raw_text"], "[голосовое] понравилось")
        heard_vk.assert_called_once_with("https://psv4.userapi.com/a.ogg", 5)

    def test_a_redelivery_keeps_the_first_text(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO games (id, game_date, title, skill) VALUES (1, '2026-11-15', 'И', 'speech')")
        con.execute("INSERT INTO feedback (game_id, channel, external_event_id, raw_text) "
                    "VALUES (1, 'vk', 'e1', '[голосовое] первый раз')")
        con.commit()
        con.close()
        with mock.patch.object(self.qc.voice, "from_vk") as heard_vk:
            self.assertEqual(self.qc.with_transcript(self.payload())["raw_text"], "[голосовое] первый раз")
        heard_vk.assert_not_called()

    def test_envelope_validation(self):
        envelope = {"version": "feedback-storage-v1", **self.payload()}
        self.assertEqual(self.qc.validate_envelope(envelope)["audio_seconds"], 5)
        with self.assertRaises(ValueError):
            self.qc.validate_envelope({**envelope, "audio_url": "file:///etc/passwd"})


class Hallucinations(unittest.TestCase):
    def test_subtitle_credits_on_silence_are_no_transcript(self):
        for text in ("Продолжение следует...", " Субтитры сделал DimaTorzok", "Спасибо за просмотр!", "...", ""):
            self.assertTrue(voice.hallucination(text), text)
        post = lambda url, body: {"success": True, "result": {"text": " Продолжение следует..."}}  # noqa: E731
        with mock.patch.dict(os.environ, ON):
            self.assertEqual(voice.transcribe(b"x", post=post), "")

    def test_real_feedback_is_kept_even_with_a_common_word(self):
        for text in ("Сыну очень понравилось, просил ещё", "Спасибо, было весело, продолжение будет завтра"):
            self.assertFalse(voice.hallucination(text), text)


if __name__ == "__main__":
    unittest.main()
