# Reliability model

What can go wrong, how the owner finds out, and what the system does about it. The
architecture is in [architecture.md](architecture.md); the October host loss that shaped
much of this is in the [postmortem](postmortems/2026-10-host-loss.md).

## Principles

- "It ran" is not "the family got the game": the day's outcome is checked independently.
- Durable state first: raw feedback is stored before any model call; a delivery is
  `sent` only after the messenger confirmed it.
- A model problem must never cost a day: degrade to a cheaper model, then to a library of
  games that already passed the gate.
- Observable failure over silent recovery: every degraded path tells the owner why.
- Healthy polling is cheap: no check calls a model.

## Failure → detection → recovery

| failure | detected by | recovery |
|---|---|---|
| the gate passes something unsafe the words cannot catch | safety reviewer after the gate | rejected with the reason, corrective retry |
| model rejects or fails 3 attempts | preparation | fallback model (Haiku) under the same gate; owner gets 🟡 with the reason |
| subscription quota exhausted, daily budget spent | preparation | library game chosen by code; owner gets 🟠 "from the reserve" with the reason |
| no library game fits the child | preparation | 🔴 alert; day can be sent manually after a fix (`/send`) |
| a delivery fails (VK or Telegram error) | delivery, outcome check 10:15 | `failed`, other recipients unaffected; explicit `/retry` from the ops bot |
| interrupted while sending | 10:12 catch-up | reconciled against VK history by the stable message id; Telegram reported, not guessed |
| a service crashes or exits | systemd `OnFailure` on every unit | restart policy; owner alert with the unit name |
| a bot cannot reach Telegram for 10 minutes | the bot itself | exits so systemd restarts it visibly |
| feedback analysis model down | insights run | feedback stays pending, retried next run |
| local backup missing or stale | backup check 12:00 | owner alert |
| off-site backup broken | weekly restore test, Sundays | owner alert; local backups still present |
| the whole host down or unreachable | GitHub watcher every 6 hours (no backup for 26 h) | GitHub notification; rebuild from the repository and the encrypted off-site backup |
| disk, memory, load, VPN services, ports | host health every 5 minutes (infra repository) | owner alert on change, reminder every 6 hours, recovery notice |
| host lease or the Claude token expiring | daily reminder 30/14/7/3/1/0 days before | renew; reminders continue daily when overdue |
| a deploy breaks the tests | `ops/deploy.sh` | code rolled back before the migration, database untouched |

## Delivery

- only the intended activity for the current date is eligible;
- an already-sent delivery is never sent again; side effects have stable identities;
- `sent` is recorded only after confirmed success; `failed` stays failed until an
  explicit retry;
- each recipient has its own delivery and state;
- an interrupted `sending` is resolved from the channel: found → `sent`, not found →
  `failed`, younger than 10 minutes → left alone as possibly in flight;
- no activity for a date is a normal state, not corruption.

## Feedback

Raw persistence precedes inference, so a model outage, exhausted quota or malformed output
never asks a parent to write again. Duplicates resolve to the existing record before any
target is chosen. Analysis caps (per child and in total, per day) leave the rest pending
for the next day instead of spending past the budget.

## Backups and restore

Nightly SQLite online backup with integrity check and retention; an age-encrypted copy in a
private repository (only ciphertext leaves the host); a weekly automatic restore into a
sandbox with `integrity_check`; the decryption key kept offline by the owner as well. A new
host is restored with `ops/backup/offsite-git.sh restore`.

## Monitoring signals

| signal | where it goes |
|---|---|
| game prepared (normal / fallback model / library) | ops bot: 🟢 / 🟡 / 🟠 |
| preparation impossible, delivery not done, unit failed, backup stale | ops bot: 🔴 |
| families: join, new child, pause, limits, block, wishes | ops bot, instantly |
| model quality, latency, tokens, cost, reserve days | ops bot, Mondays and `/llm` |
| host and VPN health, lease and credential expiry | owner's channel bot (infrastructure) |
| host silent for 26 hours | GitHub notification |

## Known gaps

- **The host watcher notifies by e-mail.** It runs outside the host, as it must, but posts
  to Telegram only once a GitHub token for repository secrets is provided.
- **VK ingress has no health signal.** If the Worker or Cloudflare Queues fail, VK feedback
  is lost without an alert; there is no cheap reliable way to tell "nobody wrote" from
  "nothing arrived".
- **Queue retention is one day.** A host outage longer than that loses queued VK feedback.
- **Queues have no dead-letter destination.** A message that keeps failing is eventually
  dropped instead of quarantined.
- **Duplicate detection is exact.** A game with a new title but the same idea passes; a
  similarity rule (a mechanic tag with a no-repeat window) is planned.
- **Test and family VK dialogs share a channel.** A family's answer cannot land on a test
  game, but the reverse cannot be told apart until the Worker passes the peer id.
- **Credentials handled in chat during the recovery have not all been rotated.**
