"""Telegram API errors in the journal: status and description, never the token."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops import telegram_errors  # noqa: E402

URL = "https://api.telegram.org/bot123:SECRET/getUpdates"


def http_error(code, body):
    return urllib.error.HTTPError(URL, code, "x", {}, io.BytesIO(body))


class Describe(unittest.TestCase):
    def test_conflict_and_rate_limit_carry_telegrams_words(self):
        conflict = json.dumps({"ok": False, "error_code": 409,
                               "description": "Conflict: terminated by other getUpdates request"}).encode()
        self.assertEqual(telegram_errors.describe(http_error(409, conflict)),
                         "HTTPError 409: Conflict: terminated by other getUpdates request")
        limited = json.dumps({"description": "Too Many Requests", "parameters": {"retry_after": 7}}).encode()
        self.assertEqual(telegram_errors.describe(http_error(429, limited)),
                         "HTTPError 429: Too Many Requests (retry_after 7s)")

    def test_a_proxy_page_and_network_errors_stay_short_and_tokenless(self):
        for text in (telegram_errors.describe(http_error(502, b"<html>Bad Gateway</html>")),
                     telegram_errors.describe(urllib.error.URLError(TimeoutError())),
                     telegram_errors.describe(TimeoutError())):
            self.assertNotIn("SECRET", text)
        self.assertEqual(telegram_errors.describe(http_error(502, b"<html>")), "HTTPError 502")
        self.assertEqual(telegram_errors.describe(urllib.error.URLError(TimeoutError())), "URLError: TimeoutError")
        self.assertEqual(telegram_errors.describe(TimeoutError()), "TimeoutError")


if __name__ == "__main__":
    unittest.main()
