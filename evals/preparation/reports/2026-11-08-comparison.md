# Opus vs Sonnet for daily selection — 2026-11-08

Six synthetic cases, eight age-banded skills, the same prompt and validator. Opus judged
both runs, so treat its scores as indicative: a model may prefer its own output, and six
cases are a small sample.

| | Opus | Sonnet |
|---|---|---|
| passed the production gate | 6/6, all on the first attempt | 6/6, all on the first attempt |
| judge: age fit / clarity / value (of 5) | 4.8 / 4.5 / 4.8 | 4.7 / 4.0 / 4.7 |
| cases with judge safety concerns | 4/6 | 6/6 |
| time per case | 36 s | 21 s |
| API-equivalent cost per case | $0.10 | $0.05 |

Observations
- Skill rotation works for both: after three fine-motor games both switched to speech; the
  new skills (cognitive, sensory, social_emotional, imagination) were used where they fit.
- The weakest dimension for **both** models is the safety note, not the activity: the judge
  repeatedly found practical gaps the validator cannot see (detachable eyes on soft toys,
  paper that can be chewed, supporting a 2-month-old's head, a firm surface, taping paper
  down before jumping). This is a prompt problem, not a model problem.

Recommendation
- Move daily selection to **Sonnet**: the same gate results and close quality at half the
  quota and nearly twice the speed. Opus stays the judge for evals.
- Next: a concrete safety checklist in the prompt (detachable parts, surfaces and falls,
  head and sitting support by age, chewable materials), then re-run these evals.
- The weekly `/llm` report watches the switch on real days.

## Follow-up: Sonnet with the safety-by-design prompt (a54dc61)

| | Sonnet, before | Sonnet, after |
|---|---|---|
| production gate | 6/6, first attempt | 6/6, first attempt |
| judge: age fit / clarity / value | 4.7 / 4.0 / 4.7 | 5.0 / 4.2 / 4.7 |
| cases with safety concerns | 6/6 | 4/6 |
| clean lint | 4/6 | 5/6 |

The infant cases (2 and 6 months) are now clean; the remaining concerns are second-order
(sharing a cup with a toy, a basket a child could climb into). Daily selection switched to
Sonnet with this prompt on 2026-11-08.

## Follow-up: the safety reviewer (Haiku after the gate, 011e200)

Seven cases (the six above and the profile case), Sonnet with the reviewer, Opus judging.

- The reviewer flagged 2 of 7 cases, 5 findings, all concrete and valid on reading: pillows
  around a baby who cannot sit steadily, a cloth that can cover a 9-month-old's face, a
  board book left to chew, real food entering a pretend tea party, small lids on a toy tea
  set. After the corrective retries the judge found **no** safety concerns in those cases.
- No false alarms seen: the 5 cases it passed went through on the first attempt.
- It does not replace the judge: 4 of 7 cases still had judge concerns, mostly missing
  advice (sleep on the floor, drinking upright, furniture corners) rather than hazards.
- Cost: attempts per case 1.43 (from 1.00); about $0.07 per game instead of $0.05.
  Side effect: retries tend to add steps (lint "7 steps" twice).

Recommendation: enable in production (`PREPARATION_REVIEW_MODEL=haiku`) and watch the
review rate in the weekly report.
