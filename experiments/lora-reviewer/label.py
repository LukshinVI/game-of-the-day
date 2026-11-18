#!/usr/bin/env python3
"""Step 1b: label generated activities with a teacher model — the production reviewer
prompt and schema (review.TIERED_PROMPT / TIERED_SCHEMA), only the model is stronger.

The student is trained on what the teacher *said* (its block/fix split). The production
code applies its rules (review.split) on top of any reviewer, so the rows keep both: the
raw answer as the training target, and the verdict after the rules for analysis.

Resumable: activities that already have a label in --out are skipped, so the dataset can
be built in daily portions (--limit) without touching the production quota reserve.
--double labels a share of the rows a second time to measure how consistent the teacher
itself is: the student cannot be expected to agree with the teacher more than the
teacher agrees with itself.

    python3 experiments/lora-reviewer/label.py --limit 20
    python3 experiments/lora-reviewer/label.py --limit 300 --double 0.2

Spends quota: one teacher call per activity (plus the doubled share).
"""

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from src.preparation import review  # noqa: E402


def prompt_for(row):
    """Exactly the production reviewer prompt for this activity and age."""
    activity = {k: row["activity"].get(k) for k in common.ACTIVITY_FIELDS}
    return review.TIERED_PROMPT.format(age=row["age_months"],
                                       activity=json.dumps(activity, ensure_ascii=False, indent=2))


def teacher(row, model):
    output, cost = common.call(prompt_for(row), model, review.TIERED_SCHEMA)
    clean = lambda key: [str(p).strip() for p in output.get(key, []) if str(p).strip()][:3]  # noqa: E731
    said_block, said_fix = clean("block"), clean("fix")
    blocks, fixes = review.split(said_block, said_fix)
    return {"block": said_block, "fix": said_fix,
            "verdict": "block" if blocks else ("fix" if fixes else "ok"),
            "by_rules": [b for b in blocks if b not in said_block]}, cost


def verdict_of(answer):
    return "block" if answer["block"] else ("fix" if answer["fix"] else "ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="activities to label in this run")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--double", type=float, default=0.0, help="share labelled twice (0..1)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--src", default=str(common.DATA_DIR / "activities.jsonl"))
    parser.add_argument("--out", default=str(common.DATA_DIR / "labels.jsonl"))
    args = parser.parse_args()

    rng = random.Random(args.seed)
    done = {r["id"] for r in common.read_jsonl(args.out)}
    todo = [r for r in common.read_jsonl(args.src) if r["id"] not in done][:args.limit]
    spent = 0.0
    tally = Counter()
    for n, row in enumerate(todo, 1):
        try:
            first, cost = teacher(row, args.model)
            spent += cost
            second = None
            if rng.random() < args.double:
                second, cost = teacher(row, args.model)
                spent += cost
        except Exception as exc:  # noqa: BLE001 - skip and retry on the next run
            print(f"{row['id']}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            continue
        common.append_jsonl(args.out, {**row, "teacher": args.model, "label": first,
                                       **({"label_2": second} if second else {})})
        tally[(row["intended"], verdict_of(first))] += 1
        print(f"{n}/{len(todo)} {row['id']} {row['intended']:>7} -> {verdict_of(first):5}"
              f"{'' if not second else ' / ' + verdict_of(second)}  ${spent:.3f}")

    print(f"\nlabelled {sum(tally.values())} in this run, ${spent:.3f}")
    print("intended -> teacher verdict (raw split):")
    for (intended, verdict), count in sorted(tally.items()):
        print(f"  {intended:>7} -> {verdict:5} {count}")


if __name__ == "__main__":
    main()
