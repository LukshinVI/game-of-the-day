# AI design

Game of the Day uses models for language, not for state. Every model call has a schema,
a deterministic check after it, a fallback when it fails, a budget, and a measurement.

## Where models are used

| use | model | input | output | checked by | if it fails |
|---|---|---|---|---|---|
| propose the day's activity | Claude Sonnet (Claude Code headless, web search) | age in months, skills offered at that age with recent counts, history, the child's profile | candidate JSON (schema) | the gate: schema, age, duration, safety terms, safety length, skill band and rotation, duplicates; corrective retry with the reasons | Haiku under the same gate, then a library game chosen by code |
| review safety after the gate | Claude Haiku | the activity, the child's age | serious, concrete problems only | a finding rejects like a gate error and reaches the retry; a reviewer outage never blocks | the gate result stands |
| extract observations from feedback | Claude Haiku | the feedback text, the game | observations with verbatim quotes; wishes about the service | quote must be verbatim in the text; enums; caps per child and per day | feedback stays pending, retried next run |
| classify VK feedback semantically | Gemma 4 on Workers AI | application-segmented feedback | classification of existing segments | deterministic validation, policy, guarded target linking | raw feedback remains stored |
| judge evals (offline) | Claude Opus | an activity and the case intent | age fit, clarity, value, safety concerns, profile fit | — | — |

The child's name and birth date never reach a model; only the age in months does.

## Selection: propose, verify, degrade

```text
age → skills.for_age() ─┐
history → recent skills ├→ prompt → model → gate → reviewer ──ok──→ game (origin: model)
profile (code-built) ───┘               │ reject: reasons → retry (×3)
                                        └ fail → fallback model → gate → game (origin: fallback_model)
                                                   └ fail / quota / budget → library (origin: library)
```

- **Skills by age.** Eight skills open by age (movement, senses, emotions and social play
  and speech from birth; fine motor from 3 months; thinking from 4; self-care from 12;
  imagination from 18). The model sees only those; the validator rejects anything else
  and a skill used in both of the last two games.
- **Safety by design.** The prompt asks for safe materials, positioning in the steps,
  a clear floor and explicit setup steps; the gate rejects hazardous terms and long
  generic safety notes. Evals measured the effect (judged safety concerns 6/6 → 4/6).
- **Required skill.** "Too easy" (or "too hard") on the latest game makes the next game
  train the same skill one step harder (easier); a gate rule, not a hint.
- **Second safety line.** A small model reviews what passed the gate against an age
  checklist and reports only serious, concrete problems. In evals it flagged 2 of 7 cases,
  all valid, and the judge found nothing left in them afterwards.
- **Library.** Validated past games and a warm reserve per age, chosen by code: age window,
  skill rules, nothing the child already had, least recent skill first.

## Personalization without model memory

```text
feedback → Haiku extracts observations (each with a quote) → code checks the quote
→ code builds the profile (60 days, recency-weighted) → selection prompt follows it
```

The model proposes facts; the application decides what is remembered and how it is used.
The profile says, per skill, whether the next game should be harder or easier, what went
well, what is hard for now, likes and dislikes. A request about the child's own games ("more games with objects")
is a like and goes to the profile; a wish about the service itself ("send them in the
evening") is routed to the owner, not to the profile. The parent gets one reply saying what
was taken into account, built by code from the kept observations.

## The semantic feedback agent (VK)

```text
feedback text → deterministic segmentation and provenance → model classifies the
application's segments → deterministic validation → policy → guarded target linking
```

The model cannot create evidence spans; metadata is context, not evidence; an unresolved
reference stays unresolved instead of being guessed.

## Measuring

- **Every attempt is recorded** (`model_runs`): model, latency, turns, tokens, web
  searches, API-equivalent cost, outcome, the gate's rejection reasons — also on failed
  days. The weekly report compares models and counts reserve days.
- **Selection evals** (`evals/preparation`): synthetic cases (2–36 months, a skill streak,
  mouthing age, a long history, a profile) through the real selection path, scored by the
  gate, deterministic lint and a model judge. They decided the switch from Opus to Sonnet
  (same gate results, close quality, half the quota) and verified the safety prompt.
- **Feedback agent evals** (`worker/evals`): deterministic segmentation on every push, LLM
  evals on demand across models.

## Cost

A subscription and a free tier, no paid overage. Ceilings: a daily preparation budget,
feedback analysis caps, a weekly cap on reserve generation, evals only by hand. When a
ceiling is hit the system degrades and says so; it does not spend more.

## Why this shape

The probabilistic part is kept narrow and replaceable: each model has a small contract, a
deterministic check behind it and a path that works without it. That is what made it
possible to change the selection model on evidence, add personalization without letting
the model own memory, and keep delivering on days the model is unavailable.
