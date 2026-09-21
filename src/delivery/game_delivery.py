#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
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


def send_ops_notification(level, title, details):
    project_root = Path(__file__).resolve().parents[2]
    notifier = project_root / "src" / "ops" / "notifier.py"

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(notifier),
                level,
                title,
                "--details",
                details,
            ],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        print(
            f"WARNING: Ops notification failed: {exc}",
            file=sys.stderr,
        )
        return

    if result.returncode != 0:
        print(
            "WARNING: Ops notification failed:\n"
            + result.stderr[-2000:],
            file=sys.stderr,
        )
    else:
        print(result.stdout.strip())


def send_delivery_success_alert(game, external_id):
    details = (
        f"ID: {game['legacy_id']}\n"
        f"Дата: {game['game_date']}\n"
        f"Название: {game['title']}\n"
        "Канал: VK\n"
        f"VK message_id: {external_id}\n"
        f"Попыток отправки: {game['attempts'] + 1}"
    )

    send_ops_notification(
        "info",
        "Игра отправлена",
        details,
    )


def send_delivery_failure_alert(game, error):
    details = (
        f"ID: {game['legacy_id']}\n"
        f"Дата: {game['game_date']}\n"
        f"Название: {game['title']}\n"
        "Канал: VK\n"
        f"Попыток отправки: {game['attempts'] + 1}\n"
        f"Ошибка: {error}"
    )

    send_ops_notification(
        "critical",
        "Игра не отправлена",
        details,
    )


def send_today(retry_failed=False):
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

        if retry_failed and game["status"] != "failed":
            con.rollback()
            raise RuntimeError(
                f"Nothing to retry: today's delivery is '{game['status']}'"
            )

        if game["status"] == "failed" and not retry_failed:
            con.rollback()
            raise RuntimeError(
                "Today's delivery is in 'failed'; explicit retry required "
                "(send-today --retry-failed)"
            )

        if game["status"] not in ("pending", "failed"):
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
              AND status IN ('pending', 'failed')
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

        send_delivery_failure_alert(
            game,
            str(exc),
        )
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

    send_delivery_success_alert(
        game,
        external_id,
    )


def print_state():
    """One word for machines (the manual bot): none, pending, sending, sent, failed or ambiguous."""
    with connect_db() as con:
        rows = today_deliveries(con)

    if not rows:
        print("none")
    elif len(rows) != 1:
        print("ambiguous")
    else:
        print(rows[0]["status"])


def print_status():
    with connect_db() as con:
        today = datetime.now(MOSCOW).date().isoformat()

        today_rows = con.execute(
            """
            SELECT
                g.legacy_id,
                g.game_date,
                g.title,
                d.status,
                d.attempts,
                d.sent_at,
                d.last_error
            FROM games g
            JOIN deliveries d ON d.game_id = g.id
            WHERE d.channel = 'vk'
              AND g.game_date = ?
            ORDER BY g.id
            """,
            (today,)
        ).fetchall()

        historical_pending = con.execute(
            """
            SELECT
                g.legacy_id,
                g.game_date,
                g.title
            FROM games g
            JOIN deliveries d ON d.game_id = g.id
            WHERE d.channel = 'vk'
              AND d.status = 'pending'
              AND g.game_date < ?
            ORDER BY g.game_date, g.id
            """,
            (today,)
        ).fetchall()

        print("=== TODAY'S DELIVERY ===")

        if not today_rows:
            print("No game for today.")
        else:
            for row in today_rows:
                print(f"ID:       {row['legacy_id']}")
                print(f"Date:     {row['game_date']}")
                print(f"Title:    {row['title']}")
                print(f"Status:   {row['status']}")
                print(f"Attempts: {row['attempts']}")
                print(f"Sent at:  {row['sent_at'] or '-'}")
                if row["last_error"]:
                    print(f"Error:    {row['last_error']}")

        print()
        print("=== HISTORICAL UNCONFIRMED ===")
        print(f"Count: {len(historical_pending)}")

        for row in historical_pending:
            print(
                f"{row['legacy_id']} | "
                f"{row['game_date']} | "
                f"{row['title']}"
            )



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


def main():
    parser = argparse.ArgumentParser(prog="game-delivery")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("state")
    send = sub.add_parser("send-today")
    send.add_argument(
        "--retry-failed",
        action="store_true",
        help="retry a delivery that ended in 'failed' (same VK random_id, so VK deduplicates)",
    )

    args = parser.parse_args()

    if args.command == "status":
        print_status()
    elif args.command == "state":
        print_state()
    elif args.command == "send-today":
        send_today(retry_failed=args.retry_failed)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
