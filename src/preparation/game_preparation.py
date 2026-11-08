#!/usr/bin/env python3

import argparse
import os
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.children import active_children, has_children  # noqa: E402
from src.feedback import profile as child_profile  # noqa: E402
from src.preparation import fallback, review, skills  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
PROMPT_PATH = ROOT / "src" / "preparation" / "prompts" / "game_selector.md"
SCHEMA_PATH = ROOT / "src" / "preparation" / "schemas" / "game_candidate.json"
SHADOW_DIR = ROOT / "data" / "shadow"

MOSCOW = ZoneInfo("Europe/Moscow")



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

# The age range the prompt and the candidate schema are designed for.
MAX_AGE_MONTHS_SUPPORTED = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["age_months"]["maximum"]

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
        **skills.prompt_context(age_months, history),
        **({"child_profile": PROFILE_CONTEXT} if PROFILE_CONTEXT else {}),
        "previous_games": history,
    }

    return (
        base
        + "\n\nINPUT CONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


# Observability (issue: model quality and spend). run_model() fills LAST_RUN_META for
# the attempt it ran; select_candidate() appends one record per attempt to RUN_LOG;
# prepare_for_child() persists RUN_LOG to model_runs, also when the day failed.
LAST_RUN_META = {}
RUN_LOG = []


def run_model(prompt):
    """Ask the configured model backend for one candidate (a dict).

    The backend only proposes; validate_candidate() decides. PREPARATION_BACKEND
    selects `claude` (Claude Code headless, the default) or `codex` (Codex CLI).
    """
    backend = os.environ.get("PREPARATION_BACKEND", "claude").strip().lower()
    LAST_RUN_META.clear()
    LAST_RUN_META["backend"] = backend
    if backend == "claude":
        return run_claude(prompt)
    if backend == "codex":
        return run_codex(prompt)
    raise ValueError(f"unknown PREPARATION_BACKEND: {backend!r} (expected claude or codex)")


MODEL_OVERRIDE = None  # set by obtain_candidate() for the fallback model
# The child's profile from feedback (src/feedback/profile.py), set by prepare_for_child().
PROFILE_CONTEXT = {}


def claude_command(schema):
    model = MODEL_OVERRIDE or os.environ.get("PREPARATION_CLAUDE_MODEL", "opus")
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

    LAST_RUN_META.update(claude_meta(envelope))

    candidate = envelope.get("structured_output")
    if not isinstance(candidate, dict):
        raise RuntimeError("Claude returned no structured_output object")

    return candidate


def claude_meta(envelope):
    """Latency, tokens and cost from the Claude Code JSON envelope (missing fields -> None)."""
    usage = envelope.get("usage") or {}
    server = usage.get("server_tool_use") or {}
    models = list((envelope.get("modelUsage") or {}).keys())
    return {
        "model": ",".join(models) or MODEL_OVERRIDE or os.environ.get("PREPARATION_CLAUDE_MODEL", "opus"),
        "api_ms": envelope.get("duration_api_ms") or envelope.get("duration_ms"),
        "num_turns": envelope.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "web_searches": server.get("web_search_requests"),
        "cost_usd": envelope.get("total_cost_usd"),
    }


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

    errors.extend(skills.violations(candidate.get("skill"), expected_age_months, history))

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


SKILL_LABELS = skills.LABELS


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
    origin="model",
    origin_reason=None,
):
    details = (
        (f"Ребёнок: {child_name}\n" if child_name else "")
        + f"ID: {legacy_id}\n"
        f"Дата: {target_date}\n"
        f"Название: {candidate['title']}\n"
        f"Навык: {candidate['skill']}\n"
        + (f"Попыток подбора: {attempts}\n" if origin != "library" else
           f"Из библиотеки: копия {candidate.get('library_source')}\n")
        + (f"Причина резерва: {origin_reason}\n" if origin != "model" else "")
        + "Статус: ожидает отправки"
    )
    titles = {
        "model": ("info", "Игра подготовлена"),
        "fallback_model": ("warning", "Игра подготовлена резервной моделью"),
        "library": ("warning", "Игра из резерва (библиотека), а не подобрана"),
    }
    level, title = titles.get(origin, titles["model"])

    send_ops_notification(
        level,
        title,
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
                "UPDATE games SET origin = ?, origin_reason = ?, candidate_json = ? WHERE id = ?",
                (origin, origin_reason, fallback.candidate_json(candidate), game_id),
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


def record_attempt(attempt, outcome, errors, started):
    RUN_LOG.append({
        **LAST_RUN_META,
        "attempt": attempt,
        "outcome": outcome,
        "errors": errors,
        "wall_ms": int((time.monotonic() - started) * 1000),
    })


RUN_COLUMNS = (
    "backend", "model", "wall_ms", "api_ms", "num_turns", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "web_searches", "cost_usd",
)


def persist_runs(target_date, child_id, dry_run):
    """Write RUN_LOG to model_runs. Never fails the preparation: metrics are secondary."""
    runs, RUN_LOG[:] = list(RUN_LOG), []
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


def select_candidate(history, target_date, birth_date=None, age_months=None):
    """Propose, validate and retry. age_months overrides the birth-date computation
    (used by the selection evals, which describe cases by age)."""
    if age_months is None:
        age_months = child_age_months(target_date, birth_date)
    original_prompt = build_prompt(history, age_months, today=str(target_date))
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print()
        print(f"=== ПОПЫТКА {attempt}/{MAX_ATTEMPTS} ===")

        started = time.monotonic()
        try:
            candidate = with_rendered_message(run_model(prompt))
        except Exception as exc:
            record_attempt(attempt, "error", [f"{type(exc).__name__}: {str(exc)[:300]}"], started)
            raise
        errors = validate_candidate(candidate, history, age_months)
        record_attempt(attempt, "rejected" if errors else "pass", errors, started)
        required = (PROFILE_CONTEXT or {}).get("required_next")
        if not errors and required and candidate.get("skill") != required["skill"]:
            errors = [f"after the parent's feedback on «{required['after']}» the next game must train "
                      f"{required['skill']} ({required['direction']} than before), not {candidate.get('skill')}"]
            RUN_LOG[-1]["outcome"], RUN_LOG[-1]["errors"] = "rejected", errors
        if not errors:
            problems, meta = review.review(candidate, age_months)
            if meta is not None:
                RUN_LOG.append({**meta, "attempt": attempt, "outcome": "rejected" if problems else
                                ("error" if meta["error"] else "pass"),
                                "errors": [f"safety review: {p}" for p in problems] or
                                ([meta["error"]] if meta["error"] else [])})
            errors = [f"safety review: {p}" for p in problems]

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

    if ALERT_ON_EXHAUSTION:
        send_failure_alert(last_errors)

    raise RuntimeError(
        f"Подготовка игры не удалась после {MAX_ATTEMPTS} попыток: "
        + "; ".join(last_errors)[:300]
    )


# obtain_candidate() alerts once for the whole chain instead.
ALERT_ON_EXHAUSTION = True


def obtain_candidate(history, target_date, child):
    """Primary model -> fallback model -> library. Returns (candidate, attempts, origin, reason).

    Raises (after one failure alert) only when nothing fits at all.
    """
    global ALERT_ON_EXHAUSTION, MODEL_OVERRIDE
    birth_date = child["birth_date"] if child else None
    child_id = child["id"] if child else None
    reason = None

    with connect_db() as con:
        budget = fallback.daily_budget_usd()
        spent = fallback.spent_today(con, target_date)
    if budget is not None and spent >= budget:
        reason = f"дневной бюджет модели исчерпан (${spent:.2f} из ${budget:.2f})"

    saved = ALERT_ON_EXHAUSTION
    ALERT_ON_EXHAUSTION = False
    try:
        if reason is None:
            try:
                candidate, attempts = select_candidate(history, target_date, birth_date)
                return candidate, attempts, "model", None
            except Exception as exc:  # noqa: BLE001 - every failure moves down the chain
                quota = fallback.is_quota_error(exc)
                reason = ("лимит подписки исчерпан" if quota else "основная модель не справилась") + f": {str(exc)[:200]}"
                print(f"FALLBACK: {reason}", file=sys.stderr)

            if not fallback.is_quota_error(reason) and fallback.fallback_model():
                MODEL_OVERRIDE = fallback.fallback_model()
                try:
                    candidate, attempts = select_candidate(history, target_date, birth_date)
                    return candidate, attempts, "fallback_model", f"{MODEL_OVERRIDE}; {reason}"
                except Exception as exc:  # noqa: BLE001
                    reason += f"; резервная модель {MODEL_OVERRIDE}: {str(exc)[:200]}"
                    print(f"FALLBACK: {reason}", file=sys.stderr)
                finally:
                    MODEL_OVERRIDE = None
    finally:
        ALERT_ON_EXHAUSTION = saved

    age = child_age_months(target_date, birth_date)
    with connect_db() as con:
        candidate = fallback.library_candidate(con, history, age, target_date, child_id)
    if candidate is not None:
        return candidate, 0, "library", reason

    send_failure_alert([reason or "неизвестная причина", "в библиотеке нет подходящей игры"])
    raise RuntimeError(f"Подготовка игры не удалась, резерва нет: {reason}")


def prepare_run(target_date, dry_run=False, only_child=None):
    """Prepare today's game for every active child (or the single child).

    only_child limits the run to one child id (a manual run for a new or test child
    must not prepare, and then deliver early, everyone else's game).

    Children are independent: a failure for one is reported and does not stop
    the others; the run fails at the end if any child failed.
    """
    target_date = validate_target_date(target_date)

    with connect_db() as con:
        multi_child = has_children(con)
        # A child nobody receives games for (all recipients paused) costs a model
        # call for nothing: skip it.
        children = [c for c in active_children(con, include_test=only_child is not None) if c["recipients"]]
    if only_child is not None:
        children = [c for c in children if c["id"] == only_child]
        if not children:
            raise RuntimeError(f"no active child with recipients and id {only_child}")

    # The games (and the candidate schema) cover 0-36 months; an older child would
    # fail every attempt and alert daily. Skip with a note instead.
    grown = [c for c in children if child_age_months(target_date, c["birth_date"]) > MAX_AGE_MONTHS_SUPPORTED]
    for child in grown:
        print(f"{child['name']} (id {child['id']}): старше {MAX_AGE_MONTHS_SUPPORTED} мес., игры не готовятся.")
    children = [c for c in children if c not in grown]

    # Single-child mode only before the first child exists; once children are
    # configured, "nobody to prepare for" means nothing to do, not the env child.
    plan = children if multi_child else [None]
    if not plan:
        print("Нет активных детей с получателями: готовить нечего.")
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

    global PROFILE_CONTEXT
    with connect_db() as con:
        PROFILE_CONTEXT = child_profile.load(con, child_id)
        required = child_profile.required_next(con, child_id, history)
    if required:
        PROFILE_CONTEXT = {**PROFILE_CONTEXT, "required_next": required}
    if PROFILE_CONTEXT:
        print(f"Профиль: {json.dumps(PROFILE_CONTEXT, ensure_ascii=False)}")

    RUN_LOG.clear()
    try:
        candidate, attempts, origin, origin_reason = obtain_candidate(history, target_date, child)
    finally:
        PROFILE_CONTEXT = {}
        persist_runs(target_date, child_id, dry_run)
    print(f"Источник: {fallback.describe(origin, origin_reason)}")

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
        origin=origin,
        origin_reason=origin_reason,
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
        origin=origin,
        origin_reason=origin_reason,
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

    if ALERT_ON_EXHAUSTION:
        send_failure_alert(last_errors)

    raise RuntimeError(
        f"Подготовка игры не удалась после {MAX_ATTEMPTS} попыток: "
        + "; ".join(last_errors)[:300]
    )


# obtain_candidate() alerts once for the whole chain instead.
ALERT_ON_EXHAUSTION = True


def obtain_candidate(history, target_date, child):
    """Primary model -> fallback model -> library. Returns (candidate, attempts, origin, reason).

    Raises (after one failure alert) only when nothing fits at all.
    """
    global ALERT_ON_EXHAUSTION, MODEL_OVERRIDE
    birth_date = child["birth_date"] if child else None
    child_id = child["id"] if child else None
    reason = None

    with connect_db() as con:
        budget = fallback.daily_budget_usd()
        spent = fallback.spent_today(con, target_date)
    if budget is not None and spent >= budget:
        reason = f"дневной бюджет модели исчерпан (${spent:.2f} из ${budget:.2f})"

    saved = ALERT_ON_EXHAUSTION
    ALERT_ON_EXHAUSTION = False
    try:
        if reason is None:
            try:
                candidate, attempts = select_candidate(history, target_date, birth_date)
                return candidate, attempts, "model", None
            except Exception as exc:  # noqa: BLE001 - every failure moves down the chain
                quota = fallback.is_quota_error(exc)
                reason = ("лимит подписки исчерпан" if quota else "основная модель не справилась") + f": {str(exc)[:200]}"
                print(f"FALLBACK: {reason}", file=sys.stderr)

            if not fallback.is_quota_error(reason) and fallback.fallback_model():
                MODEL_OVERRIDE = fallback.fallback_model()
                try:
                    candidate, attempts = select_candidate(history, target_date, birth_date)
                    return candidate, attempts, "fallback_model", f"{MODEL_OVERRIDE}; {reason}"
                except Exception as exc:  # noqa: BLE001
                    reason += f"; резервная модель {MODEL_OVERRIDE}: {str(exc)[:200]}"
                    print(f"FALLBACK: {reason}", file=sys.stderr)
                finally:
                    MODEL_OVERRIDE = None
    finally:
        ALERT_ON_EXHAUSTION = saved

    age = child_age_months(target_date, birth_date)
    with connect_db() as con:
        candidate = fallback.library_candidate(con, history, age, target_date, child_id)
    if candidate is not None:
        return candidate, 0, "library", reason

    send_failure_alert([reason or "неизвестная причина", "в библиотеке нет подходящей игры"])
    raise RuntimeError(f"Подготовка игры не удалась, резерва нет: {reason}")



RESERVE_MIN = 3
RESERVE_MONTHS_AHEAD = 2
RESERVE_MAX_NEW_PER_RUN = 6


def reserve_import(paths):
    """Add validated candidates from selection-eval reports to the warm reserve."""
    added = 0
    with connect_db() as con:
        for path in paths:
            for record in json.loads(Path(path).read_text(encoding="utf-8")):
                candidate = record.get("candidate")
                if not record.get("passed") or not isinstance(candidate, dict):
                    continue
                candidate = with_rendered_message(candidate)
                errors = validate_candidate(candidate, [], candidate["age_months"])
                if errors:
                    print(f"skip {candidate.get('title')}: {errors}")
                    continue
                if fallback.add_reserve(con, candidate, f"evals:{Path(path).name}"):
                    added += 1
                    print(f"+ {candidate['age_months']} мес. {candidate['skill']}: {candidate['title']}")
    print(f"added {added}")


def reserve_top_up(target_date):
    """Keep at least RESERVE_MIN library games for each child's age over the next months.

    Generates the missing ones with the configured model under the same validator and
    within the daily budget; at most RESERVE_MAX_NEW_PER_RUN per run.
    """
    global ALERT_ON_EXHAUSTION
    target_date = validate_target_date(target_date)
    with connect_db() as con:
        children = [c for c in active_children(con) if c["recipients"]]
    made = 0
    for child in children:
        history = load_history(child["id"])
        base = child_age_months(target_date, child["birth_date"])
        for age in range(base, min(base + RESERVE_MONTHS_AHEAD, MAX_AGE_MONTHS_SUPPORTED) + 1):
            with connect_db() as con:
                have = fallback.eligible_count(con, history, age, target_date)
                budget, spent = fallback.daily_budget_usd(), fallback.spent_today(con, target_date)
            print(f"{child['name']}: {age} мес. — в резерве {have}")
            synthetic = list(history)
            while have < RESERVE_MIN and made < RESERVE_MAX_NEW_PER_RUN:
                if budget is not None and spent >= budget:
                    print("budget spent; stopping")
                    return made
                RUN_LOG.clear()
                saved, ALERT_ON_EXHAUSTION = ALERT_ON_EXHAUSTION, False
                try:
                    candidate, _ = select_candidate(synthetic, target_date, age_months=age)
                except Exception as exc:  # noqa: BLE001 - a reserve gap is not an outage
                    print(f"could not make a reserve game for {age} months: {str(exc)[:200]}")
                    break
                finally:
                    ALERT_ON_EXHAUSTION = saved
                    persist_runs(target_date, None, False)
                with connect_db() as con:
                    if fallback.add_reserve(con, candidate, os.environ.get("PREPARATION_CLAUDE_MODEL", "model")):
                        made += 1
                        have += 1
                    spent = fallback.spent_today(con, target_date)
                synthetic.append(candidate)
                print(f"+ {age} мес. {candidate['skill']}: {candidate['title']}")
    print(f"reserve: {made} new")
    return made


def main():
    parser = argparse.ArgumentParser(prog="game-preparation")
    sub = parser.add_subparsers(dest="command", required=True)

    reserve = sub.add_parser("reserve", help="warm reserve of validated games for graceful degradation")
    reserve_sub = reserve.add_subparsers(dest="reserve_command", required=True)
    imp = reserve_sub.add_parser("import", help="add passed candidates from selection-eval reports")
    imp.add_argument("paths", nargs="+")
    top = reserve_sub.add_parser("top-up", help="generate missing reserve games for upcoming ages")
    top.add_argument("--date", default=datetime.now(MOSCOW).date().isoformat())

    sub.add_parser("shadow")

    prepare = sub.add_parser("prepare")
    prepare.add_argument(
        "--date",
        default=datetime.now(MOSCOW).date().isoformat(),
        help="Целевая дата YYYY-MM-DD",
    )
    prepare.add_argument("--child", type=int, help="only this child id (manual runs)")
    prepare.add_argument(
        "--dry-run",
        action="store_true",
        help="Подобрать и проверить игру без записи в SQLite",
    )

    args = parser.parse_args()

    if args.command == "reserve":
        if args.reserve_command == "import":
            reserve_import(args.paths)
        else:
            reserve_top_up(args.date)
    elif args.command == "shadow":
        shadow_run()
    elif args.command == "prepare":
        prepare_run(
            target_date=args.date,
            dry_run=args.dry_run,
            only_child=args.child,
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
