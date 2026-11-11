"""Second safety line: a model reviews an activity that already passed the gate.

The deterministic gate catches what words can catch (hazardous terms, length, age,
skills, duplicates). The evals showed what it cannot: a soft toy with button eyes for a
9-month-old, paper that can be chewed, no head support for a 2-month-old. A small model
reads the activity against a fixed checklist for the child's age and reports only
serious, concrete problems. A finding rejects the candidate like a gate error, so the
corrective retry gets the reasons; the fallback chain still applies.

Enabled by PREPARATION_REVIEW_MODEL (for example "haiku"); unset means off. A reviewer
failure (outage, malformed output) never blocks the day: the gate already passed.

Tiers (PREPARATION_REVIEW_TIERS=1). Before them every finding rejected the game, and in
production 53% of reviewed candidates were rejected, mostly for a missing caution ("keep
blind cords out of reach"); each retry invented a new game with a new small risk and the
day fell back to the library. With tiers the reviewer splits findings:
- block: the hazard is the activity itself (small parts, cloth over the face as the
  mechanic, a cord at the neck, height without support, hot or toxic): rejected, and the
  retry repairs the same game;
- fix: the game is fine with one added caution: the caution goes into the game's safety
  text, which the parent reads.
Code does not trust the split blindly: a fix that names a hazardous material (batteries,
heat, boiling water, toxic, magnets) is a block, and more than MAX_FIXES fixes is a block
too. Consequence words (swallow, neck, suffocate) are not checked: almost every caution
explains itself with them ("…so that the child does not swallow it"), and in the replay
on production candidates they turned good games into blocks.
"""

import json
import os
import re
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

MAX_FIXES = 2
# A "fix" that names one of these materials is a real hazard: treated as a block whatever
# the reviewer said.
ESCALATE = re.compile(r"батаре|горяч|кипят|кипящ|ожог|токсич|ядовит|магнит|химич|лекарств", re.IGNORECASE)

TIERED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["block", "fix"],
    "properties": {
        "block": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 240}},
        "fix": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 240}},
    },
}

TIERED_PROMPT = """You are a safety reviewer for developmental activities for young children.
A parent will do this activity with a child aged {age} months, under supervision.

Report only SERIOUS, CONCRETE problems that a careful parent could miss and that could hurt
this child at this age. Put each one in exactly one list:

block: the danger is part of the activity itself and one added sentence cannot remove it:
small or detachable parts the child can put in the mouth; cloth or a bag over the face as
part of the game; a cord, ribbon or strap that can go round the neck as part of the game;
a baby on a raised surface or a child who cannot sit steadily left unsupported; missing
head and neck support under 4 months; hot, sharp, toxic materials, batteries, food the
child may not have had. Under 6 months, any toy with sewn-on or attached parts (eyes,
buttons, ribbons, bells) is a block: ask for a toy without such parts.

fix: the activity is fine, but the parent needs one concrete caution about the
surroundings or a check before playing (for example: blind cords tied up out of reach,
tape not left where the child can pull it off, a toy checked for loose seams). Write it as
one short sentence in Russian addressed to the parent, ready to be added to the game's
safety notes.

Do not report style, clarity, nice-to-have advice, or anything the activity already
handles. Both lists empty means the activity is fine. Each item: one sentence in Russian.

ACTIVITY (JSON):
{activity}
"""

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


def tiers_enabled():
    return os.environ.get("PREPARATION_REVIEW_TIERS", "").strip() == "1"


def split(blocks, fixes):
    """Apply the code's rules on top of the reviewer's split."""
    blocks = [b for b in blocks if b]
    escalated = [f for f in fixes if ESCALATE.search(f)]
    fixes = [f for f in fixes if f and f not in escalated]
    blocks += escalated
    if len(fixes) > MAX_FIXES:
        blocks, fixes = blocks + fixes, []
    return blocks, fixes


def review_tiered(candidate, age_months, runner=None):
    """Return (blocks, fixes, meta). Both empty when the activity is fine or review is off."""
    name = model()
    if not name:
        return [], [], None
    activity = {k: candidate.get(k) for k in ("title", "materials", "steps", "safety", "duration_minutes")}
    prompt = TIERED_PROMPT.format(age=age_months, activity=json.dumps(activity, ensure_ascii=False, indent=2))
    started = time.monotonic()
    try:
        envelope = (runner or _run)(prompt, name, TIERED_SCHEMA)
        output = envelope.get("structured_output") or {}
        clean = lambda key: [str(p).strip() for p in output.get(key, []) if str(p).strip()][:3]  # noqa: E731
        blocks, fixes = split(clean("block"), clean("fix"))
        error = None
    except Exception as exc:  # noqa: BLE001 - the gate already passed; never block the day
        envelope, blocks, fixes, error = {}, [], [], f"{type(exc).__name__}: {str(exc)[:200]}"
    meta = {
        "backend": "review",
        "model": f"review:{name}",
        "wall_ms": int((time.monotonic() - started) * 1000),
        "cost_usd": envelope.get("total_cost_usd"),
        "error": error,
    }
    return blocks, fixes, meta


def with_fixes(candidate, fixes):
    """The candidate with the reviewer's cautions appended to its safety notes."""
    if not fixes:
        return candidate
    safety = str(candidate.get("safety") or "").strip()
    added = " ".join(f if f.endswith((".", "!")) else f + "." for f in fixes)
    return {**candidate, "safety": f"{safety} {added}".strip()}


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


def _run(prompt, name, schema=None):
    command = [os.environ.get("CLAUDE_BIN", "claude"), "--print", "--output-format", "json",
               "--json-schema", json.dumps(schema or SCHEMA), "--model", name, "--tools", "",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
    result = subprocess.run(command, input=prompt, text=True, capture_output=True, cwd=ROOT,
                            timeout=TIMEOUT_SECONDS)
    envelope = json.loads(result.stdout or "{}")
    if result.returncode != 0 or envelope.get("is_error"):
        raise RuntimeError((result.stderr or str(envelope.get("result")))[:200])
    return envelope
