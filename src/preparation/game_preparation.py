#!/usr/bin/env python3

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
PROMPT_PATH = ROOT / "src" / "preparation" / "prompts" / "game_selector.md"
SCHEMA_PATH = ROOT / "src" / "preparation" / "schemas" / "game_candidate.json"
SHADOW_DIR = ROOT / "data" / "shadow"

MOSCOW = ZoneInfo("Europe/Moscow")

ALLOWED_SKILLS = {"speech", "coordination", "fine_motor"}

DANGEROUS_TERMS = {
    "бусин",
    "монет",
    "пуговиц",
    "батарей",
    "магнит",
    "стекл",
    "нож",
    "игол",
    "кипят",
    "горяч",
    "лекарств",
    "химикат",
}


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_history():
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT
                legacy_id,
                game_date,
                age_months,
                title,
                skill,
                mechanics,
                source_name,
                source_url
            FROM games
            ORDER BY game_date, id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def build_prompt(history):
    base = PROMPT_PATH.read_text(encoding="utf-8")

    context = {
        "today": datetime.now(MOSCOW).date().isoformat(),
        "child_age_months": 12,
        "previous_games": history,
    }

    return (
        base
        + "\n\nINPUT CONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


def run_codex(prompt):
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)

    output_path = SHADOW_DIR / "candidate-latest.json"

    if output_path.exists():
        output_path.unlink()

    cmd = [
        "codex",
        "exec",
        "--search",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "--ephemeral",
        "--output-schema",
        str(SCHEMA_PATH),
        "--output-last-message",
        str(output_path),
        "-",
    ]

    print("Starting Codex candidate selection...", file=sys.stderr)

    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Codex failed.\n"
            f"exit={result.returncode}\n"
            f"stdout={result.stdout[-4000:]}\n"
            f"stderr={result.stderr[-4000:]}"
        )

    if not output_path.exists():
        raise RuntimeError("Codex did not create output file")

    raw = output_path.read_text(encoding="utf-8").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Codex output is not valid JSON: {exc}\n"
            f"Output:\n{raw[:4000]}"
        ) from exc


def normalize(text):
    return " ".join(str(text).lower().split())


def validate_candidate(candidate, history):
    errors = []

    required = {
        "title",
        "age_months",
        "duration_minutes",
        "goal",
        "skill",
        "mechanics",
        "materials",
        "steps",
        "safety",
        "source_name",
        "source_url",
        "message_text",
    }

    missing = required - set(candidate)
    if missing:
        errors.append(
            "missing fields: " + ", ".join(sorted(missing))
        )

    if candidate.get("age_months") != 12:
        errors.append("age_months must be exactly 12")

    duration = candidate.get("duration_minutes")
    if not isinstance(duration, int) or not 5 <= duration <= 10:
        errors.append("duration_minutes must be between 5 and 10")

    if candidate.get("skill") not in ALLOWED_SKILLS:
        errors.append("invalid primary skill")

    title = normalize(candidate.get("title", ""))
    mechanics = normalize(candidate.get("mechanics", ""))

    for old in history:
        if title and title == normalize(old.get("title", "")):
            errors.append(
                f"duplicate title: {old.get('title')}"
            )

        if mechanics and mechanics == normalize(old.get("mechanics", "")):
            errors.append(
                f"duplicate mechanics: {old.get('mechanics')}"
            )

    source_url = candidate.get("source_url", "")
    parsed = urlparse(source_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append("source_url is not a valid HTTP(S) URL")

    materials = candidate.get("materials")
    if not isinstance(materials, list) or not materials:
        errors.append("materials must be a non-empty list")

    steps = candidate.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        errors.append("steps must contain at least 2 steps")

    safety_blob = normalize(
        " ".join(
            [
                str(candidate.get("title", "")),
                str(candidate.get("mechanics", "")),
                " ".join(candidate.get("materials", []) or []),
                " ".join(candidate.get("steps", []) or []),
            ]
        )
    )

    for term in DANGEROUS_TERMS:
        if term in safety_blob:
            errors.append(
                f"dangerous-term gate triggered: {term}"
            )

    if not candidate.get("message_text", "").strip():
        errors.append("message_text is empty")

    return errors


def save_shadow(candidate):
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(MOSCOW)
    timestamp = now.strftime("%Y%m%d-%H%M%S")

    archive_path = SHADOW_DIR / f"candidate-{timestamp}.json"

    archive_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return archive_path


def shadow_run():
    history = load_history()

    print(f"Loaded {len(history)} historical games.")

    prompt = build_prompt(history)
    candidate = run_codex(prompt)

    errors = validate_candidate(candidate, history)

    print()
    print("=== CANDIDATE ===")
    print(json.dumps(candidate, ensure_ascii=False, indent=2))

    print()
    print("=== VALIDATION ===")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")

        raise RuntimeError(
            f"Candidate rejected by {len(errors)} deterministic gate(s)"
        )

    print("PASS")

    path = save_shadow(candidate)

    print()
    print("=== SHADOW RESULT ===")
    print(f"Saved: {path}")
    print("Production database: NOT MODIFIED")
    print("VK delivery: NOT ATTEMPTED")


def main():
    parser = argparse.ArgumentParser(prog="game-preparation")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("shadow")

    args = parser.parse_args()

    if args.command == "shadow":
        shadow_run()


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        print(
            "ERROR: Codex timed out after 300 seconds",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
