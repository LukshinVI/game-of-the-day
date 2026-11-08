# 0003. Degrade to a library instead of paying for overage

**Status:** accepted · **Decided:** November 2026

## Context

Selection runs on a Claude subscription through headless Claude Code; the edge runs on the
Cloudflare free tier. Quotas run out, models fail, and a family must still get a game at
10:00. The project has a strict no-unplanned-spend rule.

## Options

| option | for | against |
|---|---|---|
| retry the same model until it works | simple | an outage or exhausted quota costs the day |
| pay-as-you-go API as overflow | always available | unbounded cost, the exact thing the rule forbids |
| skip the day and alert | honest | the product fails at its one job |
| **fallback chain ending in code** | a day is never lost, cost is capped | a library game is less personal; the library must be kept full |

## Decision

Primary model (Sonnet) → cheaper fallback model (Haiku) under the same gate → a validated
game from the library, chosen by code (age window, skill rules, nothing the child already
had). A daily preparation budget (`PREPARATION_DAILY_BUDGET_USD`, 3 USD) sends the rest of
the day to the library. A weekly job keeps a warm reserve for each child's next two
months. Every game records its origin, and the owner is told which path made the day.

## Consequences

- Cost has a hard ceiling per day; running out of quota is a 🟠 alert, not an outage.
- The library is itself made of games that passed the gate, so the fallback is not less
  safe, only less personal.
- The budget caps scale: at about 0.05–0.07 USD per game it covers roughly 40–60 children
  a day before the library takes over ([scaling](../scaling.md)).

## Revisit when

Paid usage becomes a planned, budgeted line — then the budget, not the chain, changes.
