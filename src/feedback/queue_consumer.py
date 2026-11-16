#!/usr/bin/env python3

import base64
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import children as ch
from src.delivery import game_delivery
from src.feedback import buttons, reactions, vk_inbound, voice
from src.feedback.storage import DB_PATH, StorageError, save_analysis, save_raw
from src.ops import heartbeat


LOG = logging.getLogger("game-feedback-consumer")

API_ROOT = "https://api.cloudflare.com/client/v4"

ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]
QUEUE_ID = os.environ["CF_STORAGE_QUEUE_ID"]
API_TOKEN = os.environ["CF_QUEUE_API_TOKEN"]

BATCH_SIZE = 1
VISIBILITY_TIMEOUT_MS = 30_000
EMPTY_POLL_DELAY_SECONDS = 2
ERROR_DELAY_SECONDS = 5


class QueueApiError(RuntimeError):
    pass


def queue_url(action, queue_id=QUEUE_ID):
    return (
        f"{API_ROOT}/accounts/{ACCOUNT_ID}"
        f"/queues/{queue_id}/messages/{action}"
    )


def api_post(action, payload, queue_id=QUEUE_ID, url=None):
    request = urllib.request.Request(
        url or queue_url(action, queue_id),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "game-of-the-day-feedback-consumer/1",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            raw = response.read()

    except urllib.error.HTTPError as error:
        body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise QueueApiError(
            f"Cloudflare HTTP {error.code}: {body[:500]}"
        ) from error

    except urllib.error.URLError as error:
        raise QueueApiError(
            f"Cloudflare connection failed: {error.reason}"
        ) from error

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise QueueApiError(
            "Cloudflare returned invalid JSON"
        ) from error

    if result.get("success") is not True:
        raise QueueApiError(
            "Cloudflare API rejected request: "
            + json.dumps(
                result.get("errors", []),
                ensure_ascii=False,
            )
        )

    return result


def pull():
    response = api_post(
        "pull",
        {
            "batch_size": BATCH_SIZE,
            "visibility_timeout_ms":
                VISIBILITY_TIMEOUT_MS,
        },
    )

    result = response.get("result") or {}

    return result.get("messages") or []


def ack(lease_id):
    response = api_post(
        "ack",
        {
            "acks": [
                {
                    "lease_id": lease_id,
                }
            ],
            "retries": [],
        },
    )

    result = response.get("result") or {}

    if result.get("ackCount") != 1:
        raise QueueApiError(
            "Cloudflare did not confirm ACK"
        )


def retry(lease_id):
    api_post(
        "ack",
        {
            "acks": [],
            "retries": [
                {
                    "lease_id": lease_id,
                    "delay_seconds": 5,
                }
            ],
        },
    )


def decode_message(message):
    metadata = message.get("metadata") or {}

    content_type = (
        metadata.get("CF-Content-Type")
        or metadata.get("cf-content-type")
        or "json"
    ).lower()

    body = message.get("body")

    if not isinstance(body, str):
        raise ValueError("Queue message body is not a string")

    if content_type == "json":
        try:
            return json.loads(body)

        except (
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise ValueError(
                "Invalid JSON Queue payload"
            ) from error

    if content_type == "text":
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Invalid text Queue payload"
            ) from error

    raise ValueError(
        f"Unsupported Queue content type: {content_type}"
    )


def validate_envelope(payload):
    if not isinstance(payload, dict):
        raise ValueError(
            "Storage envelope must be an object"
        )

    if payload.get("version") != "feedback-storage-v1":
        raise ValueError(
            "Unsupported storage envelope version"
        )

    event_id = payload.get("event_id")
    channel = payload.get("channel")
    raw_text = payload.get("raw_text")
    analysis = payload.get("analysis")

    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id is invalid")

    if channel != "vk":
        raise ValueError("channel is invalid")

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("raw_text is invalid")

    if analysis is not None and not isinstance(analysis, dict):
        raise ValueError("analysis is invalid")

    # The sender (VK peer id), sent by the Worker since VK self-service onboarding.
    address = payload.get("address")
    if address is not None and not (isinstance(address, str) and address.isdigit()):
        raise ValueError("address is invalid")

    # A reaction button's payload ({"r": code, "g": game id}), passed on by the Worker.
    button = payload.get("payload")
    if button is not None and not (isinstance(button, str) and len(button) <= 200):
        raise ValueError("payload is invalid")

    # A voice message's audio link (the Worker passes it; the host may transcribe it).
    audio_url = payload.get("audio_url")
    if audio_url is not None and not (isinstance(audio_url, str) and audio_url.startswith("https://")
                                      and len(audio_url) <= 1000):
        raise ValueError("audio_url is invalid")
    audio_seconds = payload.get("audio_seconds") or 0
    if not isinstance(audio_seconds, (int, float)):
        raise ValueError("audio_seconds is invalid")

    # The id of the message the parent replied to ("Ответить" on a game), from the Worker.
    reply_to = payload.get("reply_to")
    if reply_to is not None and not (isinstance(reply_to, str) and reply_to.isdigit() and len(reply_to) <= 20):
        raise ValueError("reply_to is invalid")

    return {
        "event_id": event_id,
        "channel": channel,
        "raw_text": raw_text,
        **({"analysis": analysis} if analysis is not None else {}),
        **({"address": address} if address is not None else {}),
        **({"payload": button} if button is not None else {}),
        **({"audio_url": audio_url, "audio_seconds": audio_seconds} if audio_url is not None else {}),
        **({"reply_to": reply_to} if reply_to is not None else {}),
    }


CANARY_VERSION = "canary-v1"


def is_canary(payload):
    return (isinstance(payload, dict) and payload.get("version") == CANARY_VERSION
            and isinstance(payload.get("id"), str) and payload["id"].startswith("canary-")
            and len(payload["id"]) <= 40)


def report_canary(canary_id):
    """Tell the watcher the canary arrived; a failure only means a later alert."""
    if not heartbeat.configured():
        LOG.info("Canary received, watcher not configured: %s", canary_id)
        return
    try:
        heartbeat.canary_seen(canary_id)
        LOG.info("Canary received and reported: %s", canary_id)
    except Exception as exc:  # noqa: BLE001 - feedback storage must go on
        LOG.warning("Canary report failed: %s", type(exc).__name__)


def with_transcript(payload):
    """Replace the voice label with what was said, once per event.

    A redelivered event keeps the text stored the first time (a second transcription
    could differ by a word and storage would refuse it as a conflicting redelivery).
    """
    if "audio_url" not in payload or voice.LABEL not in payload["raw_text"]:
        if voice.LABEL in payload.get("raw_text", ""):
            LOG.info("Voice label without an audio link: nothing to transcribe")
        return payload
    con = ch.connect_db(DB_PATH)
    try:
        stored = con.execute(
            "SELECT raw_text FROM feedback WHERE channel = ? AND external_event_id = ?",
            (payload["channel"], payload["event_id"]),
        ).fetchone()
    finally:
        con.close()
    if stored is not None:
        text = stored[0]
    else:
        heard = voice.from_vk(payload["audio_url"], payload["audio_seconds"])
        text = payload["raw_text"].replace(voice.LABEL, heard, 1)
    return {**payload, "raw_text": text}


def persist(payload):
    raw_result = save_raw(
        {
            "event_id": payload["event_id"],
            "channel": payload["channel"],
            "raw_text": payload["raw_text"],
            **({"address": payload["address"]} if "address" in payload else {}),
            **({"reply_to": payload["reply_to"]} if "reply_to" in payload else {}),
        }
    )

    if raw_result.get("status") not in {
        "created",
        "existing",
    }:
        raise RuntimeError(
            "save_raw returned unexpected status"
        )

    # Analysis envelopes came from the Worker's semantic feedback agent, retired in
    # favour of one analyzer for both channels (src/feedback/insights.py). They are
    # still accepted so messages already in flight at the cutover are stored.
    if "analysis" not in payload: return raw_result, None
    analysis_result = save_analysis(
        {
            "event_id": payload["event_id"],
            "channel": payload["channel"],
            "analysis": payload["analysis"],
        }
    )

    if analysis_result.get("status") not in {
        "updated",
        "existing",
    }:
        raise RuntimeError(
            "save_analysis returned unexpected status"
        )

    return raw_result, analysis_result


def process_message(message):
    lease_id = message.get("lease_id")

    if not isinstance(lease_id, str) or not lease_id:
        raise ValueError("lease_id is missing")

    decoded = decode_message(message)

    # The watcher's hourly canary (worker/src/watch.js): proof that the queue reaches
    # the host. Dropped at once: no model call, nothing written to the database.
    if is_canary(decoded):
        ack(lease_id)
        report_canary(decoded["id"])
        return

    payload = validate_envelope(decoded)

    payload = with_transcript(payload)

    # A reaction button under a game: recorded without a model, never stored as text.
    pressed = buttons.parse_vk(payload.get("payload")) if "address" in payload else None
    if pressed is not None:
        vk_inbound.press(payload["event_id"], payload["address"], *pressed, vk_reply)
        ack(lease_id)
        LOG.info("VK reaction button handled event_id=%s", payload["event_id"])
        return

    # A VK message with its sender: onboarding and commands are answered here and
    # never become feedback. Analysis envelopes skip this (their raw event is stored).
    if "address" in payload and "analysis" not in payload:
        if vk_inbound.route(payload["event_id"], payload["address"], payload["raw_text"], vk_reply) == "handled":
            ack(lease_id)
            LOG.info("VK dialog message handled event_id=%s", payload["event_id"])
            return

    raw_result, analysis_result = persist(payload)

    ack(lease_id)

    if raw_result.get("status") == "created" and "address" in payload:
        acknowledge(payload, raw_result["feedback_id"])

    LOG.info(
        "Feedback persisted and acknowledged "
        "event_id=%s feedback_id=%s raw_status=%s "
        "analysis_status=%s",
        payload["event_id"],
        raw_result.get("feedback_id"),
        raw_result.get("status"),
        analysis_result.get("status") if analysis_result else "not_provided",
    )


def acknowledge(payload, feedback_id):
    """Tell the parent at once that the message arrived (insights replies minutes later)."""
    try:
        con = ch.connect_db(DB_PATH)
        try:
            row = con.execute("SELECT game_id FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
            text = row and reactions.ack(con, row[0], feedback_id, payload["raw_text"])
        finally:
            con.close()
        if text:
            vk_reply(payload["address"], text,
                     game_delivery.deterministic_random_id(f"ack:{payload['event_id']}"))
    except Exception:  # noqa: BLE001 - the stored feedback matters, the courtesy does not
        LOG.exception("Feedback acknowledgement failed")


def vk_reply(peer, text, random_id, keyboard=None):
    """A reply in the VK community chat (VK_TOKEN from game.env)."""
    if not keyboard:
        return game_delivery.vk_send(text, random_id, peer_id=peer)
    try:
        return game_delivery.vk_send(text, random_id, peer_id=peer, keyboard=keyboard)
    except RuntimeError as exc:
        if not str(exc).startswith("VK error 912"):
            raise
        # Bot features off in the community: ask in words instead of buttons.
        text = buttons.QUESTION_PLAIN if text == buttons.QUESTION else text
        return game_delivery.vk_send(text, random_id, peer_id=peer)


def send_outbox():
    try:
        sent = vk_inbound.drain_outbox(vk_reply)
    except Exception:  # noqa: BLE001 - the outbox must not stop feedback storage
        LOG.exception("Outbox send failed")
        return
    if sent:
        LOG.info("Outbox messages sent count=%s", sent)


def run():
    LOG.info(
        "Feedback storage consumer started"
    )
    con = ch.connect_db(DB_PATH)
    try:
        ch.migrate(con)  # additive and idempotent: tables for the VK dialog and outbox
    finally:
        con.close()

    while True:
        try:
            send_outbox()
            messages = pull()

            if not messages:
                time.sleep(
                    EMPTY_POLL_DELAY_SECONDS
                )
                continue

            for message in messages:
                lease_id = message.get("lease_id")

                try:
                    process_message(message)

                except StorageError as error:
                    LOG.error(
                        "Persistence rejected message: %s",
                        error,
                    )

                    if lease_id:
                        retry(lease_id)

                except Exception:
                    LOG.exception(
                        "Feedback message processing failed"
                    )

                    if lease_id:
                        try:
                            retry(lease_id)
                        except Exception:
                            LOG.exception(
                                "Queue retry request failed"
                            )

        except Exception:
            LOG.exception(
                "Queue polling failed"
            )
            time.sleep(ERROR_DELAY_SECONDS)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s: "
            "%(message)s"
        ),
    )

    run()


if __name__ == "__main__":
    main()
