#!/usr/bin/env python3
"""Safety evals: both safety lines on labelled activities whose right answer is known.

The selection evals score what the model proposes with another model, which leaves the
question "how good is the judge?" open. Here the answer is fixed in advance by a person:
cases.json holds activities written by hand, each labelled 'unsafe' or 'safe'. Every
case goes through
- the dangerous-term gate (deterministic, free) — the same check the production
  validator runs on title, mechanics, materials and steps;
- the tiered safety reviewer (the production prompt and model), --repeat times, because
  a model's answer can change between runs.

An unsafe case counts as caught when the gate fires or the reviewer blocks; a safe case
is a false alarm when either of them does (a reviewer 'fix', an added caution, is not a
block). Consistency: the share of cases where every repeat gave the same verdict.

    python3 evals/safety/run_safety_evals.py                    # PREPARATION_REVIEW_MODEL or haiku, 3 repeats
    python3 evals/safety/run_safety_evals.py --model sonnet --repeat 5
    python3 evals/safety/run_safety_evals.py --out /tmp/reports

Spends quota (one reviewer call per case and repeat): manual, never part of CI.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.preparation import game_preparation as gp  # noqa: E402
from src.preparation import review  # noqa: E402

CASES = Path(__file__).with_name("cases.json")
REPORTS = Path(__file__).with_name("reports")


def gate(activity):
    """The production dangerous-term check, alone: the other gate rules (schema, links,
    age) are about form and would fail these hand-written activities for other reasons."""
    candidate = {"title": activity["title"], "mechanics": "", "materials": activity["materials"],
                 "steps": activity["steps"], "message_text": "x"}
    return [e.split(": ", 1)[1] for e in gp.validate_candidate(candidate, [], None)
            if e.startswith("dangerous-term gate triggered")]


def run_case(case, repeat, runner=None):
    activity = case["activity"]
    record = {"id": case["id"], "label": case["label"], "age_months": case["age_months"],
              "gate": gate(activity), "runs": []}
    for _ in range(repeat):
        blocks, fixes, meta = review.review_tiered(activity, case["age_months"], runner=runner)
        record["runs"].append({"block": blocks, "fix": fixes, "error": (meta or {}).get("error"),
                               "cost_usd": (meta or {}).get("cost_usd") or 0})
    return record


def verdicts(record):
    """Per repeat: True when the chain stops the activity (gate or reviewer block)."""
    return [bool(record["gate"]) or bool(run["block"]) for run in record["runs"] if not run["error"]]


def summary(model, repeat, records):
    unsafe = [r for r in records if r["label"] == "unsafe"]
    safe = [r for r in records if r["label"] == "safe"]

    def share(rows, pick):
        """Mean over repeats of the share of rows for which pick(row, run) holds."""
        values = [pick(r, run) for r in rows for run in r["runs"] if not run["error"]]
        return sum(values) / len(values) if values else 0.0

    by_gate = sum(1 for r in unsafe if r["gate"])
    reviewer_recall = share(unsafe, lambda r, run: bool(run["block"]))
    chain_recall = share(unsafe, lambda r, run: bool(r["gate"]) or bool(run["block"]))
    reviewer_fp = share(safe, lambda r, run: bool(run["block"]))
    gate_fp = sum(1 for r in safe if r["gate"])
    chain_fp = share(safe, lambda r, run: bool(r["gate"]) or bool(run["block"]))
    with_fix = share(safe, lambda r, run: bool(run["fix"]) and not run["block"])
    stable = sum(1 for r in records if len(set(verdicts(r))) == 1)
    errors = sum(1 for r in records for run in r["runs"] if run["error"])
    cost = sum(run["cost_usd"] for r in records for run in r["runs"])
    missed = [r["id"] for r in unsafe if not all(verdicts(r))]
    alarms = [r["id"] for r in safe if any(verdicts(r))]

    lines = [
        f"# Safety evals — reviewer {model} — {date.today().isoformat()}",
        "",
        f"{len(unsafe)} unsafe and {len(safe)} safe hand-labelled activities, {repeat} reviewer runs each.",
        "",
        "| | unsafe caught (recall) | safe blocked (false alarms) |",
        "|---|---|---|",
        f"| dangerous-term gate | {by_gate}/{len(unsafe)} | {gate_fp}/{len(safe)} |",
        f"| reviewer (block) | {reviewer_recall:.0%} | {reviewer_fp:.0%} |",
        f"| **both lines** | **{chain_recall:.0%}** | **{chain_fp:.0%}** |",
        "",
        f"- safe activities that passed with an added caution (fix): {with_fix:.0%}",
        f"- same verdict in every repeat: {stable}/{len(records)} cases",
        f"- reviewer errors: {errors}; API-equivalent cost: ${cost:.2f}",
        f"- unsafe not caught in every run: {', '.join(missed) or 'none'}",
        f"- safe blocked at least once: {', '.join(alarms) or 'none'}",
        "",
        "| case | label | gate | reviewer blocks per run | example |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        blocks = "/".join(str(len(run["block"])) if not run["error"] else "err" for run in r["runs"])
        example = next((run["block"][0] for run in r["runs"] if run["block"]),
                       next((run["fix"][0] for run in r["runs"] if run["fix"]), ""))
        lines.append(f"| {r['id']} | {r['label']} | {', '.join(r['gate']) or '—'} | {blocks} | "
                     f"{example[:110].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="safety-evals")
    parser.add_argument("--model", default=os.environ.get("PREPARATION_REVIEW_MODEL") or "haiku")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--out", type=Path, default=REPORTS, help="report directory")
    args = parser.parse_args(argv)

    os.environ["PREPARATION_REVIEW_MODEL"] = args.model
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    records = []
    for case in cases:
        print(f"{case['id']} ...", end=" ", flush=True)
        record = run_case(case, args.repeat)
        records.append(record)
        print("stopped" if all(verdicts(record)) else "passed" if not any(verdicts(record)) else "mixed",
              flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{date.today().isoformat()}-safety-{args.model.replace('/', '_')}"
    stem.with_suffix(".json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    text = summary(args.model, args.repeat, records)
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    print()
    print(text)


if __name__ == "__main__":
    main()
