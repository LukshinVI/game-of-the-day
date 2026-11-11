# Service level objectives

What "the service works" means in numbers, how it is measured, and what happens when the
numbers are missed. Failure modes and recovery are in [reliability.md](reliability.md).

## The user journey that matters

A parent gets today's game in the morning, in time to use it during the day. Everything
else — feedback, personalization, digests — is secondary to that.

## SLIs and objectives

| SLI | definition | objective | window |
|---|---|---|---|
| **On-time delivery** | share of active child-days whose game is confirmed `sent` by **10:15 MSK** (the first outcome check) | **≥ 95 %** | rolling 28 days |
| **Same-day delivery** | share of active child-days whose game is confirmed `sent` on that day at all (last outcome check 17:15 MSK) | **≥ 99 %** | rolling 90 days |
| **Feedback freshness** | share of stored feedback that has insights processed within 1 hour (excluding feedback deferred by the daily caps) | ≥ 95 % | rolling 28 days |

A child-day is a (child, date) pair for a non-test child that is not paused. A
game delivered the evening before its date counts as on time. The 95 % on-time objective
allows about one late or missed morning per child in four weeks: honest for one host
without redundancy, and strict enough that a repeating failure burns the budget.

### Invariants — zero error budget

Some properties are not objectives with a budget; one violation is an incident:

- no game is sent twice to the same recipient for the same day;
- no feedback accepted by the host is lost;
- no game that failed the safety gate reaches a family;
- the child's name and birth date never reach a model.

## Error budget policy

- **Budget left:** features ship as usual.
- **Budget exhausted for on-time or same-day delivery:** product work stops until the
  cause is fixed and verified; the next change is a reliability change. A postmortem is
  written for any miss of more than one consecutive day.
- **Invariant violated:** incident, postmortem, and a regression test before anything else
  ships.

## Measured so far

Child 1 (the first family, VK), reconstructed from the messenger history for September and
October and from the delivery table since the rebuild:

| period | child-days | delivered | on time | on-time SLI | notes |
|---|---|---|---|---|---|
| 2026-09-01 … 2026-10-14 | 44 | 42 | 40 | 90.9 % | launch day sent in the afternoon; 09-15 missed; 09-20 missed and 09-21 late from one validation bug, fixed |
| 2026-10-15 … 2026-11-07 | 24 | 0 | 0 | 0 % | host lost ([postmortem](postmortems/2026-10-host-loss.md)) |
| 2026-11-08 | 1 | 1 | 1 | — | first day after the rebuild |
| **total** | **69** | **43** | **41** | **59.4 %** | |

The objective has **not** been met so far. The September misses led to the independent
daily-outcome check; the host loss led to the off-site backup, the restore test and the
watcher outside the host. Whether that is enough is what the next 28-day windows will
show.

## How it is measured

The delivery table is the source of truth; `sent_at` is stored with an offset (imported
history in UTC), so `datetime()` normalizes it. On-time SLI for one child over a period:

```sql
WITH RECURSIVE days(d) AS (
  SELECT :from UNION ALL SELECT date(d, '+1 day') FROM days WHERE d < :to
),
first_sent AS (
  SELECT g.child_id, g.game_date AS d, min(datetime(dl.sent_at)) AS sent_utc
  FROM games g JOIN deliveries dl ON dl.game_id = g.id
  WHERE dl.status = 'sent'
  GROUP BY 1, 2
)
SELECT count(*)                                                    AS child_days,
       sum(fs.sent_utc IS NOT NULL)                                AS delivered,
       sum(fs.sent_utc <= datetime(days.d || ' 07:15:00'))         AS on_time  -- 10:15 MSK
FROM days LEFT JOIN first_sent fs ON fs.d = days.d AND fs.child_id = :child;
```

Detection already exists: the outcome check alerts at 10:15, 11:15, 13:15 and 17:15 MSK
when a delivery of the day is not `sent`, and the watcher outside the host notices a host
that is silent for 26 hours.

## Not built yet

- The SLIs are computed by `src/ops/slo.py` (`/slo` in the ops bot, and the Monday
  report), together with the share of games made by the model rather than the library or
  reserve and the age of the newest backup. Burn rate does not alert yet.
- Pauses are logged as owner-facing events per chat, not as per-child intervals, so a
  paused child-day cannot be excluded by the query yet; it covers children that were
  never paused.
- Time to detect a host that is down is 26 hours — the weakest number here. A heartbeat
  with a shorter interval outside the host would bring it to minutes.
