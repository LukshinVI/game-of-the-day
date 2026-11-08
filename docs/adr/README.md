# Architecture decision records

Short records of decisions that shape the system: the context, the options that were
considered, what was chosen and what it costs. Written retroactively on 2026-11-08 for
decisions taken between September and November 2026 (see
[development process](../development-process.md)); new decisions get a record when they
are made.

| # | decision | status |
|---|---|---|
| [0001](0001-sqlite-on-one-host.md) | SQLite on one host is the single source of truth | accepted |
| [0002](0002-llm-proposes-code-decides.md) | The model proposes, deterministic code decides | accepted |
| [0003](0003-degrade-instead-of-paid-overage.md) | Degrade to a library instead of paying for overage | accepted |
| [0004](0004-separate-product-and-ops-bots.md) | Separate product and ops bots, invite-only onboarding | accepted |

Format: context → options → decision → consequences → when to revisit.
