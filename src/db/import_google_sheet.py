#!/usr/bin/env python3

import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

CSV_PATH = Path("/tmp/games.csv")
DB_PATH = Path("data/game-of-the-day.db")

EXPECTED_HEADER = [
    "ID", "Дата", "Возраст", "Название игры", "Навык",
    "Механика", "Источник", "Ссылка", "Текст для отправки",
    "Отправлено", "Обратная связь", "Время ОС",
    "Результат ОС", "ОС обработана"
]

def empty_to_none(value):
    value = value.strip()
    return value if value else None

def parse_date(value):
    value = value.strip()
    if not value:
        return None
    return datetime.strptime(value, "%d.%m.%Y").date().isoformat()

def parse_age(value):
    value = value.strip()
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group()) if match else None

with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)

    if reader.fieldnames != EXPECTED_HEADER:
        raise RuntimeError(
            f"Unexpected CSV header:\n{reader.fieldnames}"
        )

    rows = list(reader)

if len(rows) != 18:
    raise RuntimeError(f"Expected 18 games, got {len(rows)}")

if rows[0]["ID"] != "001" or rows[-1]["ID"] != "018":
    raise RuntimeError("Unexpected ID range")

con = sqlite3.connect(DB_PATH)
con.execute("PRAGMA foreign_keys = ON")

try:
    con.execute("BEGIN IMMEDIATE")

    # Импорт должен быть повторяемым.
    # Удаляем только данные, созданные этой миграцией.
    con.execute("DELETE FROM feedback")
    con.execute("DELETE FROM deliveries")
    con.execute("DELETE FROM games")
    con.execute(
        "DELETE FROM migration_meta WHERE key LIKE 'google_sheet_%'"
    )

    for row in rows:
        legacy_id = row["ID"].strip()
        game_id = int(legacy_id)

        con.execute(
            """
            INSERT INTO games (
                id,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                legacy_id,
                parse_date(row["Дата"]),
                parse_age(row["Возраст"]),
                row["Название игры"].strip(),
                empty_to_none(row["Навык"]),
                empty_to_none(row["Механика"]),
                empty_to_none(row["Источник"]),
                empty_to_none(row["Ссылка"]),
                empty_to_none(row["Текст для отправки"]),
            )
        )

        sent = row["Отправлено"].strip().casefold() == "да"

        # Каждая историческая игра получает delivery-state.
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
                "sent" if sent else "pending",
                1 if sent else 0,
                f"legacy-vk-{legacy_id}",
                parse_date(row["Дата"]) if sent else None,
            )
        )

        feedback_text = empty_to_none(row["Обратная связь"])

        if feedback_text:
            feedback_time = parse_date(row["Время ОС"])

            analysis_raw = empty_to_none(row["Результат ОС"])
            analysis_json = None

            if analysis_raw:
                # Проверяем, что JSON действительно валиден,
                # но сохраняем его содержание без смысловой переработки.
                parsed = json.loads(analysis_raw)
                analysis_json = json.dumps(
                    parsed,
                    ensure_ascii=False,
                    separators=(",", ":")
                )

            processed = (
                1
                if row["ОС обработана"].strip().casefold() == "да"
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
                )
            )

    con.execute(
        """
        INSERT INTO migration_meta(key, value)
        VALUES ('google_sheet_source_rows', ?)
        """,
        (str(len(rows)),)
    )

    con.execute(
        """
        INSERT INTO migration_meta(key, value)
        VALUES ('google_sheet_imported_at', datetime('now'))
        """
    )

    # ---------- validation before COMMIT ----------

    games = con.execute(
        "SELECT COUNT(*) FROM games"
    ).fetchone()[0]

    deliveries = con.execute(
        "SELECT COUNT(*) FROM deliveries"
    ).fetchone()[0]

    feedback = con.execute(
        "SELECT COUNT(*) FROM feedback"
    ).fetchone()[0]

    ids = [
        r[0]
        for r in con.execute(
            "SELECT legacy_id FROM games ORDER BY id"
        )
    ]

    if games != 18:
        raise RuntimeError(f"Validation failed: games={games}")

    if deliveries != 18:
        raise RuntimeError(
            f"Validation failed: deliveries={deliveries}"
        )

    if feedback != 3:
        raise RuntimeError(
            f"Validation failed: feedback={feedback}"
        )

    if ids != [f"{i:03d}" for i in range(1, 19)]:
        raise RuntimeError(
            f"Validation failed: IDs={ids}"
        )

    con.commit()

except Exception:
    con.rollback()
    raise

finally:
    con.close()

print("Migration completed successfully.")
