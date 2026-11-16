# Product Roadmap — Game of the Day

Product direction and what is done. The system itself is in
[architecture.md](architecture.md); shipped changes are in [CHANGELOG.md](../CHANGELOG.md).

## Product vision

```text
useful daily activity
→ listen to the caregiver
→ personalize future activities
→ keep a history of activities and observations
→ support goals and explainable progress
```

A caregiver gets one short, safe, age-appropriate activity a day, answers in a couple of
words, and the next activities follow what actually happened. Not a medical or
diagnostic tool.

## Done

**V1 — Daily activity delivery.** Age-appropriate activity every day at 10:00 MSK on a
host/SQLite runtime with an explicit delivery state machine; the day's outcome checked
independently; the original Google Sheets runtime retired to `legacy/`.

**Families and channels.** Several children per family and several families; VK and a
Telegram product bot; invite-only self-service onboarding (name, birth date, confirmed
age); a welcome and FAQ; pause and resume; abuse limits; the child's name on games in a
shared chat; birthday and month-birthday greetings.

**V2 — Feedback loop.** VK feedback through a durable queue and a semantic feedback agent;
Telegram feedback attached to the exact game; stickers and photos ask for a few words;
wishes about the service reach the owner (`/idea`, `/ideas`).

**V3 — Personalization, first step.** Observations extracted from feedback with verbatim
quotes; a code-built profile per child (difficulty direction per skill, what went well,
likes, dislikes); the selection prompt follows it; the parent sees what was taken into
account.

**Parents' week.** A Sunday digest with the week's games, what went well, one or two
home ideas for next week (no age norms), and a monthly question about what is missing.

**Quality and reliability of selection.** A second safety line (a model reviewer after the
gate); "too easy" means the same skill, harder. Eight age-banded skills; safety by design in the
prompt; selection evals that chose Sonnet over Opus on evidence; per-attempt metrics and a
weekly report; a fallback chain (fallback model → library and warm reserve) so a model
problem never costs a day.

**One feedback path for both channels.** The Worker passes the VK sender (test and family
dialogs told apart) and reaction labels; VK and Telegram feedback land in one table and one
analyzer (`src/feedback/insights.py`) reads both. The Worker's separate semantic agent on
Workers AI was retired: its analysis was stored but nothing read it.

## Release 2.0.0

Theme: easier to answer, nicer to receive, progress you can see. Each feature shipped to
production behind a flag and was tested there; all were switched on together on
2026-11-15.

| feature | state |
|---|---|
| One feedback analyzer for both channels ([ADR 0005](adr/0005-one-feedback-analyzer.md)) | live (internal, no flag) |
| Game cards: a skill picture above the game text (`GAME_CARDS`) | live |
| A new full-year birthday text and picture (`GAME_HOLIDAYS`); public holidays go to wall posts | live |
| Birthday greeting picture | live (`assets/cards`) |
| One-tap reactions under the game, with the reason on «Не зашло» (`GAME_BUTTONS`) | live |
| Voice feedback, transcribed (`GAME_VOICE`) | live |
| Theme of the month (`GAME_MONTH_THEME`) | live |
| «Полезно ли?» score in the digest (`GAME_DIGEST_PULSE`) | live |

After 2.0 (in [Unreleased](../CHANGELOG.md)):

| feature | state |
|---|---|
| Instant acknowledgement, considered reply in 3–5 minutes; VK «Ответить» attaches to that game | live |
| Feedback in context: earlier games, where the family plays, recent trend, corrections (`GAME_FEEDBACK_CONTEXT`) | live |
| Album of the month (`GAME_ALBUM`) | built, off until the owner decides |

A referral is not needed now.

## Next

- ~~**More families on the same host.**~~ Done 16.11: preparation at 02:00 MSK, caps 50
  active children and 60 feedback analyses a day, one owner summary per run
  ([scaling](scaling.md), step 1).

## Backlog (agreed, not scheduled)

- **Referral.** A family invites another; needs a limit and the owner's approval like any
  application, so it never becomes open sign-up.

## Later

**V4 — Activity and development history.** A caregiver-facing history of activities and
observations per child, exportable.

**V5 — Goals and progress.** The first part is live as the theme of the month (something
to play more of, never a goal); next, progress explained from observations, never
inferred as a diagnosis.

## Non-goals for this stage

- no microservices, no new database, no orchestration framework without a demonstrated need;
- no additional agents for the sake of agent count;
- no automatic paid AI or infrastructure overage;
- no medical or diagnostic interpretation of observations;
- no open sign-up: families join by invitation.
