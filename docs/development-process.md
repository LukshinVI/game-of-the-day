# How this is built

Game of the Day is built by one maintainer working with an AI coding agent (Claude Code).
This page says who does what, how changes are kept safe, and how to read the commit
history.

## Roles

**The maintainer** owns the product and the system: what problem is worth solving, the
architecture and its boundaries, the trade-offs, what is acceptable risk, and whether a
change goes to production. Examples of decisions made this way:

- the model never owns state, memory or side effects ([ADR 0002](adr/0002-llm-proposes-code-decides.md));
- degrade to a library of validated games instead of paying for overage ([ADR 0003](adr/0003-degrade-instead-of-paid-overage.md));
- families onboard themselves through invites, in a separate product bot, not through the
  operator ([ADR 0004](adr/0004-separate-product-and-ops-bots.md));
- the selection model was moved from Opus to Sonnet only after evals showed the same gate
  results at half the cost ([report](../evals/preparation/reports/2026-11-08-comparison.md)).

**The agent** implements: code, tests, evals, documentation, runbooks, and operational
work on the host within explicit permissions. It proposes options and disagrees with
reasons; it does not decide product direction or ship to production on its own.

## Guardrails on the agent

The same principle as in the product applies to its development: the probabilistic part
proposes, deterministic checks and a human decide.

| guardrail | what it stops |
|---|---|
| CI on every push: unit tests, deterministic evals, ShellCheck | regressions in invariants (delivery state machine, idempotency, feedback storage) |
| Selection evals before a prompt or model change | "looks fine" changes that make games worse or more expensive |
| `ops/deploy.sh`: backup → fast-forward → tests on the new code → additive migration → health | a broken change reaching the database |
| Tool permissions: narrow allow rules; production deploys, remote repointing, credential access and firewall changes need the maintainer's explicit approval per change | the agent acting outside its mandate |
| Read-only diagnostics by default on the production host | accidental changes while investigating |
| Blameless postmortems with tracked actions ([host loss](postmortems/2026-10-host-loss.md)) | the same failure twice |

## Reading the history

- **September 2026** — the original runtime and its migration from Google Apps Script to
  a host with SQLite, through small pull requests against an issue tracker (the tracker
  stays private: it contains production operational details; `#NN` in older docs and
  commits refers to it).
- **2026-11-08** — after the [host loss](postmortems/2026-10-host-loss.md) the service was
  rebuilt and then extended in one long working day: about sixty commits, often minutes
  apart, and releases 1.2–1.5 on the same day. That pace is what an agent produces when the maintainer
  sets direction and reviews outcomes; CI ran on every push, and production deploys went
  through the maintainer. Since then work is committed to `main` as small conventional
  commits, with tagged releases.
- The [ADRs](adr/) were written retroactively on 2026-11-08 for decisions taken earlier.
  They describe the decisions as they were made, not as a justification after the fact.

## Why say this at all

Hiding the agent would make the history misleading. The interesting skill here is not
typing speed but setting boundaries a fast, fallible implementer cannot cross: the same
skill the product applies to LLMs in production.
