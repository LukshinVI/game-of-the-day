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
from datetime import datetime, timedelta, timezone

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "game-of-the-day.db"
MOSCOW = ZoneInfo("Europe/Moscow")
VK_API_VERSION = "5.199"

# A delivery left in 'sending' longer than this is treated as interrupted (the
# process died between acquiring it and recording the VK result). A younger one
# may still be in flight in another process and is left alone.
SENDING_STALE_AFTER = timedelta(minutes=10)
# How many recent messages of the dialog are searched for our random_id.
VK_RECONCILE_WINDOW = 50


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
            d.idempotency_key,
            d.updated_at
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
            outcome = recover_stuck_sending(game)
            if outcome == "sent":
                print("Today's game was already delivered (recovered from VK). Nothing sent.")
                return
            if outcome == "failed":
                # Even with --retry-failed: the owner asked to retry a failure they
                # had seen, not this one. The new state and reason are shown first.
                raise RuntimeError(
                    "An interrupted delivery was not found in VK and is now 'failed'; "
                    "explicit retry required (send-today --retry-failed)"
                )
            raise RuntimeError(
                "Today's delivery is in 'sending' and may still be in flight; "
                "not touching it (manual recovery if it stays so)"
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


def is_stale(updated_at, now=None):
    """updated_at is SQLite CURRENT_TIMESTAMP (UTC, 'YYYY-MM-DD HH:MM:SS')."""
    if not updated_at:
        return True
    started = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - started >= SENDING_STALE_AFTER


def recover_stuck_sending(game, now=None):
    """Resolve an interrupted 'sending' delivery without guessing (issue #28).

    VK keeps the random_id of messages we sent, and our random_id is derived from
    the idempotency key. So the dialog history answers the only question that
    matters: did the message go out?

    - found      -> 'sent' with the real VK message id (no second message);
    - not found  -> 'failed' with the reason; a resend stays an explicit decision;
    - too recent -> left alone, another process may still be sending.
    Returns 'sent', 'failed' or 'in-flight'.
    """
    if not is_stale(game["updated_at"], now):
        return "in-flight"

    random_id = deterministic_random_id(game["idempotency_key"])
    message_id = vk_find_sent(random_id)

    with connect_db() as con:
        if message_id is not None:
            changed = con.execute(
                """
                UPDATE deliveries
                SET status = 'sent',
                    external_message_id = ?,
                    sent_at = COALESCE(sent_at, ?),
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'sending'
                """,
                (str(message_id), datetime.now(MOSCOW).isoformat(), game["delivery_id"]),
            ).rowcount
            outcome = "sent"
        else:
            changed = con.execute(
                """
                UPDATE deliveries
                SET status = 'failed',
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'sending'
                """,
                ("interrupted while sending; not found in VK history", game["delivery_id"]),
            ).rowcount
            outcome = "failed"
        con.commit()

    if changed != 1:
        raise RuntimeError("Delivery state changed during recovery; not touching it")

    if outcome == "sent":
        send_ops_notification(
            "info",
            "Зависшая отправка восстановлена",
            f"ID: {game['legacy_id']}\nДата: {game['game_date']}\n"
            f"Сообщение найдено в VK (message_id {message_id}), повторно не отправлялось.",
        )
    else:
        send_ops_notification(
            "critical",
            "Зависшая отправка: сообщения нет в VK",
            f"ID: {game['legacy_id']}\nДата: {game['game_date']}\n"
            "Отправка прервалась, в VK сообщения нет. Статус: failed. "
            "Повторить: /retry в ops-боте.",
        )
    return outcome


def recover_command():
    """Recover today's interrupted delivery, if any. Safe to run at any time."""
    with connect_db() as con:
        rows = today_deliveries(con)
    stuck = [row for row in rows if row["status"] == "sending"]
    if not stuck:
        print("Nothing to recover.")
        return
    for game in stuck:
        print(f"{game['legacy_id']}: {recover_stuck_sending(game)}")


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


def vk_find_sent(random_id):
    """Return the VK message id of our message with this random_id, or None."""
    token = os.environ.get("VK_TOKEN")
    peer_id = os.environ.get("VK_PEER_ID")

    if not token or not peer_id:
        raise RuntimeError("VK_TOKEN or VK_PEER_ID is missing")

    payload = urllib.parse.urlencode({
        "peer_id": peer_id,
        "count": str(VK_RECONCILE_WINDOW),
        "access_token": token,
        "v": VK_API_VERSION,
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://api.vk.com/method/messages.getHistory",
        data=payload,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))

    if "error" in data:
        error = data["error"]
        raise RuntimeError(
            f"VK error {error.get('error_code')}: {error.get('error_msg')}"
        )

    for item in data.get("response", {}).get("items", []):
        if item.get("out") == 1 and item.get("random_id") == random_id:
            return item["id"]
    return None


def main():
    parser = argparse.ArgumentParser(prog="game-delivery")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("state")
    sub.add_parser("recover", help="resolve an interrupted 'sending' delivery using VK history")
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
    elif args.command == "recover":
        recover_command()
    elif args.command == "send-today":
        send_today(retry_failed=args.retry_failed)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
