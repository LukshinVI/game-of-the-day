# Postmortem: production host lost, 25 days without delivery

**Status:** resolved 2026-11-08 · **Severity:** full outage of the daily product loop · blameless

## Summary

The single application host that ran preparation, delivery, feedback storage and
the SQLite database became unavailable after the delivery of 2026-10-14. No game
was delivered from 2026-10-15 to 2026-11-07. The service was rebuilt from this
repository on a new host overnight on 2026-11-07/08. The game history was
recovered from the messenger itself; no caregiver feedback was lost.

## Impact

- 25 days without a daily activity for the child.
- The SQLite database (games, deliveries, feedback) was lost with the host.
- Feedback: none lost. The edge (Cloudflare Worker + queues) stayed up, but the
  storage queue keeps messages for 1 day only; the impact was nil because no
  feedback was sent during the outage, not because the design protected it.

## Timeline (Moscow time)

| when | what |
|---|---|
| 2026-10-14 10:00 | last successful delivery from the old host |
| 2026-10-15 … | host unavailable; no delivery, no alert reached the owner |
| 2026-11-08 ~03:15 | outage confirmed; decision to rebuild on the new host |
| 2026-11-08 ~03:45 | repository made deployable from scratch: missing systemd units added, secrets split per unit, model backend switched |
| 2026-11-08 04:01 | history of 42 games recovered from the messenger API and imported |
| 2026-11-08 04:36 | first preparation on the new host; test delivery to a test dialog |
| 2026-11-08 04:43 | first production delivery after recovery |

## What went wrong

1. **Backups lived on the same host as the data.** The backup job was verified and
   alerting, but its off-site step was optional and not configured, so it protected
   against corruption, not against losing the machine.
2. **Every alert ran on the failed host.** The daily outcome check and the backup
   freshness check are systemd timers on the application host. When the host
   disappeared, the watchers disappeared with it, and the outage was silent.
3. **The deploy recipe was not fully in the repository.** The preparation,
   delivery and feedback-consumer units existed only on the old host.
4. **A hand-maintained constant drifted.** The prompt and validation hardcoded the
   child's age as 12 months; by October the activities were selected for the wrong age.

## What went right

- Durable state was in one SQLite file and the delivery state machine is
  idempotent, so rebuilding meant: schema, history, units, secrets.
- The messenger kept a complete record of what was sent, which made history
  recovery exact (dates, titles, sources) instead of reconstructed.
- Deterministic validation and the test suite made it safe to change the model
  backend and the message layout during the recovery.

## Actions

| action | status |
|---|---|
| All units tracked in `ops/`; [deployment guide](../deployment.md) covers a fresh host end to end | done |
| Child age computed from a birth date on the delivery date | done |
| History import from the messenger, delivery rows marked `sent` so nothing is re-sent | done |
| Off-site backup: nightly age-encrypted copy in a private repository, weekly automatic restore check ([runbook](../../ops/backup/README.md#off-site-copy-encrypted-private-git-repository)) | done (2026-11-08) |
| Watcher outside the host: a scheduled GitHub Actions check in the private backup repository fails (and emails the owner) when no nightly backup arrived for 26 hours | done (2026-11-08) |
| Storage-queue retention from 1 day to the maximum, so a host outage does not lose feedback | open |
| Rotate credentials that were handled in chat during the recovery | open |
