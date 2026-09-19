# AI design

Game of the Day uses an LLM for semantic work, not as the application's state machine.

## Boundary

Deterministic code owns authentication, routing, workflow state, dates, persistence, retries, idempotency, provenance, schema validation, policy decisions and side effects.

The LLM is used where natural-language ambiguity makes fixed rules insufficient: classifying free-form feedback and resolving semantic references inside an application-controlled candidate space.

## Feedback pipeline

```text
raw feedback
→ deterministic segmentation
→ provenance assignment
→ semantic classification
→ deterministic schema/business validation
→ deterministic policy
→ guarded target linking
→ persistence
```

The important property is that the model classifies evidence supplied by the application rather than inventing evidence that the application later treats as fact.

## Guardrails

- Model output is structured but never trusted solely because it is syntactically valid.
- Evidence spans originate from deterministic segmentation.
- Metadata may provide context but is not itself user evidence.
- An unresolved target remains unresolved rather than being guessed.
- Extracted observations do not directly become durable preferences.
- Model failure leaves raw feedback available for retry or later processing.
- Persistent memory lives in application storage, not in model context.

## Why this design

The goal is to keep probabilistic reasoning where it adds value while preserving deterministic control over safety-sensitive and state-changing behavior. This also makes model replacement, quota degradation and evaluation easier because the semantic component has a narrower contract.
