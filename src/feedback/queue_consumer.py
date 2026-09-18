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

from src.feedback.feedback_api import ApiError, save_analysis, save_raw


LOG = logging.getLogger("game-feedback-consumer")

API_ROOT = "https://api.cloudflare.com/client/v4"

ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]
QUEUE_ID = os.environ["CF_STORAGE_QUEUE_ID"]
ENRICHED_QUEUE_ID = os.environ["CF_ENRICHED_QUEUE_ID"]
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


def push_url(queue_id): return f"{API_ROOT}/accounts/{ACCOUNT_ID}/queues/{queue_id}/messages"

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


def push_enriched(payload):
    return api_post(
        None,
        {
            "body": payload,
            "content_type": "json",
        },
        queue_id=ENRICHED_QUEUE_ID,
        url=push_url(ENRICHED_QUEUE_ID),
    )


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

    return {
        "event_id": event_id,
        "channel": channel,
        "raw_text": raw_text,
        **({"analysis": analysis} if analysis is not None else {}),
    }


def persist(payload):
    raw_result = save_raw(
        {
            "event_id": payload["event_id"],
            "channel": payload["channel"],
            "raw_text": payload["raw_text"],
        }
    )

    if raw_result.get("status") not in {
        "created",
        "existing",
    }:
        raise RuntimeError(
            "save_raw returned unexpected status"
        )

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

    payload = validate_envelope(
        decode_message(message)
    )

    raw_result, analysis_result = persist(payload)

    if analysis_result is None:
        game = raw_result.get("game")

        if not isinstance(game, dict):
            raise RuntimeError(
                "save_raw returned no game context"
            )

        push_result = push_enriched(
            {
                "version": "feedback-enriched-v1",
                "event_id": payload["event_id"],
                "channel": payload["channel"],
                "raw_text": payload["raw_text"],
                "game": game,
            }
        )
        push_metrics = (
            (push_result.get("result") or {})
            .get("metadata", {})
            .get("metrics", {})
        )
        LOG.info(
            "Enriched feedback queued "
            "event_id=%s backlog_count=%s backlog_bytes=%s",
            payload["event_id"],
            push_metrics.get("backlog_count"),
            push_metrics.get("backlog_bytes"),
        )

    ack(lease_id)

    LOG.info(
        "Feedback persisted and acknowledged "
        "event_id=%s feedback_id=%s raw_status=%s "
        "analysis_status=%s",
        payload["event_id"],
        raw_result.get("feedback_id"),
        raw_result.get("status"),
        analysis_result.get("status") if analysis_result else "not_provided",
    )


def run():
    LOG.info(
        "Feedback storage consumer started"
    )

    while True:
        try:
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

                except ApiError as error:
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
