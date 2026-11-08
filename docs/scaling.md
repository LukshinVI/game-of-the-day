# Scaling

The service runs for a few families on one host on purpose ([ADR 0001](adr/0001-sqlite-on-one-host.md)).
This page names what breaks first as the number of children grows, with numbers from
production and the evals, and what would change at each step. None of it is built: it is
the plan, so that today's simplicity is a choice with a known ceiling, not an accident.

## Where the ceilings are today

| resource | today | ceiling | what sets it |
|---|---|---|---|
| active children | a few | **20** | `GAME_MAX_ACTIVE_CHILDREN`, a deliberate cap: each child is a model run a day |
| model budget | — | **≈ 40–60 games/day** | `PREPARATION_DAILY_BUDGET_USD` = 3 USD at ≈ 0.05–0.07 USD per game; the rest goes to the library |
| preparation time | ≈ 21 s per attempt, ≈ 1.4 attempts per game | **≈ 70 children per run** | children are prepared one after another; the unit has `TimeoutStartSec=35min`; runs at 07:00, 08:00, 09:00 MSK pick up only what is missing |
| model quota | one subscription | not a fixed number | the subscription's usage limits; exhaustion degrades to the library ([ADR 0003](adr/0003-degrade-instead-of-paid-overage.md)) |
| delivery | one message per recipient at 10:00 | messenger rate limits | sequential sends; Telegram allows about 30 messages per second for a bot |
| database | a few thousand rows | far away | SQLite with one writer handles this write load by orders of magnitude |
| availability | one host | the host | recovery point 24 h (nightly off-site copy), recovery time hours (rebuild from the repository) |

The first walls are **cost and preparation time**, not the database. That shapes the
order of the steps below.

## Step 1 — up to ~100 children: same architecture

- Prepare **the evening before** for the next day instead of the same morning: the window
  grows from three hours to more than twelve, and a failure has a whole night to recover.
- Prepare children **concurrently** with a small bound (for example 4), keeping the
  per-child transaction and idempotency as they are.
- Raise the cap and make the model budget a planned monthly line (the revisit condition
  of ADR 0003).
- Put the SLIs from [slo.md](slo.md) into the weekly report and alert on burn rate.

## Step 2 — up to ~1 000 children: reuse instead of generating per child

Generating a game per child costs ≈ 60 USD a day at this size. Most of that is waste:
children of the same age and skill can share well-made games.

- Turn the library into the primary source: a **catalogue per (age band, skill,
  mechanic)**, generated and safety-reviewed once, rated by the feedback of every family
  that played it.
- Personalize by **selection** from the catalogue (the child's profile, history, rotation
  rules — already deterministic code), and generate only for gaps. Model cost then grows
  with the catalogue, not with the number of children.
- Preparation becomes a queue of per-child jobs with workers; delivery sends in batches
  with backoff per channel.
- Feedback insights already have per-child and per-day caps; at this size the caps become
  a budget per family tier.

## Step 3 — ~10 000 children: leave the single host

- **Availability, not throughput, forces the move**: one host with a 24-hour recovery point
  is not acceptable for thousands of families. Move state to managed Postgres with
  point-in-time recovery; the schema and the additive-migration rule carry over.
- Stateless workers (preparation, delivery, insights) on two or more hosts; bots switch
  from long polling to webhooks behind the existing edge.
- Observability moves from owner messages to metrics and on-call alerting; Telegram stays
  for the product, not for operations.
- Children's personal data at this scale needs formal compliance work (storage location,
  retention, consent) before anything else.

## What does not change at any size

- The model proposes and code decides ([ADR 0002](adr/0002-llm-proposes-code-decides.md)).
- The delivery invariants: one delivery per recipient and day, stable idempotency keys,
  `sent` only after confirmation.
- Raw feedback is durable before any model call.
- Degrading to the library is preferred to unplanned spend.
