# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). Every version is what was
running in production at that point. Versions before 1.0.0 were tagged retroactively,
on the commit that completed each milestone.

## [Unreleased]

## [1.8.0] - 2026-11-11 — Measured and fenced

### Added
- "What's new" for parents without extra messages: news written by hand in
  `src/ops/news.json` ride on the Sunday digest of their week (at most two), with a link
  to the community wall post when `GAME_NEWS_LINK` is set.
- Reliability objectives (`src/ops/slo.py`, `/slo` in the ops bot, and in the Monday
  report): game delivered by 10:15 Moscow time (target 95%), the game made by the model
  rather than taken from the library or reserve (90%), written feedback answered within
  an hour (95%), plus the age of the newest backup. Rolling 28 days, with the error
  budget left for each.

### Changed
- Every product service runs in `game-of-the-day.slice`, capped together at 1.2 GB of
  memory and 1.5 of the 2 CPUs, so a runaway job cannot starve the VPN and sshd that
  share the host. Model runs are capped at 600 MB each, long-running bots at 128 MB;
  a unit that hits its ceiling fails and alerts instead of taking the host down.
  `deploy.sh` installs `.slice` units.

## [1.7.1] - 2026-11-11 — What it cost

### Added
- The morning safety-review summary also shows what the run cost: model calls, tokens
  (input with cache / output) and dollars, for preparation and for the review apart.

## [1.7.0] - 2026-11-11 — Cautions, not rejections

### Changed
- The safety reviewer splits its findings (`PREPARATION_REVIEW_TIERS=1`): a **block**
  (the hazard is the activity itself) rejects the game and the retry repairs the same
  game instead of inventing a new one; a **fix** (one caution about the surroundings or a
  check before playing) is added to the game's safety notes and the game passes. Code
  turns a fix that names a hazardous material (batteries, heat, toxic, magnets) into a
  block, and more than two fixes into a block; under 6 months any toy with attached parts
  is a block. Before, every finding rejected the game: 9 of 18 reviewed candidates were
  rejected and on 2026-11-11 one child's day fell back to the library after six
  attempts. A replay on the same 18 candidates rejects 3, each for a real hazard. While
  the flag is on, every preparation run sends the owner a summary of blocks, fixes and
  library days.

## [1.6.1] - 2026-11-10 — Attention is not difficulty

Hotfix.

### Fixed
- Feedback: trouble keeping attention was read as "too hard" and made the next games
  easier although the child had managed the game (production 2026-11-10). Attention and
  mood are now their own observation kind (`attention`: `short` | `tired`, schema 13);
  "attention is short" puts `short_attention` into the profile, and the selector keeps
  games short and lively instead of easier. "Went well" plus "too hard" for the same
  skill keeps the first and drops the second; the reply no longer contradicts itself.

## [1.6.0] - 2026-11-09 — VK families sign up themselves

### Added
- VK self-service onboarding with owner approval. Whoever writes to the community fills
  the questionnaire in the chat (name, birth date, confirmation — the Telegram bot's
  rules); the owner gets the application in the ops bot with "approve" / "reject"
  buttons, and only an approval creates the child and its VK recipient. Subscribed VK
  families get text commands: «пауза», «продолжить», «мои дети», «добавить ребёнка»,
  «помощь». Asking for Telegram notifies the owner to send an invite. Schema 12
  (`applications`, `outbox`, `inbound_events`); the module is `src/bot/vk_dialog.py`.
- A one-time request to star the project on GitHub, in the Sunday digest: once per
  recipient, only after the child has had games for 30 days and the last 30 days of
  feedback have at least one "liked" and no more "disliked" than "liked"; never in the
  digest that carries the monthly question. Schema 11 (`star_requests`).
- Engineering docs: service level objectives with the measured history
  ([docs/slo.md](docs/slo.md)), a scaling plan with today's ceilings
  ([docs/scaling.md](docs/scaling.md)), four architecture decision records
  ([docs/adr](docs/adr/)) and how the project is built with an AI coding agent
  ([docs/development-process.md](docs/development-process.md)).
- Product metrics (`/metrics` in the ops bot, and the top of the Monday report): games
  delivered, the share with a written answer or a reaction, liked, difficulty (too easy /
  right / too hard), pauses, wishes, reserve days and reviewer rejections, each against the
  previous period, test children excluded.

### Changed
- The Sunday digest no longer lists typical milestones for the age: "what children
  usually do by N months" reads as a test and worries parents whose child is not there
  yet. In its place, "What to try next week": one or two home ideas chosen by code — a
  liked game to repeat, then the skills played least this week, with an idea per skill
  and age band; ties rotate weekly. A test keeps age-norm wording out of the ideas.
- The GitHub star request is off unless `GAME_DIGEST_STAR_REQUEST=1`, until it is
  decided whether to send it.

### Fixed
- `ops/deploy.sh` failed its test step when started from a directory the application
  user cannot enter (e.g. `/root`): it now changes into the application directory first.
- The Worker passed on VK messages from two hard-coded dialogs only, so the feedback of
  any other VK family was dropped. It now passes every private dialog with the sender's
  peer id, and feedback is attached to the newest unanswered game delivered to that
  sender, so two VK families never answer each other's games.
- An unfinished `/add_child` in the Telegram bot captured the family's feedback the next
  day and answered "could not parse the date", so the feedback was lost. For a chat that
  already has a child, a message is now feedback when the questionnaire is over an hour
  old, the message replies to a game, or it is too long to be an answer; the
  questionnaire is dropped with a hint to start again.
- An unknown command in the Telegram bot got a bare repeat of the help, which read as
  the bot being stuck; the reply now names the command first.
- The Telegram bot's command menu had been set by hand to BotFather's setting names
  (`/setdescription`, `/setabouttext`…), and families tapped them. The bot now sets its
  menu at startup from the same list the help text is built from, in every scope.
- A parent's request about her own child's games ("can the next games use more
  objects?") was filed as a wish about the service for the owner and never reached the
  child's profile. The analysis prompt now treats what this child's games should be like
  as a like / dislike; wishes are only about the service itself.
- A thank-you reply that failed to send was dropped for good, and a Telegram feedback
  restored by hand (no message id) always failed it. The reply is now sent without a
  quote when there is no message to quote, and a failed reply is retried on the next
  runs from the stored observations (no second model call) while the feedback is fresh.
- The host-loss postmortem counted 25 days without delivery; it was 24 (2026-10-15 … 11-07).

## [1.5.0] - 2026-11-08 — Not the same game twice

### Added
- Similar games are caught, not only identical titles: every activity carries a mechanic tag
  (what the child mainly does, 24 kinds); the gate and the library choice reject a tag used
  in the child's last 7 games, and the prompt offers only fresh ones. Older games are
  backfilled once (`src/preparation/mechanics.py backfill`). Schema 10.

## [1.4.0] - 2026-11-08 — Families talk back

### Added
- A second safety line: with `PREPARATION_REVIEW_MODEL` set, a small model reviews every
  candidate that passed the gate against an age checklist and reports only serious,
  concrete problems; a finding rejects like a gate error; a reviewer outage never blocks.
- "Too easy" (or "too hard") on the child's latest game makes the next game train the
  same skill one step harder (easier), as a gate rule; the rotation rule still wins.
- A Sunday digest for parents: the week's games and skills, what was liked and went well,
  typical milestones for the age (CDC, marked as a guide), and once a month one question
  about what the service is missing. Deterministic, idempotent per week.
- Wishes about the service: the feedback analysis separates requests about the service
  ("more outdoor games") from observations about the child; families can also send
  `/idea` in the product bot. Each wish reaches the owner at once through the ops bot;
  `/ideas` lists them. They never enter the child's profile.
- Stickers, photos, videos and voice messages as feedback: labelled by the channel
  adapters (the Worker for VK, the product bot for Telegram), never downloaded or stored;
  a reaction alone gets one request per game to write a few words and costs no model call;
  text sent along with it is analysed as usual.
- The child profile from feedback (`src/feedback/insights.py`, `profile.py`): a small model
  extracts observations (outcome, difficulty, what went well or was hard, likes and
  dislikes) into a strict schema; code keeps only those whose quote is verbatim in the
  feedback, aggregates them into a per-child profile (60 days, recency-weighted), and the
  selection prompt follows it. Messages sent close together are merged; at most 3 analyses
  per child and 20 in total a day; no dialogue, one deterministic reply saying what was
  taken into account (only for fresh feedback). Every game now ends with an invitation to
  reply. Evals gained a profile case and a profile-fit score.
- Graceful degradation: a day is never lost to the model. If the primary model fails its
  attempts, a cheaper fallback model (Haiku) gets the same gate; if the subscription quota
  is exhausted or the daily model budget is spent, or the fallback fails too, code picks an
  already validated game from the library (age window, skill rules, nothing repeated). The
  owner is told which path produced the day; every game records its origin; `/llm` counts
  reserve days.
- A warm reserve (`reserve_games`) so a child of any age has a library game even with no
  history: seeded from passed eval candidates (`prepare reserve import`) and topped up
  weekly for each child's next months (`reserve top-up`, Sundays 04:30 MSK, within budget).
- Test children (`children.py add --test`): games only from a manual run
  (`prepare --child ID`), never from the daily schedule, and never the target of feedback
  that arrives without an explicit game, so a family's VK answer cannot land on a test game.
- `game_preparation.py prepare --child ID` limits a manual run to one child.

### Changed
- Schema version 9 (additive).

## [1.3.0] - 2026-11-08 — Measured, then cheaper

### Added
- Eight developmental skills instead of three, opened by age (`src/preparation/skills.py`):
  movement, senses, emotions and social play, speech from birth; fine motor from 3 months;
  thinking from 4; self-care from 12; imagination from 18. The model is offered only the
  skills for the child's age with recent counts; the validator rejects a skill outside the
  age band or used in both of the last two games.
- LLM observability: every model attempt in daily preparation is recorded in
  `model_runs` (latency, turns, tokens, web searches, API-equivalent cost, outcome and
  the validator's rejection reasons), also when the day fails. A weekly report per model
  goes to the owner on Mondays, and `/llm` in the ops bot shows it on demand.

- Selection evals (`evals/preparation`): synthetic cases run through the real selection
  path and scored by the production gate, deterministic lint and an optional model judge,
  with a Markdown report per model.
- An external host watcher: the private backup repository checks every 6 hours that a
  backup arrived within 26 hours, and GitHub emails the owner otherwise.

### Fixed
- The product bot accepted children up to 6 years, but the games and the candidate schema
  cover 0–36 months: such a child would have failed every day. Sign-up is capped at 3 years
  and preparation skips a child who outgrew the range instead of alerting daily.

### Changed
- Daily selection runs on Sonnet instead of Opus, decided by the evals: the same gate
  results, close judged quality, half the quota and nearly twice the speed
  ([comparison](https://github.com/LukshinVI/game-of-the-day/blob/main/evals/preparation/reports/2026-11-08-comparison.md)).
- The selection prompt asks for safety by design (safe materials, positioning in the steps,
  a clear floor, explicit setup steps): judged safety concerns fell from 6/6 to 4/6 cases.
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

[Unreleased]: ../../compare/v1.8.0...HEAD
[1.8.0]: ../../compare/v1.7.1...v1.8.0
[1.7.1]: ../../compare/v1.7.0...v1.7.1
[1.7.0]: ../../compare/v1.6.1...v1.7.0
[1.6.1]: ../../compare/v1.6.0...v1.6.1
[1.6.0]: ../../compare/v1.5.0...v1.6.0
[1.5.0]: ../../compare/v1.4.0...v1.5.0
[1.4.0]: ../../compare/v1.3.0...v1.4.0
[1.3.0]: ../../compare/v1.2.0...v1.3.0
[1.2.0]: ../../compare/v1.1.0...v1.2.0
[1.1.0]: ../../compare/v1.0.0...v1.1.0
[1.0.0]: ../../compare/v0.5.0...v1.0.0
[0.5.0]: ../../compare/v0.4.0...v0.5.0
[0.4.0]: ../../compare/v0.3.0...v0.4.0
[0.3.0]: ../../compare/v0.2.0...v0.3.0
[0.2.0]: ../../compare/v0.1.0...v0.2.0
[0.1.0]: ../../releases/tag/v0.1.0
