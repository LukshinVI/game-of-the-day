"""The preparation's database side: a child's game history, the game and its deliveries
(one transaction, idempotent per date and child) and the model attempt log."""

import json
import sqlite3
import sys
from pathlib import Path

from src.preparation import fallback


ROOT = Path(__file__).resolve().parents[2]

DB_PATH = ROOT / "data" / "game-of-the-day.db"


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_history(child_id=None):
    """Games already given to this child (all games in single-child mode)."""
    where = "WHERE child_id = ?" if child_id is not None else ""
    params = (child_id,) if child_id is not None else ()
    with connect_db() as con:
        tag_column = "mechanic_tag" if "mechanic_tag" in {r[1] for r in con.execute("PRAGMA table_info(games)")} else "NULL AS mechanic_tag"
        rows = con.execute(
            f"""
            SELECT
                {tag_column},
                legacy_id,
                game_date,
                age_months,
                title,
                skill,
                mechanics,
                source_name,
                source_url
            FROM games
            {where}
            ORDER BY game_date, id
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def game_for_date(target_date, child_id=None):
    child_filter = "AND g.child_id = ?" if child_id is not None else ""
    params = (target_date, child_id) if child_id is not None else (target_date,)
    with connect_db() as con:
        return con.execute(
            f"""
            SELECT
                g.id,
                g.legacy_id,
                g.game_date,
                g.title,
                d.status
            FROM games g
            LEFT JOIN deliveries d
                ON d.game_id = g.id
            WHERE g.game_date = ?
              {child_filter}
            ORDER BY g.id, d.id
            LIMIT 1
            """,
            params,
        ).fetchone()


def next_game_identity(con):
    max_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) FROM games"
    ).fetchone()[0]

    game_id = max_id + 1
    legacy_id = f"{game_id:03d}"

    return game_id, legacy_id


def persist_candidate(candidate, target_date, child=None, origin="model", origin_reason=None):
    """Store the game and one pending delivery per recipient, atomically.

    child=None is single-child mode: one VK delivery to VK_PEER_ID, as before.
    """
    child_id = child["id"] if child else None
    with connect_db() as con:
        con.execute("BEGIN IMMEDIATE")

        existing = con.execute(
            f"""
            SELECT legacy_id, title
            FROM games
            WHERE game_date = ?
              {"AND child_id = ?" if child else ""}
            ORDER BY id
            LIMIT 1
            """,
            (target_date, child_id) if child else (target_date,),
        ).fetchone()

        if existing:
            con.rollback()
            raise RuntimeError(
                f"На дату {target_date} уже существует игра "
                f"{existing['legacy_id']} — {existing['title']}"
            )

        game_id, legacy_id = next_game_identity(con)
        game_columns = {r[1] for r in con.execute("PRAGMA table_info(games)")}

        con.execute(
            f"""
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
                message_text{", child_id" if child else ""}
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?{", ?" if child else ""})
            """,
            (
                game_id,
                legacy_id,
                target_date,
                candidate["age_months"],
                candidate["title"].strip(),
                candidate["skill"],
                candidate["mechanics"].strip(),
                candidate["source_name"].strip(),
                candidate["source_url"].strip(),
                candidate["message_text"].strip(),
            ) + ((child_id,) if child else ()),
        )

        if "origin" in game_columns:
            con.execute(
                "UPDATE games SET origin = ?, origin_reason = ?, candidate_json = ?, mechanic_tag = ? WHERE id = ?",
                (origin, origin_reason, fallback.candidate_json(candidate), candidate.get("mechanic_tag"), game_id),
            )

        if child is None:
            targets = [("vk", None, f"vk-{target_date}-{legacy_id}")]
        else:
            if not child["recipients"]:
                con.rollback()
                raise RuntimeError(f"У ребёнка {child['name']} нет получателей")
            targets = [
                (r["channel"], r["address"], f"{r['channel']}-{target_date}-{legacy_id}-{r['address']}")
                for r in child["recipients"]
            ]

        for channel, recipient, key in targets:
            con.execute(
                """
                INSERT INTO deliveries (
                    game_id,
                    channel,
                    recipient,
                    status,
                    attempts,
                    idempotency_key
                )
                VALUES (?, ?, ?, 'pending', 0, ?)
                """,
                (game_id, channel, recipient, key),
            )

        con.commit()

    return game_id, legacy_id


RUN_COLUMNS = (
    "backend", "model", "wall_ms", "api_ms", "num_turns", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "web_searches", "cost_usd",
)


def persist_runs(target_date, child_id, dry_run, log):
    """Write the attempt log to model_runs and empty it. Never fails the preparation:
    metrics are secondary."""
    runs, log[:] = list(log), []
    if not runs:
        return
    try:
        with connect_db() as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_runs'").fetchone() is None:
                return
            for run in runs:
                con.execute(
                    f"INSERT INTO model_runs(target_date, child_id, attempt, outcome, errors_json, dry_run, "
                    f"{', '.join(RUN_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, {', '.join('?' * len(RUN_COLUMNS))})",
                    (str(target_date), child_id, run["attempt"], run["outcome"],
                     json.dumps(run["errors"], ensure_ascii=False) if run["errors"] else None,
                     int(dry_run), *(run.get(c) for c in RUN_COLUMNS)),
                )
    except sqlite3.Error as exc:
        print(f"WARN: model run metrics not saved: {exc}", file=sys.stderr)
