"""What went wrong in a Telegram Bot API call, safe for the journal.

The bots used to log only the exception type ("HTTPError"), which after the ops-bot outage
of 2026-11-18 left the question "whose side?" open. This adds the HTTP status and
Telegram's own description (409 Conflict, 429 Too Many Requests with retry_after, 502…).
Never the URL: it holds the bot token.
"""

import json
import urllib.error


def describe(exc):
    if isinstance(exc, urllib.error.HTTPError):
        text = f"HTTPError {exc.code}"
        try:
            body = json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001 - a non-JSON body (a proxy's HTML page) is fine
            body = {}
        if isinstance(body, dict):
            if body.get("description"):
                text += f": {str(body['description'])[:200]}"
            retry = (body.get("parameters") or {}).get("retry_after")
            if retry is not None:
                text += f" (retry_after {retry}s)"
        return text
    if isinstance(exc, urllib.error.URLError):
        return f"URLError: {type(exc.reason).__name__}"
    return type(exc).__name__
