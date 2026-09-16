#!/usr/bin/env python3

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_CSV = Path("/tmp/games-new.csv")
DB_PATH = Path("data/game-of-the-day.db")

EXPECTED_HEADER = [
    "ID", "Дата", "Возраст", "Название игры", "Навык",
    "Механика", "Источник", "Ссылка", "Текст для отправки",
    "Отправлено", "Обратная связь", "Время ОС",
    "Результат ОС", "ОС обработана"
]

GAME_COMPARE_FIELDS = {
    "game_date": "Дата",
    "age_months": "Возраст",
    "title": "Название игры",
    "skill": "Навык",
    "mechanics": "Механика",
    "source_name": "Источник",
    "source_url": "Ссылка",
    "message_text": "Текст для отправки",
}


def empty_to_none(value):
    value = (value or "").strip()
    return value if value else None


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%d.%m.%Y").date().isoformat()


def parse_age(value):
    value = (value or "").strip()
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group()) if match else None


def csv_game(row):
    return {
        "legacy_id": row["ID"].strip(),
        "game_date": parse_date(row["Дата"]),
        "age_months": parse_age(row["Возраст"]),
        "title": row["Название игры"].strip(),
        "skill": empty_to_none(row["Навык"]),
        "mechanics": empty_to_none(row["Механика"]),
        "source_name": empty_to_none(row["Источник"]),
        "source_url": empty_to_none(row["Ссылка"]),
        "message_text": empty_to_none(row["Текст для отправки"]),
    }


def load_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames != EXPECTED_HEADER:
            raise RuntimeError(
                f"Unexpected CSV header:\n{reader.fieldnames}"
            )

        rows = list(reader)

    if not rows:
        raise RuntimeError("CSV contains no games")

    ids = [row["ID"].strip() for row in rows]
    expected_ids = [f"{i:03d}" for i in range(1, len(rows) + 1)]

    if ids != expected_ids:
        raise RuntimeError(
            f"CSV IDs are not contiguous from 001: {ids}"
        )

    return rows


def compare_existing(db_row, source):
    differences = []

    for field in GAME_COMPARE_FIELDS:
        db_value = db_row[field]
        source_value = source[field]

        if db_value != source_value:
            differences.append(
                (
                    field,
                    db_value,
                    source_value,
                )
            )

    return differences


def main():
    parser = argparse.ArgumentParser(prog="sync-google-sheet")
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag the command is dry-run.",
    )
    args = parser.parse_args()

    rows = load_csv(args.csv)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    try:
        con.execute("BEGIN IMMEDIATE")

        inserts = []
        delivery_updates = []
        feedback_inserts = []

        for row in rows:
            source = csv_game(row)
            legacy_id = source["legacy_id"]

            existing = con.execute(
                """
                SELECT *
                FROM games
                WHERE legacy_id = ?
                """,
                (legacy_id,),
            ).fetchone()

            if existing:
                differences = compare_existing(existing, source)

                if differences:
                    print(
                        f"ERROR: existing game {legacy_id} differs "
                        "from Google snapshot:"
                    )
                    for field, db_value, source_value in differences:
                        print(f"  {field}:")
                        print(f"    DB:     {db_value!r}")
                        print(f"    Google: {source_value!r}")

                    raise RuntimeError(
                        f"Historical mismatch for {legacy_id}"
                    )

                game_id = existing["id"]

            else:
                inserts.append(source)

                if args.apply:
                    cur = con.execute(
                        """
                        INSERT INTO games (
                            legacy_id,
                            game_date,
                            age_months,
                            title,
                            skill,
                            mechanics,
                            source_name,
                            source_url,
                            message_text
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source["legacy_id"],
                            source["game_date"],
                            source["age_months"],
                            source["title"],
                            source["skill"],
                            source["mechanics"],
                            source["source_name"],
                            source["source_url"],
                            source["message_text"],
                        ),
                    )
                    game_id = cur.lastrowid
                else:
                    game_id = None

            sent = row["Отправлено"].strip().casefold() == "да"

            if game_id is not None:
                delivery = con.execute(
                    """
                    SELECT *
                    FROM deliveries
                    WHERE game_id = ?
                      AND channel = 'vk'
                    """,
                    (game_id,),
                ).fetchone()

                desired_status = "sent" if sent else "pending"
                desired_attempts = 1 if sent else 0
                desired_sent_at = source["game_date"] if sent else None

                if not delivery:
                    if args.apply:
                        con.execute(
                            """
                            INSERT INTO deliveries (
                                game_id,
                                channel,
                                status,
                                attempts,
                                idempotency_key,
                                sent_at
                            )
                            VALUES (?, 'vk', ?, ?, ?, ?)
                            """,
                            (
                                game_id,
                                desired_status,
                                desired_attempts,
                                f"legacy-vk-{legacy_id}",
                                desired_sent_at,
                            ),
                        )
                elif (
                    delivery["status"] != desired_status
                    or delivery["attempts"] != desired_attempts
                    or delivery["sent_at"] != desired_sent_at
                ):
                    delivery_updates.append(legacy_id)

                    if args.apply:
                        con.execute(
                            """
                            UPDATE deliveries
                            SET status = ?,
                                attempts = ?,
                                sent_at = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (
                                desired_status,
                                desired_attempts,
                                desired_sent_at,
                                delivery["id"],
                            ),
                        )

            feedback_text = empty_to_none(row["Обратная связь"])

            if feedback_text and game_id is not None:
                existing_feedback = con.execute(
                    """
                    SELECT id
                    FROM feedback
                    WHERE game_id = ?
                      AND channel = 'vk'
                      AND raw_text = ?
                    """,
                    (game_id, feedback_text),
                ).fetchone()

                if not existing_feedback:
                    feedback_inserts.append(legacy_id)

                    if args.apply:
                        feedback_time = parse_date(row["Время ОС"])
                        analysis_raw = empty_to_none(row["Результат ОС"])
                        analysis_json = None

                        if analysis_raw:
                            parsed = json.loads(analysis_raw)
                            analysis_json = json.dumps(
                                parsed,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )

                        processed = (
                            1
                            if row["ОС обработана"]
                            .strip()
                            .casefold() == "да"
                            else 0
                        )

                        con.execute(
                            """
                            INSERT INTO feedback (
                                game_id,
                                channel,
                                raw_text,
                                received_at,
                                analysis_json,
                                processed,
                                analyzed_at
                            )
                            VALUES (?, 'vk', ?, ?, ?, ?, ?)
                            """,
                            (
                                game_id,
                                feedback_text,
                                feedback_time,
                                analysis_json,
                                processed,
                                feedback_time if analysis_json else None,
                            ),
                        )

        print("=== SYNC PLAN ===")
        print(f"Snapshot rows:      {len(rows)}")
        print(
            "New games:          "
            + (", ".join(g["legacy_id"] for g in inserts) or "none")
        )
        print(
            "Delivery updates:   "
            + (", ".join(delivery_updates) or "none")
        )
        print(
            "Feedback additions: "
            + (", ".join(feedback_inserts) or "none")
        )

        if not args.apply:
            con.rollback()
            print()
            print("DRY RUN: database NOT modified.")
            return

        con.execute(
            """
            INSERT INTO migration_meta(key, value)
            VALUES ('google_sheet_source_rows', ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (str(len(rows)),),
        )

        con.execute(
            """
            INSERT INTO migration_meta(key, value)
            VALUES ('google_sheet_imported_at', datetime('now'))
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """
        )

        con.commit()

        print()
        print("SYNC APPLIED.")

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
