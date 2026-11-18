#!/usr/bin/env python3
"""Step 1c: turn labelled rows into training files for the notebook.

Each example is the production reviewer prompt (review.TIERED_PROMPT) plus FORMAT, a
line asking for the JSON the production CLI gets through --json-schema, and the
teacher's raw split as the answer. Conversational prompt-completion format, so the loss
is computed on the answer only.

The split is stratified by the teacher's verdict and seeded: val.jsonl measures how
often the student agrees with the teacher on unseen rows. The hand-labelled safety
evals stay the real test; the notebook reads them from the repository.

    python3 experiments/lora-reviewer/export.py            # -> LORA_DATA_DIR/train.jsonl, val.jsonl
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import label  # noqa: E402

FORMAT = ('\nAnswer with JSON only, no other text: {"block": [...], "fix": [...]} '
          "(each list at most 3 items, empty lists when the activity is fine).")


def example(row):
    answer = {"block": row["label"]["block"], "fix": row["label"]["fix"]}
    return {
        "id": row["id"],
        "verdict": label.verdict_of(row["label"]),
        "prompt": [{"role": "user", "content": label.prompt_for(row) + FORMAT}],
        "completion": [{"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}],
    }


def split(rows, val_share, seed):
    by_verdict = defaultdict(list)
    for row in rows:
        by_verdict[label.verdict_of(row["label"])].append(row)
    rng = random.Random(seed)
    train, val = [], []
    for verdict in sorted(by_verdict):
        group = by_verdict[verdict]
        rng.shuffle(group)
        cut = round(len(group) * val_share)
        val += group[:cut]
        train += group[cut:]
    rng.shuffle(train)
    return train, val


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", default=str(common.DATA_DIR / "labels.jsonl"))
    parser.add_argument("--out", default=str(common.DATA_DIR))
    parser.add_argument("--val", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    evals = common.eval_activities()
    rows = [r for r in common.read_jsonl(args.src) if not common.leaks(r["activity"], evals)]
    train, val = split(rows, args.val, args.seed)
    out = Path(args.out)
    for name, part in (("train", train), ("val", val)):
        path = out / f"{name}.jsonl"
        path.write_text("".join(json.dumps(example(r), ensure_ascii=False) + "\n" for r in part), encoding="utf-8")
        counts = defaultdict(int)
        for r in part:
            counts[label.verdict_of(r["label"])] += 1
        print(f"{path}: {len(part)} ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})")


if __name__ == "__main__":
    main()
