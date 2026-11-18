"""The parent-facing message, built by code from the candidate's fields: the layout is
product-owned, the model only supplies the content."""


from src.preparation import skills



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
