#!/usr/bin/env python3
"""Children and their delivery recipients (issues #20 and #21).

Product state belongs to a child, not to a messenger dialog: a child has a birth
date (the age of every activity is computed from it) and any number of
recipients, each a channel plus an address (VK peer id, Telegram chat id).

Until the first child is added the system runs in single-child mode, exactly as
before: CHILD_BIRTH_DATE and VK_PEER_ID from the environment.

    python3 src/db/children.py migrate
    python3 src/db/children.py add --name "Старший" --birth-date 2025-09-01 --adopt-existing
    python3 src/db/children.py add-recipient --child 1 --channel vk --address 2488584
    python3 src/db/children.py add-recipient --child 2 --channel telegram --address 123456789
    python3 src/db/children.py rename --child 1 --name "Миша"
    python3 src/db/children.py list
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"

CHANNELS = ("vk", "telegram")
SCHEMA_VERSION = 10

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY,
    child_id INTEGER NOT NULL REFERENCES children(id),
    channel TEXT NOT NULL CHECK(channel IN ('vk', 'telegram')),
    address TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(child_id, channel, address)
);
"""

# Self-service onboarding in the product Telegram bot (src/bot/product_bot.py):
# one-time invites (only the hash is stored), members (a chat that joined with an
# invite), the in-progress questionnaire (a bot restart loses nothing) and
# owner-facing events.
MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    used_by TEXT
);
CREATE TABLE IF NOT EXISTS members (
    chat_id TEXT PRIMARY KEY,
    invite_id INTEGER REFERENCES invites(id),
    display_name TEXT,
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    blocked_at TEXT
);
CREATE TABLE IF NOT EXISTS onboarding (
    chat_id TEXT PRIMARY KEY REFERENCES members(chat_id),
    step TEXT NOT NULL CHECK(step IN ('name', 'birth_date', 'confirm')),
    name TEXT,
    birth_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Owner-facing events (joins, subscriptions, pauses, limits). The product bot only
-- writes them; the ops bot forwards them, so the public bot holds no ops credentials.
CREATE TABLE IF NOT EXISTS user_events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    chat_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    notified_at TEXT
);
"""


# One row per model attempt in daily preparation: latency, tokens, cost, outcome and
# the validator's rejection reasons (src/ops/llm_report.py summarises them).
MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    target_date TEXT NOT NULL,
    child_id INTEGER,
    attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('pass', 'rejected', 'error')),
    backend TEXT,
    model TEXT,
    wall_ms INTEGER,
    api_ms INTEGER,
    num_turns INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    web_searches INTEGER,
    cost_usd REAL,
    errors_json TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0
);
"""


# Warm reserve for graceful degradation: validated games kept per age, so a child of
# any age has a library game even before the family has any history.
MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS reserve_games (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    age_months INTEGER NOT NULL,
    skill TEXT NOT NULL,
    title TEXT NOT NULL,
    mechanics TEXT,
    source_name TEXT,
    source_url TEXT NOT NULL,
    message_text TEXT NOT NULL,
    candidate_json TEXT,
    made_by TEXT,
    UNIQUE(title, age_months)
);
"""


# Feedback insights (src/feedback/insights.py): facts extracted from feedback, each with
# a verbatim quote, and the per-child profile code aggregates from them.
MIGRATION_7 = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    feedback_id INTEGER NOT NULL REFERENCES feedback(id),
    child_id INTEGER,
    game_id INTEGER,
    kind TEXT NOT NULL CHECK(kind IN ('outcome', 'difficulty', 'skill', 'like', 'dislike')),
    value TEXT NOT NULL,
    skill TEXT,
    evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS child_profiles (
    child_id INTEGER PRIMARY KEY,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    profile_json TEXT NOT NULL
);
"""


# Wishes about the service itself (not about the child): from feedback or /idea.
MIGRATION_8 = """
CREATE TABLE IF NOT EXISTS wishes (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL CHECK(source IN ('feedback', 'command')),
    feedback_id INTEGER,
    child_id INTEGER,
    chat_id TEXT,
    text TEXT NOT NULL,
    evidence TEXT
);
"""


# Sunday digests already sent (src/ops/weekly_digest.py), one per child, recipient, week.
MIGRATION_9 = """
CREATE TABLE IF NOT EXISTS digests (
    child_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    week TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (child_id, channel, address, week)
);
"""


def connect_db(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def migrate(con):
    """Bring an existing database to SCHEMA_VERSION. Idempotent and additive only."""
    con.executescript(MIGRATION_2)
    con.executescript(MIGRATION_3)
    con.executescript(MIGRATION_4)
    con.executescript(MIGRATION_6)
    con.executescript(MIGRATION_7)
    con.executescript(MIGRATION_8)
    con.executescript(MIGRATION_9)
    # Mechanic tags (src/preparation/mechanics.py): similar ideas, not just equal titles.
    for table in ("games", "reserve_games"):
        if "mechanic_tag" not in columns(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN mechanic_tag TEXT")
    for column in ("insights_at", "insights_status", "reply_sent_at"):
        if column not in columns(con, "feedback"):
            con.execute(f"ALTER TABLE feedback ADD COLUMN {column} TEXT")
    if "is_test" not in columns(con, "children"):
        # A test child (e.g. the VK test dialog) gets real games and deliveries, but its
        # games never absorb feedback that arrives without an explicit game.
        con.execute("ALTER TABLE children ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
    # Where a game came from (graceful degradation): the primary model, the fallback
    # model or the library of already validated games; the full candidate is kept so
    # library games can be re-rendered later.
    for column, ddl in (("origin", "TEXT NOT NULL DEFAULT 'model'"), ("origin_reason", "TEXT"),
                        ("candidate_json", "TEXT")):
        if column not in columns(con, "games"):
            con.execute(f"ALTER TABLE games ADD COLUMN {column} {ddl}")
    if "child_id" not in columns(con, "games"):
        con.execute("ALTER TABLE games ADD COLUMN child_id INTEGER REFERENCES children(id)")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_games_child_date "
        "ON games(child_id, game_date) WHERE child_id IS NOT NULL"
    )
    con.execute(
        "INSERT INTO migration_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    con.commit()


def is_migrated(con):
    return "child_id" in columns(con, "games") and bool(columns(con, "children"))


def has_children(con):
    """True once any child was ever added: from then on there is no single-child fallback."""
    return is_migrated(con) and con.execute("SELECT 1 FROM children LIMIT 1").fetchone() is not None


def active_children(con, include_test=False):
    """Active children with their active recipients; [] means single-child mode.

    Test children are left out unless asked for: they get games only from a manual
    run (game_preparation.py prepare --child ID), never from the daily schedule."""
    if not is_migrated(con):
        return []
    test_filter = "" if include_test else " AND is_test = 0"
    children = [dict(row) for row in con.execute(
        f"SELECT id, name, birth_date, is_test FROM children WHERE active = 1{test_filter} ORDER BY id"
    )]
    for child in children:
        child["recipients"] = [dict(row) for row in con.execute(
            "SELECT channel, address FROM recipients "
            "WHERE child_id = ? AND active = 1 ORDER BY id",
            (child["id"],),
        )]
    return children


def add_child(con, name, birth_date, adopt_existing=False, is_test=False):
    date.fromisoformat(birth_date)  # validates YYYY-MM-DD
    if not name.strip():
        raise ValueError("name must not be empty")
    child_id = con.execute(
        "INSERT INTO children(name, birth_date, is_test) VALUES (?, ?, ?)",
        (name.strip(), birth_date, int(is_test)),
    ).lastrowid
    if adopt_existing:
        con.execute("UPDATE games SET child_id = ? WHERE child_id IS NULL", (child_id,))
    con.commit()
    return child_id


def add_recipient(con, child_id, channel, address):
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    address = str(address).strip()
    if not address.lstrip("-").isdigit():
        raise ValueError("address must be a numeric peer/chat id")
    if con.execute("SELECT 1 FROM children WHERE id = ?", (child_id,)).fetchone() is None:
        raise ValueError(f"no child with id {child_id}")
    con.execute(
        "INSERT INTO recipients(child_id, channel, address) VALUES (?, ?, ?)",
        (child_id, channel, address),
    )
    con.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="children")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--birth-date", required=True, help="YYYY-MM-DD")
    add.add_argument(
        "--adopt-existing", action="store_true",
        help="attach games recorded before children existed to this child",
    )
    ren = sub.add_parser("rename")
    ren.add_argument("--child", type=int, required=True)
    ren.add_argument("--name", required=True)
    add.add_argument("--test", action="store_true",
                     help="a test child: real deliveries, never a target for unattributed feedback")
    rec = sub.add_parser("add-recipient")
    rec.add_argument("--child", type=int, required=True)
    rec.add_argument("--channel", choices=CHANNELS, required=True)
    rec.add_argument("--address", required=True)
    args = parser.parse_args(argv)

    with connect_db() as con:
        if args.command == "migrate":
            migrate(con)
            print(f"schema version {SCHEMA_VERSION}")
            return
        if not is_migrated(con):
            raise SystemExit("run 'migrate' first")
        if args.command == "add":
            print(add_child(con, args.name, args.birth_date, args.adopt_existing, args.test))
        elif args.command == "rename":
            if not args.name.strip():
                raise ValueError("name must not be empty")
            if con.execute("UPDATE children SET name = ? WHERE id = ?", (args.name.strip(), args.child)).rowcount != 1:
                raise ValueError(f"no child with id {args.child}")
            con.commit()
            print("ok")
        elif args.command == "add-recipient":
            add_recipient(con, args.child, args.channel, args.address)
            print("ok")
        elif args.command == "list":
            children = active_children(con, include_test=True)
            if not children:
                print("no children: single-child mode (CHILD_BIRTH_DATE, VK_PEER_ID)")
            for child in children:
                targets = ", ".join(f"{r['channel']}:{r['address']}" for r in child["recipients"]) or "-"
                test = "  [test]" if child.get("is_test") else ""
                print(f"{child['id']}  {child['name']}  {child['birth_date']}{test}  -> {targets}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.IntegrityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
