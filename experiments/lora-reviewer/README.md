# Experiment: a LoRA safety reviewer

Question: can a small open model with a LoRA adapter do the job of the production safety
reviewer (Claude Haiku, `src/preparation/review.py`) as well, measured by the same
hand-labelled safety evals (`evals/safety/`)?

Not part of production: nothing in `src/` imports this folder, and it never writes to the
production database. In production the reviewer stays as it is; a student model could at
most run in shadow next to it after a long comparison.

Status (2026-11-19): steps 1–3 done, step 4 measured with transformers on a rented GPU
instead of vLLM; see [Results](#results-2026-11-19). The bar is not reached yet.

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

Then `export.py` writes `train.jsonl` / `val.jsonl` (stratified by the teacher's verdict,
prompt-completion format), and `train_reviewer.ipynb` runs steps 2-4 in Google Colab on a
T4: the base model through the safety evals and val, QLoRA training, the same
measurement after. [Open in Colab](https://colab.research.google.com/github/LukshinVI/game-of-the-day/blob/main/experiments/lora-reviewer/train_reviewer.ipynb).
The notebook cells were dry-run on CPU with a tiny model up to training; the training cell
itself could not be (the host's Python 3.14 breaks `datasets`), Colab runs Python 3.12.

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

## Results, 2026-11-19

Trained on a rented RTX 4090 (Python 3.12 with the notebook's pinned libraries), 351
training rows, 39 val rows. "Unsafe caught" and "safe blocked" are counted on the 27
hand-labelled safety evals: the model alone, and with the production rules
(`review.split`) after it. Val is block / not-block agreement with the teacher, whose
ceiling is 87%. Each run changes one setting from the first run (7B, r=16, 2 epochs).

| Run | Unsafe caught (model / +rules) | Safe blocked (model / +rules) | Invalid JSON | Val |
|---|---|---|---|---|
| Qwen2.5-7B, no training | 10/15 / 10/15 | 3/12 / 3/12 | 0 | 79% |
| 7B r=16, 2 epochs | 13/15 / 14/15 | 0/12 / 2/12 | 0 | 82% |
| 7B r=8 | 13/15 / 13/15 | 0/12 / 1/12 | 0 | 82% |
| 7B r=32 | 13/15 / 14/15 | 0/12 / 2/12 | 0 | 87% |
| 7B, 1 epoch | 12/15 / 12/15 | 1/12 / 2/12 | 0 | 77% |
| 7B, 3 epochs | 13/15 / 13/15 | 1/12 / 2/12 | 3 | 87% |
| Qwen2.5-3B, no training | 1/15 / 2/15 | 0/12 / 0/12 | 0 | 59% |
| 3B r=16, 2 epochs | 13/15 / 13/15 | 1/12 / 1/12 | 0 | 77% |
| Production reviewer (Haiku, 18.11) | 98% | 0% / 11% | - | - |

- Training works: the 7B student goes from 10/15 to 13-14/15 unsafe caught and stops
  blocking safe games on its own; the 3B one goes from 1/15 to 13/15.
- No run reaches the bar (every unsafe case caught), so no student replaces the
  production reviewer yet.
- The changing table at 7 months (a fall from height) is missed by every run, whatever the
  settings: only 4 of 351 training rows mention a changing table. The next step is data
  (more fall-from-height cases), not tuning.
- r=32 matches the teacher's own ceiling on val (87%) with the same safety-eval result as
  r=16; 3 epochs reach 87% too but start breaking the format (3 invalid answers): the
  first sign of overfitting. 1 epoch is too little.
- The two false alarms of the trained 7B (finger paint, pouring water) come from the
  production rules, not the model, as with Haiku.
- A notebook bug found on the first run: measured in the same process right after
  training, every answer came out unparseable (0/15, 27 invalid). The saved adapter was
  fine when loaded again. The model is now switched back to eval mode with left padding
  after training (step 4), and every later run measured correctly in process.

Adapters and logs: `LORA_DATA_DIR/runs/` on the server (not in git).
