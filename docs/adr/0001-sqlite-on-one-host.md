# 0001. SQLite on one host is the single source of truth

**Status:** accepted · **Decided:** September 2026 (migration off Google Sheets)

## Context

The first version kept state in a Google Sheet driven by Apps Script. Delivery had no
explicit states, no idempotency, and the sheet could be edited by hand. The service needs
exactly-once delivery per recipient and per day, durable feedback, and cheap operation for
a handful of families with one maintainer.

## Options

| option | for | against |
|---|---|---|
| keep Google Sheets | already works, zero infra | no transactions, no constraints, state edited by hand |
| managed Postgres | HA, point-in-time recovery | cost, a network dependency for every step, more to operate than the load needs |
| Cloudflare D1 | next to the Worker | the host jobs (preparation, delivery, bots) would reach state over the network |
| **SQLite (WAL) on the application host** | transactions and constraints, zero ops, trivially backed up, tests run on real SQLite | one writer, one host, availability equals the host's |

## Decision

SQLite in WAL mode on the application host. All durable state — children, games,
deliveries, feedback, observations, model runs — lives in one file. Migrations are
additive so the previous code runs on a migrated database and a code rollback is safe.

## Consequences

- Delivery invariants are enforced by the database (unique idempotency keys, explicit
  states), not by convention.
- The host is a single point of failure. This was paid for in October 2026: the host and
  its database were lost ([postmortem](../postmortems/2026-10-host-loss.md)). The decision
  stood; its mitigations were completed instead: an age-encrypted off-site copy every
  night, a weekly automatic restore test, a watcher outside the host, and a deploy guide
  that rebuilds a host from the repository.
- Recovery point objective: up to 24 hours of state (nightly off-site copy). Accepted for
  this product: a lost day of feedback is bad, a corrupted delivery state is worse.

## Revisit when

Several hosts must write at once, the recovery point of 24 hours stops being acceptable, or
the write load approaches what one SQLite writer handles (far beyond the current scale;
see [scaling](../scaling.md)).
