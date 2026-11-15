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

Theme: easier to answer, nicer to receive, progress you can see. Each feature ships to
production behind a flag and is tested there; all are switched on together on release day.

| feature | state |
|---|---|
| One feedback analyzer for both channels ([ADR 0005](adr/0005-one-feedback-analyzer.md)) | live (internal, no flag) |
| Game cards: a skill picture above the game text (`GAME_CARDS`) | in production, off |
| Seasonal and greeting pictures (birthday, New Year) | illustrations being made |
| One-tap reactions under the game | proposed |
| Voice feedback, transcribed | planned (below) |
| A focus for the month | proposed |

## Next

- **Voice feedback, transcribed.** Today a voice message is only a label (`[голосовое]`,
  nothing stored). Plan: the host downloads the audio (Telegram file, VK audio link),
  transcribes it with Whisper on Workers AI, stores only the text as `[голосовое] …`, and
  never keeps the audio; on failure the label stays and the family is asked for a few
  words. The privacy terms (`docs/privacy.md`) and the FAQ change first.

## Backlog (agreed, not scheduled)

- **Referral.** A family invites another; needs a limit and the owner's approval like any
  application, so it never becomes open sign-up.
- **Image cards.** Illustrations made in the ChatGPT style the VK community already uses,
  composed by code into a card for the game; a separate release.

## Later

**V4 — Activity and development history.** A caregiver-facing history of activities and
observations per child, exportable.

**V5 — Goals and progress.** The caregiver picks a focus ("more speech this month");
selection weights it; progress is explained from observations, never inferred as a
diagnosis.

## Non-goals for this stage

- no microservices, no new database, no orchestration framework without a demonstrated need;
- no additional agents for the sake of agent count;
- no automatic paid AI or infrastructure overage;
- no medical or diagnostic interpretation of observations;
- no open sign-up: families join by invitation.
