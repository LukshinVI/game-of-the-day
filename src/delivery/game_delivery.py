#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "game-of-the-day.db"
MOSCOW = ZoneInfo("Europe/Moscow")
VK_API_VERSION = "5.199"


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def deterministic_random_id(idempotency_key: str) -> int:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") & 0x7fffffff
    return value or 1


def latest_sendable_pending(con):
    return con.execute(
        """
        SELECT
            g.id AS game_id,
            g.legacy_id,
            g.game_date,
            g.title,
            g.message_text,
            d.id AS delivery_id,
            d.status,
            d.attempts,
            d.idempotency_key
        FROM games g
        JOIN deliveries d ON d.game_id = g.id
        WHERE d.channel = 'vk'
          AND d.status = 'pending'
          AND g.message_text IS NOT NULL
          AND TRIM(g.message_text) <> ''
        ORDER BY g.game_date DESC, g.id DESC
        LIMIT 1
        """
    ).fetchone()


def today_deliveries(con):
    today = datetime.now(MOSCOW).date().isoformat()

    return con.execute(
        """
        SELECT
            g.id AS game_id,
            g.legacy_id,
            g.game_date,
            g.title,
            g.message_text,
            d.id AS delivery_id,
            d.status,
            d.attempts,
            d.idempotency_key
        FROM games g
        JOIN deliveries d ON d.game_id = g.id
        WHERE d.channel = 'vk'
          AND g.game_date = ?
        ORDER BY g.id
        """,
        (today,)
    ).fetchall()


def send_today():
    with connect_db() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = today_deliveries(con)

        if not rows:
            con.rollback()
            print("No game for today. Nothing sent.")
            return

        if len(rows) != 1:
            con.rollback()
            raise RuntimeError(
                f"Expected exactly one delivery for today, found {len(rows)}"
            )

        game = rows[0]

        if game["status"] == "sent":
            con.rollback()
            print("Today's game is already sent. Nothing sent.")
            return

        if game["status"] == "sending":
            con.rollback()
            raise RuntimeError(
                "Today's delivery is stuck in 'sending'; manual recovery required"
            )

        if game["status"] == "failed":
            con.rollback()
            raise RuntimeError(
                "Today's delivery is in 'failed'; explicit retry required"
            )

        if game["status"] != "pending":
            con.rollback()
            raise RuntimeError(
                f"Unexpected delivery status: {game['status']}"
            )

        if not game["message_text"] or not game["message_text"].strip():
            con.rollback()
            raise RuntimeError("Today's game has empty message_text")

        if not game["idempotency_key"]:
            con.rollback()
            raise RuntimeError("Today's delivery has no idempotency_key")

        print("=== ABOUT TO SEND TODAY ===")
        print(f"ID:    {game['legacy_id']}")
        print(f"Date:  {game['game_date']}")
        print(f"Title: {game['title']}")

        random_id = deterministic_random_id(game["idempotency_key"])

        changed = con.execute(
            """
            UPDATE deliveries
            SET status = 'sending',
                attempts = attempts + 1,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'pending'
            """,
            (game["delivery_id"],)
        ).rowcount

        if changed != 1:
            con.rollback()
            raise RuntimeError("Could not acquire delivery for sending")

        con.commit()

    try:
        external_id = vk_send(game["message_text"], random_id)
    except Exception as exc:
        with connect_db() as fail_con:
            fail_con.execute(
                """
                UPDATE deliveries
                SET status = 'failed',
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'sending'
                """,
                (str(exc), game["delivery_id"])
            )
            fail_con.commit()
        raise

    with connect_db() as success_con:
        changed = success_con.execute(
            """
            UPDATE deliveries
            SET status = 'sent',
                external_message_id = ?,
                sent_at = ?,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'sending'
            """,
            (
                str(external_id),
                datetime.now(MOSCOW).isoformat(),
                game["delivery_id"],
            )
        ).rowcount

        if changed != 1:
            raise RuntimeError(
                "VK accepted message but delivery state could not be finalized"
            )

        success_con.commit()

    print(f"VK send successful. message_id={external_id}")


def print_status():
    with connect_db() as con:
        totals = con.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM deliveries
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()

        candidate = latest_sendable_pending(con)

        print("=== DELIVERY STATUS ===")
        for row in totals:
            print(f"{row['status']}: {row['count']}")

        print()
        if candidate:
            print("=== SEND-LAST CANDIDATE ===")
            print(f"ID:       {candidate['legacy_id']}")
            print(f"Date:     {candidate['game_date']}")
            print(f"Title:    {candidate['title']}")
            print(f"Status:   {candidate['status']}")
            print(f"Attempts: {candidate['attempts']}")
        else:
            print("No sendable pending games.")


def vk_send(message: str, random_id: int):
    token = os.environ.get("VK_TOKEN")
    peer_id = os.environ.get("VK_PEER_ID")

    if not token or not peer_id:
        raise RuntimeError("VK_TOKEN or VK_PEER_ID is missing")

    payload = urllib.parse.urlencode({
        "peer_id": peer_id,
        "message": message,
        "random_id": str(random_id),
        "access_token": token,
        "v": VK_API_VERSION,
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://api.vk.com/method/messages.send",
        data=payload,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")

    data = json.loads(body)

    if "error" in data:
        error = data["error"]
        raise RuntimeError(
            f"VK error {error.get('error_code')}: "
            f"{error.get('error_msg')}"
        )

    if "response" not in data:
        raise RuntimeError(f"Unexpected VK response: {data}")

    return data["response"]


def send_last():
    with connect_db() as con:
        con.execute("BEGIN IMMEDIATE")

        game = latest_sendable_pending(con)

        if not game:
            con.rollback()
            print("No sendable pending games.")
            return

        print("=== ABOUT TO SEND ===")
        print(f"ID:    {game['legacy_id']}")
        print(f"Date:  {game['game_date']}")
        print(f"Title: {game['title']}")
        print()

        random_id = deterministic_random_id(game["idempotency_key"])

        con.execute(
            """
            UPDATE deliveries
            SET status = 'sending',
                attempts = attempts + 1,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'pending'
            """,
            (game["delivery_id"],)
        )

        con.commit()

        try:
            external_id = vk_send(game["message_text"], random_id)

        except Exception as exc:
            with connect_db() as fail_con:
                fail_con.execute(
                    """
                    UPDATE deliveries
                    SET status = 'failed',
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (str(exc), game["delivery_id"])
                )
                fail_con.commit()

            raise

        with connect_db() as success_con:
            success_con.execute(
                """
                UPDATE deliveries
                SET status = 'sent',
                    external_message_id = ?,
                    sent_at = ?,
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(external_id),
                    datetime.now(MOSCOW).isoformat(),
                    game["delivery_id"],
                )
            )
            success_con.commit()

        print(f"VK send successful. message_id={external_id}")


def main():
    parser = argparse.ArgumentParser(prog="game-delivery")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("send-last")
    sub.add_parser("send-today")

    args = parser.parse_args()

    if args.command == "status":
        print_status()
    elif args.command == "send-last":
        send_last()
    elif args.command == "send-today":
        send_today()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
