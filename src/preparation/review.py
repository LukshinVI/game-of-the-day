"""Second safety line: a model reviews an activity that already passed the gate.

The deterministic gate catches what words can catch (hazardous terms, length, age,
skills, duplicates). The evals showed what it cannot: a soft toy with button eyes for a
9-month-old, paper that can be chewed, no head support for a 2-month-old. A small model
reads the activity against a fixed checklist for the child's age and reports only
serious, concrete problems. A finding rejects the candidate like a gate error, so the
corrective retry gets the reasons; the fallback chain still applies.

Enabled by PREPARATION_REVIEW_MODEL (for example "haiku"); unset means off. A reviewer
failure (outage, malformed output) never blocks the day: the gate already passed.
"""

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 180

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["serious_problems"],
    "properties": {
        "serious_problems": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 240},
        },
    },
}

PROMPT = """You are a safety reviewer for developmental activities for young children.
A parent will do this activity with a child aged {age} months, under supervision.

Report only SERIOUS, CONCRETE problems that a careful parent could miss and that could hurt
this child at this age, for example: a choking hazard (small or detachable parts, chewable
paper or food the child may not have had), suffocation or strangulation (cloth over a baby's
face, cords or ribbons longer than 30 cm), falls (a baby on a raised surface, a child who
cannot sit steadily left unsupported, slippery or cluttered floor in a moving game), missing
head and neck support under 4 months, hot or toxic materials.

Do not report style, clarity, missing nice-to-have advice, or anything the activity already
handles. If there is nothing serious, return an empty list. Each problem: one sentence in
Russian saying what to change.

ACTIVITY (JSON):
{activity}
"""


def model():
    return os.environ.get("PREPARATION_REVIEW_MODEL", "").strip()


def review(candidate, age_months, runner=None):
    """Return (problems, meta). problems is [] when the activity is fine or review is off."""
    name = model()
    if not name:
        return [], None
    activity = {k: candidate.get(k) for k in ("title", "materials", "steps", "safety", "duration_minutes")}
    prompt = PROMPT.format(age=age_months, activity=json.dumps(activity, ensure_ascii=False, indent=2))
    started = time.monotonic()
    try:
        envelope = (runner or _run)(prompt, name)
        output = envelope.get("structured_output") or {}
        problems = [str(p).strip() for p in output.get("serious_problems", []) if str(p).strip()][:3]
        error = None
    except Exception as exc:  # noqa: BLE001 - the gate already passed; never block the day
        envelope, problems, error = {}, [], f"{type(exc).__name__}: {str(exc)[:200]}"
    meta = {
        "backend": "review",
        "model": f"review:{name}",
        "wall_ms": int((time.monotonic() - started) * 1000),
        "cost_usd": envelope.get("total_cost_usd"),
        "error": error,
    }
    return problems, meta


def _run(prompt, name):
    command = [os.environ.get("CLAUDE_BIN", "claude"), "--print", "--output-format", "json",
               "--json-schema", json.dumps(SCHEMA), "--model", name, "--tools", "",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
    result = subprocess.run(command, input=prompt, text=True, capture_output=True, cwd=ROOT,
                            timeout=TIMEOUT_SECONDS)
    envelope = json.loads(result.stdout or "{}")
    if result.returncode != 0 or envelope.get("is_error"):
        raise RuntimeError((result.stderr or str(envelope.get("result")))[:200])
    return envelope
