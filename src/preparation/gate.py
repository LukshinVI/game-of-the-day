"""The deterministic gate: schema fields, age, duration, skills, mechanics, themes,
duplicates, the source link and dangerous terms. A candidate with any error is rejected
and the errors go to the corrective retry."""

import re
from urllib.parse import urlparse

from src.preparation import skills, themes
from src.preparation import mechanics as mechanic_tags



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

# The prompt asks for under 300 characters; the hard limit leaves slack so a
# slightly long but focused note does not cost an attempt. Long generic lists of
# forbidden objects were the main reason messages felt heavy to read.
MAX_SAFETY_CHARS = 450


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
    errors.extend(mechanic_tags.violations(candidate.get("mechanic_tag"), history))
    errors.extend(themes.violations(candidate))

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
