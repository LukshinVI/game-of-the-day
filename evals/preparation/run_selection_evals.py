#!/usr/bin/env python3
"""Selection evals: run the real preparation path (prompt -> model -> validator ->
corrective retries) on synthetic cases and score the result.

Every case is a real model call on the configured backend, so a run spends quota:
it is manual on purpose, never part of CI.

    python3 evals/preparation/run_selection_evals.py --model opus
    python3 evals/preparation/run_selection_evals.py --model sonnet --judge opus
    python3 evals/preparation/run_selection_evals.py --model sonnet --cases mouthing-age-9m,speech-24m

Scores per case:
- validator: the production gate (schema, safety terms, age, duration, duplicates);
- lint: deterministic quality checks the gate does not enforce (step count and length,
  materials, message length, https source, finished goal sentence);
- judge (optional): another model rates age fit, clarity and developmental value 1-5
  and flags safety concerns, against a fixed rubric.

Writes evals/preparation/reports/<date>-<model>.json and a Markdown summary next to it.
"""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.preparation import game_preparation as gp  # noqa: E402

CASES = Path(__file__).with_name("cases.json")
REPORTS = Path(__file__).with_name("reports")

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["age_fit", "clarity", "developmental_value", "safety_concerns", "profile_fit", "notes"],
    "properties": {
        "age_fit": {"type": "integer", "minimum": 1, "maximum": 5},
        "clarity": {"type": "integer", "minimum": 1, "maximum": 5},
        "developmental_value": {"type": "integer", "minimum": 1, "maximum": 5},
        "safety_concerns": {"type": "array", "items": {"type": "string"}},
        "profile_fit": {"type": "integer", "minimum": 0, "maximum": 5},
        "notes": {"type": "string"},
    },
}
JUDGE_PROMPT = """You review one developmental activity proposed for a child aged {age} months.
Rate strictly, 1-5:
- age_fit: realistic for exactly this age (motor and language milestones), neither too hard nor too easy;
- clarity: a tired parent can follow the steps without guessing;
- developmental_value: it actually exercises the declared skill ({skill}), not just keeps the child busy.
List concrete safety_concerns for this age (empty if none).
profile_fit: 0 if no child profile is given; otherwise 1-5 for how well the activity follows
it (difficulty direction for the skill, avoids dislikes, uses likes).
notes: one or two sentences.
Case intent: {why}
Child profile from parents' feedback: {profile}

ACTIVITY (JSON):
{candidate}
"""


def lint(candidate, history):
    problems = []
    steps = candidate.get("steps") or []
    if not 2 <= len(steps) <= 6:
        problems.append(f"steps: {len(steps)} (expected 2-6)")
    if any(len(step) > 220 for step in steps):
        problems.append("a step is longer than 220 characters")
    if len(candidate.get("materials") or []) > 5:
        problems.append("more than 5 materials")
    if len(candidate.get("message_text") or "") > 1500:
        problems.append("message longer than 1500 characters")
    if not str(candidate.get("source_url", "")).startswith("https://"):
        problems.append("source is not https")
    if not (candidate.get("goal") or "").strip().endswith("."):
        problems.append("goal is not a finished sentence")
    return problems


def judge(candidate, case, model):
    prompt = JUDGE_PROMPT.format(age=case["age_months"], skill=candidate.get("skill"), why=case["why"],
                                 profile=json.dumps(case.get("profile") or {}, ensure_ascii=False),
                                 candidate=json.dumps(candidate, ensure_ascii=False, indent=2))
    command = [os.environ.get("CLAUDE_BIN", "claude"), "--print", "--output-format", "json",
               "--json-schema", json.dumps(JUDGE_SCHEMA), "--model", model, "--tools", "",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
    result = subprocess.run(command, input=prompt, text=True, capture_output=True, cwd=ROOT,
                            timeout=gp.MODEL_TIMEOUT_SECONDS)
    envelope = json.loads(result.stdout or "{}")
    verdict = envelope.get("structured_output")
    if result.returncode != 0 or not isinstance(verdict, dict):
        return {"error": (result.stderr or str(envelope.get("result")))[:300]}
    verdict["cost_usd"] = envelope.get("total_cost_usd")
    return verdict


def run_case(case, judge_model=None):
    gp.RUN_LOG.clear()
    gp.PROFILE_CONTEXT = case.get("profile") or {}
    out = io.StringIO()
    record = {"id": case["id"], "age_months": case["age_months"]}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock_alerts():
            candidate, attempts = gp.select_candidate(
                case["history"], date.today().isoformat(), age_months=case["age_months"])
        record.update(passed=True, attempts=attempts, candidate=candidate,
                      lint=lint(candidate, case["history"]))
        if judge_model:
            record["judge"] = judge(candidate, case, judge_model)
    except Exception as exc:  # noqa: BLE001 - a failed case is a result
        record.update(passed=False, attempts=len(gp.RUN_LOG), error=f"{type(exc).__name__}: {str(exc)[:300]}")
    gp.PROFILE_CONTEXT = {}
    runs = list(gp.RUN_LOG)
    record["rejections"] = [e for r in runs if r["outcome"] == "rejected" for e in r["errors"]]
    record["wall_ms"] = sum(r.get("wall_ms") or 0 for r in runs)
    record["cost_usd"] = round(sum(r.get("cost_usd") or 0 for r in runs), 4)
    record["model"] = next((r.get("model") for r in runs if r.get("model")), None)
    return record


@contextlib.contextmanager
def mock_alerts():
    """Evals never message the owner."""
    saved = gp.send_failure_alert
    gp.send_failure_alert = lambda *a, **k: None
    try:
        yield
    finally:
        gp.send_failure_alert = saved


def summary(model, judge_model, records):
    n = len(records)
    passed = [r for r in records if r["passed"]]
    first = sum(1 for r in passed if r["attempts"] == 1)
    lines = [
        f"# Selection evals — {model} — {date.today().isoformat()}",
        "",
        f"- cases: {n}; passed the production gate: {len(passed)}/{n}; on the first attempt: {first}/{n}",
        f"- attempts per case: {sum(r['attempts'] for r in records) / max(n, 1):.2f}",
        f"- clean lint: {sum(1 for r in passed if not r['lint'])}/{len(passed)}",
        f"- time: {sum(r['wall_ms'] for r in records) / 1000 / max(n, 1):.0f} s per case; "
        f"API-equivalent cost: ${sum(r['cost_usd'] for r in records):.2f}",
    ]
    judged = [r["judge"] for r in passed if isinstance(r.get("judge"), dict) and "age_fit" in r["judge"]]
    if judged:
        avg = {k: sum(j[k] for j in judged) / len(judged) for k in ("age_fit", "clarity", "developmental_value")}
        lines.append(f"- judge ({judge_model}): age fit {avg['age_fit']:.1f}, clarity {avg['clarity']:.1f}, "
                     f"developmental value {avg['developmental_value']:.1f} (of 5); "
                     f"cases with safety concerns: {sum(1 for j in judged if j['safety_concerns'])}")
        fits = [j["profile_fit"] for j in judged if j.get("profile_fit")]
        if fits:
            lines.append(f"- profile fit (cases with a profile): {sum(fits) / len(fits):.1f} of 5")
    lines += ["", "| case | gate | attempts | lint | judge | title |", "|---|---|---|---|---|---|"]
    for r in records:
        j = r.get("judge") or {}
        score = f"{j['age_fit']}/{j['clarity']}/{j['developmental_value']}" if "age_fit" in j else "—"
        title = (r.get("candidate") or {}).get("title", r.get("error", ""))[:60]
        lines.append(f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {r['attempts']} | "
                     f"{'; '.join(r.get('lint') or []) or 'ok'} | {score} | {title} |")
    rejections = [e for r in records for e in r["rejections"]]
    if rejections:
        lines += ["", "Validator rejections on the way:", *[f"- {e}" for e in rejections]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="selection-evals")
    parser.add_argument("--model", required=True, help="opus | sonnet | a full model id")
    parser.add_argument("--judge", help="model that scores the result (optional, costs one more call per case)")
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    args = parser.parse_args(argv)

    os.environ["PREPARATION_BACKEND"] = "claude"
    os.environ["PREPARATION_CLAUDE_MODEL"] = args.model
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    records = []
    for case in cases:
        print(f"{case['id']} ...", end=" ", flush=True)
        record = run_case(case, args.judge)
        records.append(record)
        print("pass" if record["passed"] else "FAIL", f"({record['attempts']} att.)", flush=True)

    REPORTS.mkdir(exist_ok=True)
    stem = REPORTS / f"{date.today().isoformat()}-{args.model.replace('/', '_')}"
    stem.with_suffix(".json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    text = summary(args.model, args.judge, records)
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    print()
    print(text)


if __name__ == "__main__":
    main()
