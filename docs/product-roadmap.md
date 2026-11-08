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

**Parents' week.** A Sunday digest with the week's games, what went well, typical
milestones for the age, and a monthly question about what is missing.

**Quality and reliability of selection.** A second safety line (a model reviewer after the
gate); "too easy" means the same skill, harder. Eight age-banded skills; safety by design in the
prompt; selection evals that chose Sonnet over Opus on evidence; per-attempt metrics and a
weekly report; a fallback chain (fallback model → library and warm reserve) so a model
problem never costs a day.

## Next

- **Similarity, not just exact duplicates.** A mechanic tag set by the model and a
  deterministic no-repeat window.
- **One feedback path for both channels.** The Worker passes the VK peer id (test and
  family dialogs told apart) and reaction labels; Telegram feedback joins the semantic
  agent.

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
