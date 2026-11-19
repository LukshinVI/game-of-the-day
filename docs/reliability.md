# Reliability model

What can go wrong, how the owner finds out, and what the system does about it. The
architecture is in [architecture.md](architecture.md); the October host loss that shaped
much of this is in the [postmortem](postmortems/2026-10-host-loss.md). The objectives this
model serves, and how far it is from them, are in [slo.md](slo.md).

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
| Claude does not answer (quota, a 3-minute timeout, an overloaded or failing API) | preparation | Haiku is skipped (same provider) and Claude is not called again for the rest of the run; the independent model on Workers AI (`PREPARATION_INDEPENDENT_MODEL`) cites only known sources and is reviewed on the same provider; owner gets 🟡 |
| subscription quota exhausted, daily budget spent, independent model off, capped (20 calls a day, the free allocation shared with voice) or its reviewer silent | preparation | library game chosen by code; owner gets 🟠 "from the reserve" with the reason |
| no library game fits the child | preparation | 🔴 alert; day can be sent manually after a fix (`/send`) |
| a delivery fails (VK or Telegram error) | delivery, outcome check 10:15 | `failed`, other recipients unaffected; explicit `/retry` from the ops bot |
| interrupted while sending | 10:12 catch-up | reconciled against VK history by the stable message id; Telegram reported, not guessed |
| a service crashes or exits | systemd `OnFailure` on every unit | restart policy; owner alert with the unit name |
| a bot cannot reach Telegram for 10 minutes | the bot itself | exits so systemd restarts it visibly |
| feedback analysis model down | insights run | feedback stays pending, retried next run; the family already has the instant acknowledgement |
| voice transcription fails (no token, too long, download or model error) | the consumer / product bot | the `[голосовое]` label is stored and the family is asked once for a few words |
| local backup missing or stale | backup check 12:00 | owner alert |
| off-site backup broken | weekly restore test, Sundays | owner alert; local backups still present |
| the whole host down or unreachable | the pulse: no beat for 15 minutes at the Worker (`worker/src/watch.js`); GitHub watcher every 6 hours as a second line (no backup for 26 h) | 🔴 to the ops bot from Cloudflare, a reminder every 6 hours, 🟢 when the beats return; rebuild from the repository and the encrypted off-site backup |
| VK feedback stops reaching the host (queue or consumer broken) | the hourly canary through the storage queue, not seen within 30 minutes while the host beats | 🔴 to the ops bot; 🟢 when a canary arrives again |
| a VK message keeps failing on the host | the consumer: retries after 5 s, 1 min, 5 min | kept in the quarantine instead of dropped, 🟡 to the owner, `/replay N` after the fix (`src/feedback/quarantine.py`) |
| an error budget runs out | daily SLO alert at 17:25 MSK over 7 and 28 days (`src/ops/slo_watch.py`) | 🟡/🔴 on a change of level, 🟢 on recovery; silence while unchanged |
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
| host silent for 15 minutes; VK → host chain broken | ops bot, from the Worker (outside the host) |
| a VK message in the quarantine | ops bot: 🟡 with the reason and `/replay N` |
| an error budget changes level | ops bot, daily at 17:25 |
| host silent for 26 hours | GitHub notification (second line) |

## Known gaps

- **The watcher does not see VK itself.** The pulse and the canary prove the host and
  the queue → host path; if VK stops calling the Worker (a changed secret, the callback
  server switched off), nothing alerts. The canary alert says when the last real VK
  message arrived.
- **The watcher is one Worker.** If Cloudflare's cron stops, nothing says so; the GitHub
  watcher (26 h) remains the second line.

- **Queue retention is one day.** A host outage longer than that loses queued VK feedback.
- **Similarity is by category.** A mechanic tag with a 7-game window catches "the same idea
  under a new name", but two different games of one kind are also kept apart, and the tag
  is the model's own label (checked against a fixed list, not against the text).
- **Credentials handled in chat during the recovery have not all been rotated.**
