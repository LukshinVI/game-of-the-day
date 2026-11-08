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
