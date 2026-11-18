# Experiment: a LoRA safety reviewer

Question: can a small open model with a LoRA adapter do the job of the production safety
reviewer (Claude Haiku, `src/preparation/review.py`) as well, measured by the same
hand-labelled safety evals (`evals/safety/`)?

Not part of production: nothing in `src/` imports this folder, and it never writes to the
production database. In production the reviewer stays as it is; a student model could at
most run in shadow next to it after a long comparison.

## Plan

1. **Data** — synthetic activities (`generate.py`) labelled by a teacher model with the
   production reviewer prompt and schema (`label.py`).
2. **Baseline** — the base model without training, through the safety evals.
3. **Training** — QLoRA (PEFT/TRL) on the labelled rows.
4. **Serving and evaluation** — the adapter in vLLM, the safety evals ×3 against Haiku:
   recall on unsafe cases (the bar is 100%), false alarms, latency, cost.
5. Optional — DPO on the student's mistakes; a second adapter on the same base
   (multi-LoRA serving).
6. Report and ADR.

## Data rules

- The 27 hand-labelled cases of `evals/safety/cases.json` are the test set and never go
  into training: `common.leaks()` drops generated activities too close to any of them.
- Synthetic only: no production games or family data in the training rows.
- Data lives outside the repository, in `LORA_DATA_DIR` (default
  `/var/lib/game-of-the-day/lora`), as JSONL: one row per activity.
- The generator's intended risk (`intended`) is kept for analysis, never used as the label.
  The label is the teacher's answer; the training target is what the teacher said (its
  block/fix split), and the verdict after the production rules (`review.split`) is kept
  alongside.

## Running

On the host, as the service user (the model calls use the production subscription token):

```sh
python3 experiments/lora-reviewer/generate.py --n 300 --model sonnet --seed 2
python3 experiments/lora-reviewer/label.py --limit 300 --double 0.2
```

Both spend quota, so the dataset is built in daily portions that leave the production
reserve intact; `label.py` is resumable.

## Findings so far

Trial on 2026-11-18 (30 activities):

- Haiku as the generator returned fewer activities than asked and, asked for an unsafe
  example (a plastic bag, a pillow over the face), wrote a safe one instead, often with a
  warning: 4 of 10 specs filled. Without unsafe examples there is nothing to teach.
  Sonnet filled 10 of 10 with the hazard really in the materials or steps. Activities are
  now matched to their spec by number, not by position.
- Teacher (Sonnet): every overt hazard blocked (3/3); one of two age problems blocked, the
  other (standing on one leg to dress at 21 months) given as a caution; no safe activity
  blocked, all of them got a caution. Doubled rows agreed 5/5.
- Cost: about $0.006 per generated and $0.012 per labelled activity, so roughly $27 of
  quota for 1500 rows.

First portions on 2026-11-18: 390 labelled rows (block 201, fix 173, ok 16), ages spread
evenly over 1-36 months; about 5% of the weekly Pro quota for 400 rows.

- The teacher agreed with itself on 68 of 78 doubled rows (87%): the ceiling for a student
  trained on its labels.
- Age problems looked "missed" (subtle -> fix in about half the rows), but the generator
  was wrong, not the teacher: problems were drawn for any age ("missing head support" for
  a 12-month-old is no problem). The intended mode is not ground truth. Age problems are
  now drawn only for the ages where they are real (`SUBTLE` ranges).
