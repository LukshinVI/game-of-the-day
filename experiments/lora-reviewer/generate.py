#!/usr/bin/env python3
"""Step 1a: generate synthetic activities for the reviewer dataset.

Each activity is written from a spec: age, skill and a risk mode chosen by code, so the
dataset is balanced on purpose instead of mirroring what the production selector happens
to propose (which is mostly safe):
- safe:    a careful, well-made activity;
- caution: a fine activity whose surroundings need one check (expected verdict: fix);
- overt:   the activity contains a concrete hazard from HAZARDS, written as if the author
           did not notice it (expected: block);
- subtle:  plausible, but wrong for this age (expected: block).
The mode is kept in the row as `intended`, never as the label: the label comes from the
teacher (label.py), and the gap between the two is itself worth a look.

    python3 experiments/lora-reviewer/generate.py --n 20                 # trial (Sonnet: Haiku would not write unsafe examples)
    python3 experiments/lora-reviewer/generate.py --n 300 --seed 2

Spends quota: one generator call per --batch activities.
"""

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from src.preparation import skills  # noqa: E402

MODES = {"safe": 0.35, "caution": 0.20, "overt": 0.30, "subtle": 0.15}

HAZARDS = [
    "small objects that fit in the mouth (beans, beads, buttons, coins, pebbles, pasta)",
    "a plastic bag or cling film",
    "a ribbon, cord or string long enough to go round the neck",
    "an uninflated or burst balloon",
    "the child on a sofa, bed or table without being held",
    "hot water, a hot cup or a working kettle nearby",
    "button batteries or a remote control with an open battery lid",
    "small magnets",
    "scissors, a knife, toothpicks or needles within reach",
    "food the child may not have tried yet (nuts, honey, whole grapes)",
    "a soft toy with sewn-on plastic eyes or buttons",
    "a pillow or blanket over the baby's face",
    "a bucket or bath with water and the child left alone for a moment",
    "medicine, cosmetics or household chemicals as play material",
]

SUBTLE = [
    "an activity that needs abilities this age does not have yet (sitting, standing, walking) without support",
    "missing head and neck support for a very young baby",
    "a chewable material (paper, crumbly cookie, play dough) for a child who still mouths everything",
    "a toy that is a little too small for this age (smaller than the child's fist)",
    "a tall stack or furniture the child can pull over onto themselves",
]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["activities"],
    "properties": {"activities": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["n", *common.ACTIVITY_FIELDS],
        "properties": {
            "n": {"type": "integer"},
            "title": {"type": "string"},
            "materials": {"type": "array", "items": {"type": "string"}},
            "steps": {"type": "array", "items": {"type": "string"}},
            "safety": {"type": "string"},
            "duration_minutes": {"type": "integer"},
        },
    }}},
}

PROMPT = """You write short developmental activities for parents of young children, in Russian,
in the style of the example below. Write one activity per spec and copy the spec's "n" into
it, so each activity can be matched to its spec.

Purpose: these activities train an automatic safety reviewer. Unsafe ones are negative
examples: no parent ever sees them, and the reviewer cannot learn to stop a hazard it has
never been shown. So when a spec asks for a hazard, really put it in.

Rules for every activity: 5-10 minutes; 1-4 everyday materials; 3-6 short steps addressed
to the parent; a one- or two-sentence safety note. Vary settings and materials: no two
activities alike.

The "mode" of each spec says how careful the activity is. This is for training a safety
reviewer, so follow it exactly and never mention the mode:
- safe: a careful activity, appropriate for the age, with a sensible safety note;
- caution: appropriate, but the surroundings need one check the note does not mention
  (a cord of a blind nearby, a wobbly shelf, tape the child could pull off);
- overt: the given hazard must appear in the materials or the steps, used naturally as if
  the author did not notice the danger (for example the beans are poured into the child's
  hands); the safety note stays generic ("play next to the child") and never warns about
  that hazard;
- subtle: the activity has the given age problem, written naturally; the safety note does
  not mention it.

EXAMPLE (safe, 4 months):
{example}

SPECS:
{specs}
"""

EXAMPLE = {
    "title": "Мягкий мячик в ладошки",
    "materials": ["Однотонный мягкий тканевый мячик крупнее кулака взрослого", "Плотный коврик на полу"],
    "steps": ["Положите малыша на спину на коврик.", "Покажите мячик на уровне груди и назовите его.",
              "Подождите, пока малыш потянется; коснитесь мячиком ладошки.",
              "Дайте подержать, не тяните, хвалите голосом."],
    "safety": "Играйте рядом и не оставляйте малыша одного с мячиком.",
    "duration_minutes": 5,
}


def specs(n, rng):
    out = []
    for _ in range(n):
        age = rng.randint(1, 36)
        mode = rng.choices(list(MODES), weights=list(MODES.values()))[0]
        spec = {"age_months": age, "skill": rng.choice(skills.for_age(age)), "mode": mode}
        if mode == "overt":
            spec["hazard"] = rng.choice(HAZARDS)
        elif mode == "subtle":
            spec["problem"] = rng.choice(SUBTLE)
        spec["n"] = len(out) + 1
        out.append(spec)
    return out


def generate(batch, model):
    prompt = PROMPT.format(example=json.dumps(EXAMPLE, ensure_ascii=False, indent=2),
                           specs=json.dumps(batch, ensure_ascii=False, indent=2))
    output, cost = common.call(prompt, model, SCHEMA)
    return output.get("activities") or [], cost


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--batch", type=int, default=5)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default=str(common.DATA_DIR / "activities.jsonl"))
    args = parser.parse_args()

    rng = random.Random(args.seed)
    evals = common.eval_activities()
    plan = specs(args.n, rng)
    kept = dropped = 0
    spent = 0.0
    for i in range(0, len(plan), args.batch):
        batch = plan[i:i + args.batch]
        try:
            activities, cost = generate(batch, args.model)
        except Exception as exc:  # noqa: BLE001 - one bad batch must not lose the rest
            print(f"batch {i // args.batch + 1}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            continue
        spent += cost
        by_n = {a.get("n"): a for a in activities}
        missing = [s["n"] for s in batch if s["n"] not in by_n]
        if missing:
            print(f"batch {i // args.batch + 1}: no activity for specs {missing}", file=sys.stderr)
        for spec in batch:
            if spec["n"] not in by_n:
                continue
            activity = by_n[spec["n"]]
            activity = {k: activity.get(k) for k in common.ACTIVITY_FIELDS}
            if common.leaks(activity, evals):
                dropped += 1
                continue
            common.append_jsonl(args.out, {
                "id": uuid.uuid4().hex[:12], "age_months": spec["age_months"], "skill": spec["skill"],
                "intended": spec["mode"], "seed_detail": spec.get("hazard") or spec.get("problem"),
                "generator": args.model, "activity": activity,
            })
            kept += 1
        print(f"batch {i // args.batch + 1}: kept {kept}, dropped as eval-like {dropped}, ${spent:.3f}")
    print(f"done: {kept} activities -> {args.out}; dropped {dropped}; ${spent:.3f}")


if __name__ == "__main__":
    main()
