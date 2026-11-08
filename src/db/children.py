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
SCHEMA_VERSION = 3

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
# invite) and the in-progress questionnaire, so a bot restart loses nothing.
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
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS onboarding (
    chat_id TEXT PRIMARY KEY REFERENCES members(chat_id),
    step TEXT NOT NULL CHECK(step IN ('name', 'birth_date', 'confirm')),
    name TEXT,
    birth_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def active_children(con):
    """Active children with their active recipients; [] means single-child mode."""
    if not is_migrated(con):
        return []
    children = [dict(row) for row in con.execute(
        "SELECT id, name, birth_date FROM children WHERE active = 1 ORDER BY id"
    )]
    for child in children:
        child["recipients"] = [dict(row) for row in con.execute(
            "SELECT channel, address FROM recipients "
            "WHERE child_id = ? AND active = 1 ORDER BY id",
            (child["id"],),
        )]
    return children


def add_child(con, name, birth_date, adopt_existing=False):
    date.fromisoformat(birth_date)  # validates YYYY-MM-DD
    if not name.strip():
        raise ValueError("name must not be empty")
    child_id = con.execute(
        "INSERT INTO children(name, birth_date) VALUES (?, ?)",
        (name.strip(), birth_date),
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
            print(add_child(con, args.name, args.birth_date, args.adopt_existing))
        elif args.command == "add-recipient":
            add_recipient(con, args.child, args.channel, args.address)
            print("ok")
        elif args.command == "list":
            children = active_children(con)
            if not children:
                print("no children: single-child mode (CHILD_BIRTH_DATE, VK_PEER_ID)")
            for child in children:
                targets = ", ".join(f"{r['channel']}:{r['address']}" for r in child["recipients"]) or "-"
                print(f"{child['id']}  {child['name']}  {child['birth_date']}  -> {targets}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.IntegrityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
