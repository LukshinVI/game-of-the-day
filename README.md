# Game of the Day

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/LukshinVI/game-of-the-day)](../../releases)

A small production service that sends a caregiver one safe, age-appropriate developmental activity for a young child every day, collects free-form feedback in a messenger, and turns it into structured observations for future personalization.

It runs in production for a real family. It is also an engineering sandbox for one question: **how do you put LLMs into a production workflow without letting them own state, side effects, or safety?**

> Not a medical or diagnostic product.

## What is interesting here

| Problem | How it is solved |
|---|---|
| LLM output is probabilistic, but delivery must be exactly-once | The LLM only *proposes*. Deterministic code owns the delivery state machine (`pending → sending → sent / failed`), dates, idempotency, and retries. |
| An unsafe activity must never reach a child | Generated candidates are validated against a JSON Schema and deterministic safety rules (for example, sharp-object hazards). A rejected candidate gets a corrective retry with the rejection reasons, never a silent pass. |
| Model outage must not lose user input | Raw feedback is persisted **before** any model call and crosses a durable queue boundary. A model failure leaves feedback retryable, not lost. |
| The model must not invent evidence | Feedback is segmented deterministically first. The model classifies the application's segments; it cannot create its own evidence spans. Unresolved references stay unresolved instead of being guessed. |
| Prompt and model changes regress silently | Eval suites: deterministic segmentation evals on every push, LLM evals against real Workers AI models on demand, with side-by-side model comparison. |
| "It ran" is not "the user got the game" | An independent daily-outcome check verifies that today's activity actually exists and was delivered, separately from the per-process alerts. |
| Onboarding without the operator | Families join through a one-time invite link in a Telegram bot and enter the child's birth date themselves; the age is computed, confirmed and stored, the next run picks the child up. No model is involved. |
| Small moments matter | A deterministic greeting on the child's month-birthdays in the first year, then on birthdays and half-birthdays, put before that day's game; end-of-month and leap-day births handled. |
| "Is the model getting worse or more expensive?" | Every model attempt is recorded (latency, tokens, cost, outcome, the validator's rejection reasons); a weekly report and `/llm` in the ops bot compare models on real runs. |
| Ops for a one-person service | systemd units with hardening, verified SQLite backups with retention and alerting, an owner-only Telegram bot for manual send/retry. |

## Architecture

```mermaid
flowchart TD
    P[Daily preparation<br/>LLM + schema + safety validation] --> DB[(SQLite, WAL)]
    DB --> D[Delivery service<br/>state machine + scheduler]
    D --> CH[Messenger]
    CH -->|caregiver feedback| W[Cloudflare Worker: ingress]
    W --> QA[[Queue: ingress]]
    QA --> W2[Worker: forward to host]
    W2 --> QB[[Queue: storage]]
    QB --> STORE[Feedback storage] --> DB
    STORE -. raw persisted, then analysed .-> QC[[Queue: enrichment]]
    QC --> F[Feedback agent]
    F <--> AI[Workers AI]
    F --> QB
```

- **Host runtime** (Python, systemd, SQLite): game preparation, delivery, feedback storage, backups, the daily outcome check, and the ops bot.
- **Edge** (Cloudflare Workers + Queues): callback validation, durable hand-off to the host, and the semantic feedback agent on Workers AI.
- Feedback goes through three separate queues (ingress, storage, enrichment). The storage queue carries both the raw event and, later, its analysis.

Details: [Deployment](docs/deployment.md) · [Architecture](docs/architecture.md) · [Reliability model](docs/reliability.md) · [AI design](docs/ai-design.md) · [Product roadmap](docs/product-roadmap.md)

## Where AI is used, and where it is not

**Deterministic code owns:** authentication and routing, workflow state, dates and delivery windows, persistence and record linking, idempotency, retries and recovery, schema and business validation, policy decisions, and every external side effect.

**LLMs are used for:**
- **Daily preparation:** proposing an activity candidate as structured output. It is then validated, and retried with the reasons if rejected ([`src/preparation`](src/preparation)).
- **Feedback agent:** classifying free-form caregiver feedback and resolving which activity it refers to, inside an application-controlled candidate space ([`worker/src/agents`](worker/src/agents)). Production model: `@cf/google/gemma-4-26b-a4b-it` on Workers AI.

```text
feedback text
→ deterministic segmentation and provenance
→ AI classification of existing segments
→ deterministic validation
→ deterministic policy
→ guarded target linking
```

## Testing and evals

| Suite | What it covers | Runs |
|---|---|---|
| `tests/` (Python, 208 tests) | delivery state machine, feedback storage idempotency, preparation validation and message layout, child age, model backends, backup script, outcome check, ops bot, multi-child, product bot onboarding | every push (CI) |
| `worker/evals/runSegmentationEvals.js` | deterministic segmentation and relation detection | every push (CI) |
| `worker/evals/feedbackCases.js` (16 cases) | end-to-end semantic feedback analysis against a real model; compares `baseline` / `gemma` / `glm` | manual workflow ([LLM evals](.github/workflows/llm-evals.yml)) |
| `evals/preparation/` (6 synthetic cases) | the real selection path (prompt → model → validator → retries) scored by the gate, deterministic lint and an optional model judge; compares models (e.g. Opus vs Sonnet) | manual, on the host (spends quota) |
| ShellCheck | backup scripts | every push (CI) |

LLM evals are manual on purpose. The project has a strict no-unplanned-spend policy: running out of free quota must degrade safely, never switch on paid overage.

## Running locally

```bash
# Python unit tests (install the sqlite3 CLI to run the backup tests too)
python3 -m unittest discover -s tests -v

# Worker: deterministic evals
cd worker
npm ci
npm test

# Worker: LLM evals against Workers AI (needs a Cloudflare account)
npm run evals:llm:dev            # wrangler dev --remote with the evals config
curl "http://127.0.0.1:8787/__evals/feedback?model=baseline"
```

Configuration lives in environment variables; see [`.env.example`](.env.example). Worker secrets are set with `wrangler secret put`.

## Repository layout

```text
src/
  preparation/   daily activity generation: prompt, output schema, safety validation, retry
  delivery/      delivery state machine and messenger sender
  feedback/      queue consumer and idempotent feedback storage
  ops/           daily outcome check, owner-only Telegram bot, alert notifier
  bot/           product Telegram bot: invites, onboarding, feedback
  db/            schema and one-off import/sync from the legacy sheet
worker/
  src/           Cloudflare Worker: callback ingress and the feedback agent
  evals/         deterministic and LLM eval suites
ops/             systemd units, timers and runbooks (preparation, delivery, feedback, backup, bot, outcome check)
docs/            architecture, reliability, AI design, deployment, roadmap, postmortems
legacy/          the original Google Apps Script sender (rollback path)
```

Runbooks: [database backup](ops/backup/README.md) · [manual delivery control](ops/bot/README.md) · [daily outcome check](ops/day-outcome/README.md) · [product bot](ops/product-bot/README.md)

## In production

- In daily use since 2026-09-01: 43 activities delivered, no repeated titles, a balanced skill mix (coordination 16, speech 14, fine motor 13).
- In October the only host was lost together with its database. The service was rebuilt from this repository in one night, with the history recovered from the messenger and nothing re-sent. What went wrong and what changed: [postmortem](docs/postmortems/2026-10-host-loss.md).
- Activity selection runs on Claude Code in headless mode with structured output; Codex CLI is a drop-in fallback behind the same validation.

## Status

- Daily preparation and delivery run on the host/SQLite runtime. The original Google Sheets + Apps Script runtime is kept only as a rollback path.
- The semantic feedback pipeline is in production. Next: feedback UX, a clarification workflow, and personalization from validated observations ([roadmap](docs/product-roadmap.md)).
- Known gaps are documented, not hidden ([reliability model](docs/reliability.md)). An interrupted `sending` delivery used to be one of them; it is now reconciled against the messenger history by its stable message id.

Changes are released as tagged versions with notes from the [changelog](CHANGELOG.md). Development is done through small PRs against an issue tracker. Issue numbers like `#27` in the docs and commit history refer to that tracker, which stays private because it contains production operational details.

## Engineering principles

- Keep state, guardrails and side effects deterministic.
- Persist user input before optional AI processing.
- Design idempotency, retries, recovery and rollback explicitly.
- Prefer observable failure over silent recovery.
- Add agents, frameworks or model routing only when they solve a demonstrated problem.
- Treat merge, deploy and production verification as separate readiness stages.

## License

[MIT](LICENSE). Real production identifiers, credentials, and family data are not part of this repository; see [SECURITY.md](SECURITY.md).
