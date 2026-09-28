# Reliability model

This document describes the reliability guarantees the project tries to preserve across delivery, feedback ingestion and AI processing.

## Failure domains

The system treats messaging delivery, callback ingress, persistence, queue processing and model inference as separate failure domains. A failure in one stage should not silently erase durable state from another.

## Delivery

Delivery uses explicit workflow state rather than assuming that a scheduler invocation equals success.

Expected invariants:

- only the intended activity for the current delivery period is eligible;
- an already-sent delivery is not sent again;
- external side effects use stable identity where the channel supports it;
- `sent` is recorded only after confirmed external success;
- interrupted `sending` work can be detected and recovered;
- retryable failures remain retryable;
- absence of an eligible activity is a normal domain state, not database corruption.

## Feedback ingestion

Feedback is accepted as an external event with a stable channel/event identity.

```text
callback
→ validation
→ durable queue
→ save raw event
→ semantic processing
→ save validated analysis
```

Raw persistence precedes model inference. This makes model outage, quota exhaustion, malformed model output and transient processing failures recoverable without asking the user to reproduce the original feedback.

Duplicate events are resolved before selecting a new target. This prevents a delayed retry from becoming associated with a newer activity.

## AI processing

AI is not a transaction boundary and is not trusted with durable state. Model output is parsed and validated by application code. Invalid output fails processing rather than silently becoming product state.

The application owns evidence provenance, allowed target space, policy and final persistence.

## Retry policy

Retries must be bounded and observable. A retry must either be idempotent or operate on an explicit recoverable state. Blindly repeating an external side effect is not considered a valid retry strategy.

## Migration and rollback

Runtime migrations are staged. New persistence and transport paths are verified independently before the old path is disabled. Legacy code/data may remain available during a stability window, but two delivery schedulers must never be active for the same logical delivery.

A production cutover is complete only after runtime verification, not merely after code merge or deployment.

## Known gaps

This document states the invariants the project works towards. These are the places where the running system does not yet meet them; each is tracked as work rather than hidden.

- **Backups are local only.** SQLite is the single durable store on a single host. A nightly online backup with integrity verification, retention, a freshness check and a failure alert runs, but the copy sits on the same disk: there is no off-host copy and no periodic restore test yet (#37).
- **No external heartbeat.** An independent check now verifies today's game exists and was delivered (`ops/day-outcome`, several times after the delivery window) — this is what caught the two consecutive missed days in September too late before. What is still missing is a check from outside the host: nothing notices the host itself being down, since every check described here runs on it (#45).
- **Two game producers exist until the cutover.** The legacy scheduled generator and the host preparation both create games, and their identifiers collide; only the host path feeds delivery (#46).
- **Queue failures can end in silent discard.** Consumers use bounded retries, but no queue has a dead-letter destination yet, so a message that keeps failing is eventually dropped instead of being quarantined. Permanent errors (for example an event with no resolvable target) are not distinguished from transient ones.
- **Queue redelivery is not closed end to end.** Redelivery of an already-analysed event no longer re-enqueues enrichment, but an ambiguous publish outcome (accepted by the queue, error seen by the caller) can still deliver enrichment twice. A stable idempotency key for enrichment messages and downstream deduplication are not defined yet.
- **Runtime privilege is broader than necessary.** Some services run under a general-purpose or privileged account rather than a dedicated unprivileged service user with a sandboxed unit.
- **Target attribution is a heuristic.** Feedback without an explicit reference is attached to the newest sent activity that has no feedback yet. A second message about the same activity is therefore attributed to an older one. This is pinned by a characterisation test so that changing it is a conscious decision.

