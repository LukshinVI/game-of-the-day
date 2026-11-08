#!/usr/bin/env python3

import argparse
import os
import json
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.children import active_children  # noqa: E402

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

# Deny broad, allow narrow: any word that starts with "нож" is treated as a knife-family
# hazard (нож, ножи, ножик, ножовка, ножны, ножницы, ...) unless it belongs to the small
# family of harmless words for a child's legs (ножка, ножки, ножек, ножонки, ноженьки).
KNIFE_PREFIX = re.compile(r"\bнож\w*")
LEG_WORDS = re.compile(r"нож(?:к\w*|ек|онк\w*|еньк\w*)")

# The first attempt is often spent on a formatting rejection, so two independent validation
# rejections used to exhaust the daily run. One more bounded corrective attempt is allowed.
MAX_ATTEMPTS = 3

# The prompt asks for under 300 characters; the hard limit leaves slack so a
# slightly long but focused note does not cost an attempt. Long generic lists of
# forbidden objects were the main reason messages felt heavy to read.
MAX_SAFETY_CHARS = 450

# Web research makes a single model call slow; one attempt must still fit well
# inside the preparation unit's TimeoutStartSec.
MODEL_TIMEOUT_SECONDS = int(os.environ.get("PREPARATION_MODEL_TIMEOUT", "600"))


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
        rows = con.execute(
            f"""
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
            {where}
            ORDER BY game_date, id
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def child_age_months(on_date, birth_date=None):
    """Completed months of age on `on_date` (a date or YYYY-MM-DD string).

    The birth date comes from CHILD_BIRTH_DATE (YYYY-MM-DD, or YYYY-MM meaning the
    1st of that month) so the age advances on its own and stays out of the repository.
    A missing or invalid value is an error: guessing an age would silently select
    activities for the wrong developmental stage.
    """
    raw = birth_date if birth_date is not None else os.environ.get("CHILD_BIRTH_DATE", "")
    raw = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            birth = date.fromisoformat(raw + "-01")
        else:
            birth = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            "CHILD_BIRTH_DATE must be set as YYYY-MM-DD or YYYY-MM, got "
            f"{raw!r}"
        ) from None

    if isinstance(on_date, str):
        on_date = date.fromisoformat(on_date)

    months = (on_date.year - birth.year) * 12 + (on_date.month - birth.month)
    if on_date.day < birth.day:
        months -= 1

    if months < 0:
        raise ValueError(f"CHILD_BIRTH_DATE {raw} is after {on_date.isoformat()}")

    return months


def build_prompt(history, age_months, today=None):
    base = PROMPT_PATH.read_text(encoding="utf-8")

    context = {
        "today": today or datetime.now(MOSCOW).date().isoformat(),
        "child_age_months": age_months,
        "previous_games": history,
    }

    return (
        base
        + "\n\nINPUT CONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


def run_model(prompt):
    """Ask the configured model backend for one candidate (a dict).

    The backend only proposes; validate_candidate() decides. PREPARATION_BACKEND
    selects `claude` (Claude Code headless, the default) or `codex` (Codex CLI).
    """
    backend = os.environ.get("PREPARATION_BACKEND", "claude").strip().lower()
    if backend == "claude":
        return run_claude(prompt)
    if backend == "codex":
        return run_codex(prompt)
    raise ValueError(f"unknown PREPARATION_BACKEND: {backend!r} (expected claude or codex)")


def claude_command(schema):
    model = os.environ.get("PREPARATION_CLAUDE_MODEL", "opus")
    return [
        os.environ.get("CLAUDE_BIN", "claude"),
        "--print",
        "--output-format", "json",
        "--json-schema", schema,
        "--model", model,
        # Web research only: no shell, no file access, no MCP servers, no plugins.
        "--tools", "WebSearch,WebFetch",
        "--allowedTools", "WebSearch,WebFetch",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]


def run_claude(prompt):
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    print("Starting Claude candidate selection...", file=sys.stderr)

    result = subprocess.run(
        claude_command(schema),
        input=prompt,
        text=True,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=MODEL_TIMEOUT_SECONDS,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Claude failed.\n"
            f"exit={result.returncode}\n"
            f"stdout={result.stdout[-4000:]}\n"
            f"stderr={result.stderr[-4000:]}"
        )

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Claude output is not valid JSON: {exc}\n{result.stdout[-4000:]}"
        ) from None

    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise RuntimeError(
            "Claude run did not succeed: "
            f"subtype={envelope.get('subtype')} result={str(envelope.get('result'))[:2000]}"
        )

    candidate = envelope.get("structured_output")
    if not isinstance(candidate, dict):
        raise RuntimeError("Claude returned no structured_output object")

    return candidate


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
        timeout=MODEL_TIMEOUT_SECONDS,
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


def validate_candidate(candidate, history, expected_age_months):
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

    if candidate.get("age_months") != expected_age_months:
        errors.append(f"age_months must be exactly {expected_age_months}")

    duration = candidate.get("duration_minutes")
    if not isinstance(duration, int) or not 5 <= duration <= 10:
        errors.append("duration_minutes must be between 5 and 10")

    safety_text = str(candidate.get("safety", ""))
    if len(safety_text) > MAX_SAFETY_CHARS:
        errors.append(
            f"safety is {len(safety_text)} characters; keep it under {MAX_SAFETY_CHARS}: "
            "supervision, the hazards specific to this game, when to stop"
        )

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

    if any(
        not LEG_WORDS.fullmatch(match.group())
        for match in KNIFE_PREFIX.finditer(safety_blob)
    ):
        errors.append("dangerous-term gate triggered: нож")

    if not candidate.get("message_text", "").strip():
        errors.append("message_text is empty")

    return errors


SKILL_LABELS = {
    "speech": "речь",
    "coordination": "координация",
    "fine_motor": "мелкая моторика",
}


def minutes_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "минута"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "минуты"
    return "минут"


def render_message(candidate):
    """Build the parent-facing message from the structured fields.

    The layout is product-owned and deterministic (the caregiver asked for this
    emoji layout back on 2026-09-17), so the model no longer formats the message;
    it only supplies the fields, which are validated as before.
    """
    duration = candidate["duration_minutes"]
    materials = "; ".join(m.strip().rstrip(".;") for m in candidate["materials"])
    steps = "\n".join(
        f"{i}. {step.strip()}" for i, step in enumerate(candidate["steps"], start=1)
    )
    skill = SKILL_LABELS.get(candidate["skill"], candidate["skill"])

    return "\n".join(
        [
            f"🎲 Игра дня: «{candidate['title'].strip()}»",
            f"⏱ {duration} {minutes_word(duration)}",
            f"🎯 Цель: {candidate['goal'].strip()}",
            f"🤲 Навык: {skill}.",
            f"🧸 Нужно: {materials}.",
            "",
            "Как играть:",
            steps,
            "",
            f"✅ Безопасность: {candidate['safety'].strip()}",
            "",
            f"Источник: {candidate['source_name'].strip()}",
            candidate["source_url"].strip(),
        ]
    )


def with_rendered_message(candidate):
    """Replace the model's message_text with the deterministic layout.

    A candidate whose fields cannot be rendered is returned unchanged, so the
    usual validation rejects it with a reason the retry prompt can carry.
    """
    if not isinstance(candidate, dict):
        return candidate
    try:
        return {**candidate, "message_text": render_message(candidate)}
    except (KeyError, TypeError, AttributeError):
        return candidate


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
    child_name=None,
):
    details = (
        (f"Ребёнок: {child_name}\n" if child_name else "")
        + f"ID: {legacy_id}\n"
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


def persist_candidate(candidate, target_date, child=None):
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


def select_candidate(history, target_date, birth_date=None):
    age_months = child_age_months(target_date, birth_date)
    original_prompt = build_prompt(history, age_months, today=str(target_date))
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print()
        print(f"=== ПОПЫТКА {attempt}/{MAX_ATTEMPTS} ===")

        candidate = with_rendered_message(run_model(prompt))
        errors = validate_candidate(candidate, history, age_months)

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
    """Prepare today's game for every active child (or the single child).

    Children are independent: a failure for one is reported and does not stop
    the others; the run fails at the end if any child failed.
    """
    target_date = validate_target_date(target_date)

    with connect_db() as con:
        children = active_children(con)

    plan = children or [None]
    failures = []

    for child in plan:
        label = f"{child['name']} (id {child['id']})" if child else "single-child mode"
        print()
        print(f"##### {label} #####")
        try:
            prepare_for_child(target_date, child, dry_run)
        except Exception as exc:  # noqa: BLE001 - reported per child, re-raised below
            print(f"ERROR: {exc}", file=sys.stderr)
            failures.append(f"{label}: {exc}")

    if failures:
        raise RuntimeError("; ".join(failures))


def prepare_for_child(target_date, child, dry_run=False):
    child_id = child["id"] if child else None

    # Проверяем ДО вызова модели — повторный запуск ничего не тратит.
    existing = game_for_date(target_date, child_id)

    if existing:
        print("=== ИГРА УЖЕ ПОДГОТОВЛЕНА ===")
        print(f"Дата:      {existing['game_date']}")
        print(f"ID:        {existing['legacy_id']}")
        print(f"Название:  {existing['title']}")
        print(f"Delivery:  {existing['status']}")
        print()
        print("Модель: НЕ ЗАПУСКАЛАСЬ")
        print("SQLite: НЕ ИЗМЕНЁН")
        print("ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")
        return

    history = load_history(child_id)

    print(f"Исторических игр: {len(history)}")
    print(f"Целевая дата: {target_date}")

    candidate, attempts = select_candidate(
        history,
        target_date,
        child["birth_date"] if child else None,
    )

    channels = ", ".join(r["channel"] for r in child["recipients"]) if child else "vk"

    print()
    print("=== ПЛАН ЗАПИСИ ===")
    print(f"Дата:      {target_date}")
    print(f"Название:  {candidate['title']}")
    print(f"Возраст:   {candidate['age_months']} мес.")
    print(f"Навык:     {candidate['skill']}")
    print(f"Delivery:  {channels} / pending")

    if dry_run:
        print()
        print("=== DRY RUN ===")
        print(f"Попыток модели: {attempts}")
        print("Production SQLite: НЕ ИЗМЕНЁН")
        print("ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")
        return

    game_id, legacy_id = persist_candidate(
        candidate,
        target_date,
        child,
    )

    print()
    print("=== ИГРА ПОДГОТОВЛЕНА ===")
    print(f"ID:        {legacy_id}")
    print(f"DB ID:     {game_id}")
    print(f"Дата:      {target_date}")
    print(f"Название:  {candidate['title']}")
    print("Delivery:  pending")
    print("ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")

    send_preparation_success_alert(
        legacy_id,
        target_date,
        candidate,
        attempts,
        child["name"] if child else None,
    )


def shadow_run():
    history = load_history()

    print(f"Loaded {len(history)} historical games.")

    age_months = child_age_months(datetime.now(MOSCOW).date())
    original_prompt = build_prompt(history, age_months)
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print()
        print(f"=== ATTEMPT {attempt}/{MAX_ATTEMPTS} ===")

        candidate = with_rendered_message(run_model(prompt))
        errors = validate_candidate(candidate, history, age_months)

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
            f"ERROR: model call timed out after {MODEL_TIMEOUT_SECONDS} seconds",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
