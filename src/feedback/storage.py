"""Feedback persistence: raw events and their analysis in SQLite.

Called in-process by the queue consumer. Idempotent by (channel, event id);
failures raise StorageError(status, code, message), where the status is a
classification (404 not found, 409 conflict, 400 invalid input), not an HTTP reply.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(
    os.environ.get(
        "FEEDBACK_DB_PATH",
        str(ROOT / "data" / "game-of-the-day.db"),
    )
)


class StorageError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect_db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def require_string(data, field, max_length):
    value = data.get(field)

    if not isinstance(value, str):
        raise StorageError(
            400,
            "invalid_request",
            f"{field} must be a string",
        )

    value = value.strip()

    if not value:
        raise StorageError(
            400,
            "invalid_request",
            f"{field} must not be empty",
        )

    if len(value) > max_length:
        raise StorageError(
            400,
            "invalid_request",
            f"{field} is too long",
        )

    return value


def resolve_game(con, data):
    legacy_id = data.get("game_legacy_id")

    if legacy_id is not None:
        if not isinstance(legacy_id, str):
            raise StorageError(
                400,
                "invalid_request",
                "game_legacy_id must be a string",
            )

        legacy_id = legacy_id.strip()

        row = con.execute(
            """
            SELECT id, legacy_id
            FROM games
            WHERE legacy_id = ?
            """,
            (legacy_id,),
        ).fetchone()

        if not row:
            raise StorageError(
                404,
                "game_not_found",
                "Game not found",
            )

        return row

    game_id = data.get("game_id")

    if not isinstance(game_id, int) or isinstance(game_id, bool):
        raise StorageError(
            400,
            "invalid_request",
            "game_id or game_legacy_id is required",
        )

    row = con.execute(
        """
        SELECT id, legacy_id
        FROM games
        WHERE id = ?
        """,
        (game_id,),
    ).fetchone()

    if not row:
        raise StorageError(
            404,
            "game_not_found",
            "Game not found",
        )

    return row



def resolve_feedback_target(con, channel="vk"):
    """
    Resolve feedback without an explicit game to the newest successfully
    sent game that has no feedback yet (the legacy Google Sheets rule),
    among games delivered on the channel the feedback came from.

    With several children, games for a child reached only on another channel
    must not absorb this feedback. This is exact while each channel serves one
    child; a channel shared by several children needs the sender address in the
    event (not yet sent by the Worker).

    Explicit game identity is handled by resolve_game().
    """

    row = con.execute(
        """
        SELECT g.id
        FROM games AS g
        JOIN deliveries AS d
          ON d.game_id = g.id
        WHERE d.status = 'sent'
          AND d.channel = ?
          AND NOT EXISTS (
              SELECT 1
              FROM feedback AS f
              WHERE f.game_id = g.id
          )
        ORDER BY
            g.game_date DESC,
            g.id DESC
        LIMIT 1
        """,
        (channel,),
    ).fetchone()

    if row is None:
        raise StorageError(
            409,
            "feedback_target_not_found",
            "No sent game without feedback was found",
        )

    return row["id"]


def save_raw(data):
    event_id = require_string(data, "event_id", 256)
    channel = require_string(data, "channel", 32)
    raw_text = require_string(data, "raw_text", 10000)

    received_at = data.get("received_at")
    if received_at is not None:
        if not isinstance(received_at, str) or not received_at.strip():
            raise StorageError(
                400,
                "invalid_request",
                "received_at must be a non-empty string",
            )
        received_at = received_at.strip()
    else:
        received_at = now_iso()

    con = connect_db()

    try:
        con.execute("BEGIN IMMEDIATE")

        # Idempotency must be checked BEFORE target resolution.
        #
        # A Queue retry can arrive after a newer game has been sent.
        # The original event must remain attached to its original game.
        existing = con.execute(
            """
            SELECT id, game_id, raw_text, processed
            FROM feedback
            WHERE channel = ?
              AND external_event_id = ?
            """,
            (channel, event_id),
        ).fetchone()

        if existing:
            if existing["raw_text"] != raw_text:
                raise StorageError(
                    409,
                    "idempotency_conflict",
                    "Event ID already exists with different payload",
                )

            game_context = con.execute(
                """
                SELECT
                    legacy_id,
                    title,
                    skill,
                    mechanics
                FROM games
                WHERE id = ?
                """,
                (existing["game_id"],),
            ).fetchone()

            game_payload = None

            if game_context is not None:
                game_payload = {
                    "id": str(game_context["legacy_id"]),
                    "title": game_context["title"] or "",
                    "skill": game_context["skill"] or "",
                    "mechanic": game_context["mechanics"] or "",
                }

            con.commit()

            return {
                "status": "existing",
                "feedback_id": existing["id"],
                "event_id": event_id,
                "processed": bool(existing["processed"]),
                "game": game_payload,
            }

        # Explicit game identity wins. Without one, preserve the
        # legacy production rule: newest sent game without feedback.
        explicit_game_id = data.get("game_id")
        explicit_legacy_id = data.get("game_legacy_id")

        if explicit_game_id is not None or explicit_legacy_id is not None:
            game = resolve_game(con, data)
            game_id = game["id"]
        else:
            game_id = resolve_feedback_target(con, channel)

        game_context = con.execute(
            """
            SELECT
                id,
                legacy_id,
                title,
                skill,
                mechanics
            FROM games
            WHERE id = ?
            """,
            (game_id,),
        ).fetchone()

        if game_context is None:
            raise StorageError(
                404,
                "game_not_found",
                "Game not found",
            )

        game_payload = {
            "id": str(game_context["legacy_id"]),
            "title": game_context["title"] or "",
            "skill": game_context["skill"] or "",
            "mechanic": game_context["mechanics"] or "",
        }

        try:
            cur = con.execute(
                """
                INSERT INTO feedback (
                    game_id,
                    channel,
                    external_event_id,
                    raw_text,
                    received_at,
                    processed
                )
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (
                    game_id,
                    channel,
                    event_id,
                    raw_text,
                    received_at,
                ),
            )
        except sqlite3.IntegrityError:
            # Defensive concurrent-duplicate handling.
            existing = con.execute(
                """
                SELECT id, raw_text, processed
                FROM feedback
                WHERE channel = ?
                  AND external_event_id = ?
                """,
                (channel, event_id),
            ).fetchone()

            if existing:
                if existing["raw_text"] != raw_text:
                    raise StorageError(
                        409,
                        "idempotency_conflict",
                        "Event ID already exists with different payload",
                    )

                con.commit()

                return {
                    "status": "existing",
                    "feedback_id": existing["id"],
                    "event_id": event_id,
                "processed": bool(existing["processed"]),
                }

            raise

        feedback_id = cur.lastrowid
        con.commit()

        return {
            "status": "created",
            "feedback_id": feedback_id,
            "event_id": event_id,
            "processed": False,
            "game": game_payload,
        }

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def same_analysis(stored_json, analysis):
    """Semantic equality: key order is irrelevant, but true != 1."""
    try:
        stored = json.loads(stored_json)
    except (TypeError, ValueError):
        return False

    return canonical_json(stored) == canonical_json(analysis)


def save_analysis(data):
    event_id = require_string(data, "event_id", 256)
    channel = require_string(data, "channel", 32)

    analysis = data.get("analysis")

    if not isinstance(analysis, dict):
        raise StorageError(
            400,
            "invalid_request",
            "analysis must be a JSON object",
        )

    analysis_json = json.dumps(
        analysis,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    analyzed_at = data.get("analyzed_at")
    if analyzed_at is not None:
        if not isinstance(analyzed_at, str) or not analyzed_at.strip():
            raise StorageError(
                400,
                "invalid_request",
                "analyzed_at must be a non-empty string",
            )
        analyzed_at = analyzed_at.strip()
    else:
        analyzed_at = now_iso()

    con = connect_db()

    try:
        con.execute("BEGIN IMMEDIATE")

        row = con.execute(
            """
            SELECT id, analysis_json, processed
            FROM feedback
            WHERE channel = ?
              AND external_event_id = ?
            """,
            (channel, event_id),
        ).fetchone()

        if not row:
            raise StorageError(
                404,
                "feedback_not_found",
                "Raw feedback must be saved before analysis",
            )

        if row["processed"]:
            if same_analysis(row["analysis_json"], analysis):
                con.commit()
                return {
                    "status": "existing",
                    "feedback_id": row["id"],
                    "event_id": event_id,
                }

            raise StorageError(
                409,
                "analysis_conflict",
                "Feedback already contains different analysis",
            )

        con.execute(
            """
            UPDATE feedback
            SET analysis_json = ?,
                processed = 1,
                analyzed_at = ?
            WHERE id = ?
            """,
            (
                analysis_json,
                analyzed_at,
                row["id"],
            ),
        )

        con.commit()

        return {
            "status": "updated",
            "feedback_id": row["id"],
            "event_id": event_id,
        }

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
