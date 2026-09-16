#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


TELEGRAM_API = "https://api.telegram.org"


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def send_message(text):
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")

    data = json.loads(body)

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data["result"].get("message_id")


def notify_info(title, details=None):
    text = f"🟢 {title}"
    if details:
        text += f"\n\n{details}"
    return send_message(text)


def notify_warning(title, details=None):
    text = f"🟡 {title}"
    if details:
        text += f"\n\n{details}"
    return send_message(text)


def notify_critical(title, details=None):
    text = f"🔴 {title}"
    if details:
        text += f"\n\n{details}"
    return send_message(text)


def main():
    parser = argparse.ArgumentParser(prog="game-ops-notifier")

    parser.add_argument(
        "level",
        choices=["info", "warning", "critical"],
    )
    parser.add_argument("title")
    parser.add_argument("--details")

    args = parser.parse_args()

    functions = {
        "info": notify_info,
        "warning": notify_warning,
        "critical": notify_critical,
    }

    message_id = functions[args.level](
        args.title,
        args.details,
    )

    print(
        f"Telegram notification sent. "
        f"message_id={message_id}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
