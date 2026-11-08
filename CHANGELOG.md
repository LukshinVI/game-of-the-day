# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). Every version is what was
running in production at that point. Versions before 1.0.0 were tagged retroactively,
on the commit that completed each milestone.

## [Unreleased]

### Added
- LLM observability: every model attempt in daily preparation is recorded in
  `model_runs` (latency, turns, tokens, web searches, API-equivalent cost, outcome and
  the validator's rejection reasons), also when the day fails. A weekly report per model
  goes to the owner on Mondays, and `/llm` in the ops bot shows it on demand.

- An external host watcher: the private backup repository checks every 6 hours that a
  backup arrived within 26 hours, and GitHub emails the owner otherwise.

### Fixed
- The product bot accepted children up to 6 years, but the games and the candidate schema
  cover 0–36 months: such a child would have failed every day. Sign-up is capped at 3 years
  and preparation skips a child who outgrew the range instead of alerting daily.

### Changed
- Schema version 4 (additive).

## [1.2.0] - 2026-11-08 — Backups that leave the host

### Added
- Encrypted off-site backups: every night the newest verified backup is encrypted with
  age, the round trip is checked, and the ciphertext is pushed to a private repository.
  A weekly job restores the newest copy into a sandbox and runs `integrity_check`; both
  alert on failure. Closes the main action of the host-loss postmortem.
- A test that fails if a database or backup artifact is ever tracked in this repository.

### Security
- The public product bot runs as its own user (`gotd-bot`, `ProtectProc=invisible`): a
  compromise could no longer read the other services' tokens from `/proc`. It shares only
  the database, through a group.
- The off-site push runs as root, so the repository key is out of the bots' reach.

### Changed
- The ops bot start message says plainly that nothing was sent.

## [1.1.0] - 2026-11-08 — Families

### Added
- Several children: each one gets its own daily game for its own age and history;
  each recipient (VK or Telegram) gets its own delivery with its own state, retries and
  recovery. The outcome check requires every delivery (#20, #21).
- A separate product Telegram bot (`src/bot`) for families. Access is invite-only:
  one-time links that expire, stored as hashes. Self-service onboarding asks for the
  child's name and birth date and confirms the computed age. Members can `/add_child`,
  `/pause`, `/resume`; plain text and replies are stored as feedback on that chat's game.
- A welcome message and `/faq`, limited to what the code enforces.
- Abuse limits: children per chat, active children in total (each costs a model run a
  day), feedback per day; strangers get one notice a day; `/block` for the owner.
- User monitoring in the owner's ops bot: instant join/subscribe/pause/limit events
  (at-least-once) and a `/users` report per family.
- Birthday greetings: monthly in the first year, then on birthdays and half-birthdays,
  put before that day's game.

### Changed
- Schema version 3 (additive migration in `src/db/children.py`).
- Preparation skips children without active recipients and never falls back to the
  single-child environment once children exist.

## [1.0.0] - 2026-11-08 — Rebuilt in the open

The only host was lost in October together with its database. This release is the
service rebuilt from the repository on a new host, with the history recovered from the
messenger and nothing re-sent ([postmortem](https://github.com/LukshinVI/game-of-the-day/blob/main/docs/postmortems/2026-10-host-loss.md)).

### Added
- CI: unit tests, deterministic segmentation evals and ShellCheck on every push;
  LLM evals against Workers AI on demand.
- Claude Code in headless mode with structured output as the selection backend;
  Codex CLI kept as a fallback behind the same validation.
- The child's age is computed from the birth date on the delivery date.
- The parent message is rendered by code from structured fields (emoji layout).
- An interrupted `sending` delivery is reconciled against the VK history by its
  stable `random_id` instead of being guessed (#28); a 10:12 catch-up run.
- systemd units for preparation, delivery and the feedback consumer; a deployment
  guide; issue/PR templates, CODEOWNERS, Dependabot; MIT license.

### Fixed
- The outcome check could not open the WAL database from a read-only directory and
  raised false "not delivered" alerts.
- The safety note is kept short and specific (at most 450 characters).

### Security
- The VK community id is deployment configuration, not source.

## [0.5.0] - 2026-09-28 — Operable by one person

### Added
- An owner-only Telegram bot for manual delivery with one-time confirmation codes; the
  bot never holds the messaging credentials (#47).
- An independent daily-outcome check: "it ran" is not "the parent got the game" (#45).
- Verified SQLite backups with retention, a freshness check and alerts (#40).

### Fixed
- Candidate validation no longer loses the day to false positives (#44).

## [0.4.0] - 2026-09-19 — Feedback on the host

### Changed
- Feedback storage and enrichment moved from Google Sheets to the host: raw feedback
  is persisted before any model call and crosses a durable queue boundary (#36).
- The repository was prepared for public visibility (#35).

## [0.3.0] - 2026-09-16 — Host runtime

### Added
- SQLite storage and a delivery CLI with an explicit state machine
  (`pending → sending → sent / failed`) and idempotency keys.
- Automated daily preparation with a retry policy, shadow runs and ops alerts.

## [0.2.0] - 2026-09-13 — Feedback agent v2

### Changed
- Feedback semantics redesigned around deterministic segmentation and provenance; the
  model classifies existing segments and cannot invent evidence.
- Production model switched to Gemma on Workers AI; processing moved to Cloudflare Queues.

## [0.1.0] - 2026-09-03 — Production baseline

### Added
- Daily delivery to VK from Google Sheets + Apps Script, with a retry window.
- The first feedback agent and its eval harness.

[1.2.0]: ../../compare/v1.1.0...v1.2.0
[1.1.0]: ../../compare/v1.0.0...v1.1.0
[1.0.0]: ../../compare/v0.5.0...v1.0.0
[0.5.0]: ../../compare/v0.4.0...v0.5.0
[0.4.0]: ../../compare/v0.3.0...v0.4.0
[0.3.0]: ../../compare/v0.2.0...v0.3.0
[0.2.0]: ../../compare/v0.1.0...v0.2.0
[0.1.0]: ../../releases/tag/v0.1.0
