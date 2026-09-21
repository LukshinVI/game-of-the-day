#!/usr/bin/env python3

import argparse
import json
import re
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
    "игол",
    "кипят",
    "горяч",
    "лекарств",
    "химикат",
}

# "нож" must not match "ножки/ножками" (a child's legs), so knives and scissors are
# matched as whole word forms; the other hazards are safe to match as stems.
KNIFE_WORDS = re.compile(r"\bнож(?:а|у|ом|е|ей|ам|ами|ах|и\w*|ев\w*|ниц\w*)?\b")

# One attempt is usually spent correcting a formatting rejection, so a single false
# positive on the second attempt used to cost the whole day.
MAX_ATTEMPTS = 3


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
        "--search",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "exec",
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

    url_allowed = re.fullmatch(
        r"https?://[A-Za-z0-9._~:/?#@!$&'*+,;=%-]+",
        source_url,
    )

    if (
        not url_allowed
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        errors.append(
            "Ссылка на источник должна быть обычным HTTP(S) URL без Markdown-разметки"
        )

    message_text = candidate.get("message_text", "")

    markdown_link = re.search(
        r"\[[^\]]+\]\s*\\?\(",
        message_text,
    )

    if markdown_link:
        errors.append(
            "Текст для отправки не должен содержать Markdown-ссылки"
        )

    if source_url not in message_text:
        errors.append(
            "Текст для отправки должен содержать чистую ссылку source_url"
        )

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

    if KNIFE_WORDS.search(safety_blob):
        errors.append("dangerous-term gate triggered: нож")

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


def build_retry_prompt(original_prompt, candidate, errors):
    feedback = {
        "rejected_candidate": candidate,
        "deterministic_validation_errors": errors,
    }

    return (
        original_prompt
        + "\n\nIMPORTANT: A previous candidate was rejected.\n"
        + "Correct every validation error below. "
          "Do not repeat the rejected result. "
          "Re-check all original requirements before returning a new candidate.\n\n"
        + json.dumps(feedback, ensure_ascii=False, indent=2)
    )


def send_ops_notification(level, title, details):
    notifier = ROOT / "src" / "ops" / "notifier.py"

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
            cwd=ROOT,
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


def send_preparation_success_alert(
    legacy_id,
    target_date,
    candidate,
    attempts,
):
    details = (
        f"ID: {legacy_id}\n"
        f"Дата: {target_date}\n"
        f"Название: {candidate['title']}\n"
        f"Навык: {candidate['skill']}\n"
        f"Попыток подбора: {attempts}\n"
        "Статус: ожидает отправки"
    )

    send_ops_notification(
        "info",
        "Игра подготовлена",
        details,
    )


def send_failure_alert(errors):
    notifier = ROOT / "src" / "ops" / "notifier.py"

    details = (
        f"Подготовка игры завершилась ошибкой после {MAX_ATTEMPTS} попыток.\n"
        "Игра не была записана в production SQLite.\n"
        "Отправка в VK не выполнялась.\n\n"
        "Ошибки последней проверки:\n- "
        + "\n- ".join(errors)
    )

    result = subprocess.run(
        [
            sys.executable,
            str(notifier),
            "critical",
            "Подготовка игры не удалась",
            "--details",
            details,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        print(
            "WARNING: Ops notification also failed:\n"
            + result.stderr[-2000:],
            file=sys.stderr,
        )
    else:
        print(result.stdout.strip())


def validate_target_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise RuntimeError(
            "Дата должна быть в формате YYYY-MM-DD"
        ) from exc


def game_for_date(target_date):
    with connect_db() as con:
        return con.execute(
            """
            SELECT
                g.id,
                g.legacy_id,
                g.game_date,
                g.title,
                d.status
            FROM games g
            LEFT JOIN deliveries d
                ON d.game_id = g.id
                AND d.channel = 'vk'
            WHERE g.game_date = ?
            ORDER BY g.id
            LIMIT 1
            """,
            (target_date,),
        ).fetchone()


def next_game_identity(con):
    max_id = con.execute(
        "SELECT COALESCE(MAX(id), 0) FROM games"
    ).fetchone()[0]

    game_id = max_id + 1
    legacy_id = f"{game_id:03d}"

    return game_id, legacy_id


def persist_candidate(candidate, target_date):
    with connect_db() as con:
        con.execute("BEGIN IMMEDIATE")

        existing = con.execute(
            """
            SELECT legacy_id, title
            FROM games
            WHERE game_date = ?
            ORDER BY id
            LIMIT 1
            """,
            (target_date,),
        ).fetchone()

        if existing:
            con.rollback()
            raise RuntimeError(
                f"На дату {target_date} уже существует игра "
                f"{existing['legacy_id']} — {existing['title']}"
            )

        game_id, legacy_id = next_game_identity(con)

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
                target_date,
                candidate["age_months"],
                candidate["title"].strip(),
                candidate["skill"],
                candidate["mechanics"].strip(),
                candidate["source_name"].strip(),
                candidate["source_url"].strip(),
                candidate["message_text"].strip(),
            ),
        )

        con.execute(
            """
            INSERT INTO deliveries (
                game_id,
                channel,
                status,
                attempts,
                idempotency_key
            )
            VALUES (?, 'vk', 'pending', 0, ?)
            """,
            (
                game_id,
                f"vk-{target_date}-{legacy_id}",
            ),
        )

        con.commit()

    return game_id, legacy_id


def select_candidate(history, target_date):
    original_prompt = build_prompt(history)
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print()
        print(f"=== ПОПЫТКА {attempt}/{MAX_ATTEMPTS} ===")

        candidate = run_codex(prompt)
        errors = validate_candidate(candidate, history)

        print()
        print("=== КАНДИДАТ ===")
        print(json.dumps(candidate, ensure_ascii=False, indent=2))

        print()
        print("=== ПРОВЕРКА ===")

        if not errors:
            print("PASS")
            return candidate, attempt

        for error in errors:
            print(f"FAIL: {error}")

        last_errors = errors

        if attempt < MAX_ATTEMPTS:
            print()
            print(
                "Кандидат отклонён. "
                "Выполняется корректирующая попытка."
            )
            prompt = build_retry_prompt(
                original_prompt,
                candidate,
                errors,
            )

    send_failure_alert(last_errors)

    raise RuntimeError(
        f"Подготовка игры не удалась после {MAX_ATTEMPTS} попыток"
    )


def prepare_run(target_date, dry_run=False):
    target_date = validate_target_date(target_date)

    # Проверяем ДО Codex — повторный запуск ничего не тратит.
    existing = game_for_date(target_date)

    if existing:
        print("=== ИГРА УЖЕ ПОДГОТОВЛЕНА ===")
        print(f"Дата:      {existing['game_date']}")
        print(f"ID:        {existing['legacy_id']}")
        print(f"Название:  {existing['title']}")
        print(f"Delivery:  {existing['status']}")
        print()
        print("Codex: НЕ ЗАПУСКАЛСЯ")
        print("SQLite: НЕ ИЗМЕНЁН")
        print("VK: ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")
        return

    history = load_history()

    print(f"Исторических игр: {len(history)}")
    print(f"Целевая дата: {target_date}")

    candidate, attempts = select_candidate(
        history,
        target_date,
    )

    print()
    print("=== ПЛАН ЗАПИСИ ===")
    print(f"Дата:      {target_date}")
    print(f"Название:  {candidate['title']}")
    print(f"Возраст:   {candidate['age_months']} мес.")
    print(f"Навык:     {candidate['skill']}")
    print("Delivery:  vk / pending")

    if dry_run:
        print()
        print("=== DRY RUN ===")
        print(f"Попыток модели: {attempts}")
        print("Production SQLite: НЕ ИЗМЕНЁН")
        print("VK: ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")
        return

    game_id, legacy_id = persist_candidate(
        candidate,
        target_date,
    )

    print()
    print("=== ИГРА ПОДГОТОВЛЕНА ===")
    print(f"ID:        {legacy_id}")
    print(f"DB ID:     {game_id}")
    print(f"Дата:      {target_date}")
    print(f"Название:  {candidate['title']}")
    print("Delivery:  pending")
    print("VK: ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")

    send_preparation_success_alert(
        legacy_id,
        target_date,
        candidate,
        attempts,
    )


def shadow_run():
    history = load_history()

    print(f"Loaded {len(history)} historical games.")

    original_prompt = build_prompt(history)
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print()
        print(f"=== ATTEMPT {attempt}/{MAX_ATTEMPTS} ===")

        candidate = run_codex(prompt)
        errors = validate_candidate(candidate, history)

        print()
        print("=== CANDIDATE ===")
        print(json.dumps(candidate, ensure_ascii=False, indent=2))

        print()
        print("=== VALIDATION ===")

        if not errors:
            print("PASS")

            path = save_shadow(candidate)

            print()
            print("=== SHADOW RESULT ===")
            print(f"Attempts: {attempt}")
            print(f"Saved: {path}")
            print("Production database: NOT MODIFIED")
            print("VK delivery: NOT ATTEMPTED")
            return

        for error in errors:
            print(f"FAIL: {error}")

        last_errors = errors

        if attempt < MAX_ATTEMPTS:
            print()
            print("Candidate rejected. Preparing a corrective retry.")
            prompt = build_retry_prompt(
                original_prompt,
                candidate,
                errors,
            )

    print()
    print("=== PREPARATION FAILED ===")
    print(f"Attempts exhausted: {MAX_ATTEMPTS}/{MAX_ATTEMPTS}")
    print("Production database: NOT MODIFIED")
    print("VK delivery: NOT ATTEMPTED")

    send_failure_alert(last_errors)

    raise RuntimeError(
        f"Подготовка игры не удалась после {MAX_ATTEMPTS} попыток"
    )



def main():
    parser = argparse.ArgumentParser(prog="game-preparation")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("shadow")

    prepare = sub.add_parser("prepare")
    prepare.add_argument(
        "--date",
        default=datetime.now(MOSCOW).date().isoformat(),
        help="Целевая дата YYYY-MM-DD",
    )
    prepare.add_argument(
        "--dry-run",
        action="store_true",
        help="Подобрать и проверить игру без записи в SQLite",
    )

    args = parser.parse_args()

    if args.command == "shadow":
        shadow_run()
    elif args.command == "prepare":
        prepare_run(
            target_date=args.date,
            dry_run=args.dry_run,
        )


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
