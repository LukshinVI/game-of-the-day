#!/usr/bin/env python3
"""The host's pulse for the watcher outside it (worker/src/watch.js).

Every 5 minutes (game-heartbeat.timer) the host posts /watch/beat to the Worker; when
the beats stop for 15 minutes the Worker tells the owner. The feedback consumer uses
canary_seen() to report the hourly canary it took off the storage queue: that proves
the VK -> Worker -> queue -> host chain without a model call and without touching the
database.

WATCH_URL (the Worker's base URL) and WATCH_SECRET come from watch.env; unset means
off (a host without a watcher stays quiet, it does not fail).

    python3 src/ops/heartbeat.py
"""

import json
import os
import sys
import urllib.request

TIMEOUT_SECONDS = 15


def configured():
    return bool(os.environ.get("WATCH_URL") and os.environ.get("WATCH_SECRET"))


def post(path, body=None, opener=urllib.request.urlopen):
    request = urllib.request.Request(
        os.environ["WATCH_URL"].rstrip("/") + path,
        data=json.dumps(body or {}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['WATCH_SECRET']}",
            "Content-Type": "application/json",
            "User-Agent": "game-of-the-day-heartbeat/1",
        },
    )
    with opener(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status


def beat(opener=urllib.request.urlopen):
    return post("/watch/beat", opener=opener)


def canary_seen(canary_id, opener=urllib.request.urlopen):
    return post("/watch/canary", {"id": canary_id}, opener=opener)


def main():
    if not configured():
        print("heartbeat: WATCH_URL / WATCH_SECRET not set, nothing sent")
        return 0
    try:
        beat()
    except Exception as exc:  # noqa: BLE001 - one missed beat is not an incident
        print(f"heartbeat failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
