"""Shared pieces of the LoRA reviewer experiment: paths, the model call, JSONL I/O and
the leak check against the hand-labelled safety evals.

Nothing here is imported by production code, and nothing here writes to the production
database: the experiment reads production only through its public modules (the reviewer
prompt and schema), and keeps its data outside the repository (DATA_DIR)."""

import difflib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.preparation import review  # noqa: E402

DATA_DIR = Path(os.environ.get("LORA_DATA_DIR", "/var/lib/game-of-the-day/lora"))
EVAL_CASES = ROOT / "evals" / "safety" / "cases.json"

# The fields the production reviewer reads (review.review_tiered).
ACTIVITY_FIELDS = ("title", "materials", "steps", "safety", "duration_minutes")


def call(prompt, model, schema):
    """One structured model call through the same CLI path as the production reviewer.
    Returns (output, cost_usd)."""
    envelope = review._run(prompt, model, schema)
    return envelope.get("structured_output") or {}, envelope.get("total_cost_usd") or 0.0


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def eval_activities():
    return [c["activity"] for c in json.loads(EVAL_CASES.read_text(encoding="utf-8"))["cases"]]


def _materials(activity):
    return {m.lower().strip() for m in activity.get("materials") or []}


def leaks(activity, evals=None, title_ratio=0.75, materials_jaccard=0.6):
    """True when the activity is too close to a hand-labelled eval case: such rows never
    go into training, or the eval would measure memory instead of judgement."""
    title = (activity.get("title") or "").lower()
    mats = _materials(activity)
    for case in evals if evals is not None else eval_activities():
        if difflib.SequenceMatcher(None, title, case["title"].lower()).ratio() >= title_ratio:
            return True
        other = _materials(case)
        if mats and other and len(mats & other) / len(mats | other) >= materials_jaccard:
            return True
    return False
