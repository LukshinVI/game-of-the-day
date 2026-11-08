# 0002. The model proposes, deterministic code decides

**Status:** accepted · **Decided:** September 2026, reaffirmed with every model use since

## Context

The product's core content — the day's activity for a young child, the meaning of a
parent's reply — is produced by language models. Models are probabilistic, can be
unavailable, can hallucinate evidence, and can propose something unsafe. The output goes
to a real family every day.

## Options

1. **Agent owns the workflow** — the model decides what to send, when, and remembers the
   child. Least code; failures are silent and unreproducible; safety depends on a prompt.
2. **Model behind a schema, code checks the output** — structured output validated before
   use. Better, but state and memory still drift into prompts.
3. **Model proposes, code owns everything else** — state, dates, idempotency, retries,
   the safety gate, the child's profile and every side effect are deterministic; each
   model has a small contract and a path that works without it.

## Decision

Option 3. Models are used for exactly three things (proposing the activity and reviewing
its safety, extracting observations from feedback, classifying VK feedback); each sits
behind a schema, a deterministic check and a fallback. The model is never the
application's memory, workflow engine or source of truth. Details:
[AI design](../ai-design.md).

## Consequences

- A model outage degrades a day, it does not lose one ([ADR 0003](0003-degrade-instead-of-paid-overage.md)).
- Personalization works without model memory: observations must quote the feedback
  verbatim, code builds the profile.
- The selection model could be changed on evidence (Opus → Sonnet) because the gate, not
  the model, defines "acceptable".
- More code than an agent loop, and the gate can only catch what is written down — which
  is why a second, model-based safety review runs after it.

## Revisit when

A task appears that genuinely needs multi-step autonomy (tool use across several systems
with no deterministic path). Not before: agents are not added for their own sake.
