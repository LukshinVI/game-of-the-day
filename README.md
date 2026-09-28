# Game of the Day

A small production-oriented service that delivers a safe, age-appropriate developmental activity each day and learns from caregiver feedback over time.

The project is also an engineering sandbox for reliable AI-assisted workflows: deterministic guardrails own state and side effects, while LLMs are limited to semantic tasks that benefit from them.

> **Project status:** active production migration. The system is moving from a Google-based runtime to a VPS/SQLite application runtime through a controlled, reversible cutover.

## What the product does

The core loop is intentionally simple:

```text
child context + safety constraints + activity history
                    ↓
              daily activity
                    ↓
              delivery channel
                    ↓
                 feedback
                    ↓
       validated product observations
                    ↓
          future personalization
```

Activities are expected to be age-appropriate, safe, short enough for a daily routine, non-repetitive unless repetition is explicitly useful, and backed by a verifiable source.

This is **not** a medical or diagnostic product.

## Architecture

The target production architecture is:

```mermaid
flowchart TD
    P[Game preparation / product workflow] --> DB[(SQLite)]
    DB --> D[Delivery service + scheduler]
    D --> CH[Messaging channel]
    CH --> W[Cloudflare Worker]
    W --> V{Validation and routing}
    V --> Q[Cloudflare Queue]
    Q --> RAW[Feedback storage: persist raw feedback]
    RAW --> DB
    RAW --> F[Feedback Agent]
    F --> AI[Workers AI]
    AI --> F
    F --> RES[Feedback storage: persist analysis]
    RES --> DB
```

The migration deliberately separates delivery, feedback persistence, and final source-of-truth cutover so each boundary can be verified and rolled back independently.

See [Architecture](docs/architecture.md) for the detailed runtime model and migration invariants.

## Reliability model

A central design rule is:

> **AI failure must not become product failure.**

Raw feedback is persisted before AI processing. Long-running semantic analysis crosses a durable queue boundary. Duplicate external events are handled idempotently, retries are bounded, and workflow state remains application-owned.

Delivery uses explicit states such as `pending`, `sending`, `sent`, and `failed`. Repeated scheduler executions must be safe, and ambiguous failure states must be recoverable without uncontrolled duplicate delivery.

See [Reliability model](docs/reliability.md) for the invariants and the known gaps between them and the running system.

Operational runbooks live next to their code: [database backup](ops/backup/README.md), [manual delivery control](ops/bot/README.md), and [daily outcome check](ops/day-outcome/README.md).

## Deterministic / AI boundary

The application intentionally avoids delegating workflow ownership to an LLM.

**Deterministic code owns:**

- security and routing validation;
- persistence and record linking;
- workflow state transitions;
- idempotency, retry, and recovery;
- dates and delivery windows;
- provenance and schema validation;
- policy decisions and external side effects.

**AI is used for:**

- semantic classification of free-form feedback;
- interpretation of ambiguous reactions;
- constrained semantic target resolution;
- future recommendation steps where deterministic ranking is insufficient.

LLM output is structured and validated before it can affect durable state.

See [AI design](docs/ai-design.md) for the guardrails.

## Feedback Agent

The semantic feedback path follows a provenance-preserving contract:

```text
feedback text
→ deterministic segmentation and provenance
→ AI classification of existing segments
→ deterministic validation
→ deterministic policy
→ guarded target linking
```

The model does not invent evidence spans. Metadata may provide context but is not treated as user evidence. Unresolved targets remain unresolved instead of being guessed.

The current production model is `@cf/google/gemma-4-26b-a4b-it` through Cloudflare Workers AI.

## Current migration

The active migration moves durable product state and delivery from the legacy Google runtime to SQLite on the application host.

```text
production-safe delivery state machine
        +
reproducible scheduler
        +
idempotent feedback storage
        +
Cloudflare → application persistence
        +
observability and recovery checks
        ↓
controlled production cutover
        ↓
stability period
        ↓
legacy runtime retirement
```

Status: daily preparation and delivery have run from the host since 2026-09-18, together with the feedback contour. The old runtime stays enabled as a rollback path until measurable exit criteria are met (an independent daily-outcome check, a tested rollback, a recovery drill); whether it still delivers is unverified. Migration and architecture refactoring are intentionally kept separate.

## Engineering principles

- Keep state, guardrails, and side effects deterministic.
- Persist user input before optional AI processing.
- Design idempotency, retries, recovery, and rollback explicitly.
- Keep product state independent from delivery transport.
- Prefer simple application flows over premature orchestration frameworks.
- Add agents or model-routing layers only when they solve a demonstrated problem.
- Keep secrets, production identifiers, user data, and infrastructure-specific details out of the repository.
- Treat merge, deploy, and production verification as separate readiness stages.
- Prefer observable failure modes over silent recovery.

## Product roadmap

Near-term product development focuses on completing the feedback UX, clarification workflow, and personalization. Later layers add selectable development focus, a channel-independent child activity history, goals/progress, and additional delivery channels.

See [Product Roadmap](docs/product-roadmap.md) and [GitHub Issues](../../issues) for the current execution backlog.

## Repository safety

Public documentation intentionally uses role-based names and placeholders instead of production hostnames, addresses, account identifiers, recipient identities, internal filesystem paths, or credentials.

Runtime secrets must never be committed. See [Security Policy](SECURITY.md).

## Cost policy

The project currently has a strict no-unplanned-spend rule. Free-tier infrastructure and AI quotas may be used, but exhaustion of a free quota must degrade safely rather than automatically enabling paid overage.
