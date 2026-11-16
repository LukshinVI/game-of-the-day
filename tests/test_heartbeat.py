"""src/ops/heartbeat.py: the host's pulse for the watcher outside it."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops import heartbeat  # noqa: E402


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Heartbeat(unittest.TestCase):
    def test_beat_and_canary_carry_the_secret_and_the_id(self):
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, request.get_header("Authorization"), json.loads(request.data)))
            return Response()

        with mock.patch.dict(os.environ, {"WATCH_URL": "https://w.example/", "WATCH_SECRET": "s3"}):
            heartbeat.beat(opener=opener)
            heartbeat.canary_seen("canary-5", opener=opener)
        self.assertEqual(seen, [("https://w.example/watch/beat", "Bearer s3", {}),
                                ("https://w.example/watch/canary", "Bearer s3", {"id": "canary-5"})])

    def test_off_without_configuration(self):
        with mock.patch.dict(os.environ, {"WATCH_URL": "", "WATCH_SECRET": ""}), \
                mock.patch.object(heartbeat, "beat") as beat:
            self.assertEqual(heartbeat.main(), 0)
        beat.assert_not_called()

    def test_a_failed_beat_exits_nonzero_without_the_secret_in_output(self):
        with mock.patch.dict(os.environ, {"WATCH_URL": "https://w.example", "WATCH_SECRET": "s3"}), \
                mock.patch.object(heartbeat, "beat", side_effect=OSError("s3 down")):
            self.assertEqual(heartbeat.main(), 1)


if __name__ == "__main__":
    unittest.main()
