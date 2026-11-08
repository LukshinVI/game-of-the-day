# 0004. Separate product and ops bots, invite-only onboarding

**Status:** accepted · **Decided:** November 2026 (second family joining)

## Context

The owner already had a Telegram ops bot with alerts and manual controls (`/send`,
`/retry`). A second family had to join, and the owner did not want to register families by
hand. Families need a place to onboard, receive games and reply; the owner needs controls
families must never reach.

## Options

1. **One bot, role checks inside** — fewer moving parts; a bug in a role check exposes
   manual delivery controls and alerts to families.
2. **Operator registers families** — no public surface at all; does not scale past a few
   families and puts the owner in every onboarding.
3. **A separate product bot with invite-only self-service onboarding.**

## Decision

Option 3. The product bot faces families: a one-time invite link, a questionnaire (name,
birth date, confirmation of the computed age), `/children`, `/pause`, `/resume`, feedback.
It runs as its own unprivileged user (`gotd-bot`, `ProtectProc=invisible`) and shares only
the database with the rest of the system. The ops bot stays the owner's and never holds
messaging credentials. Invites are stored as hashes; strangers get one notice a day and
nothing is stored. No model is involved in onboarding.

## Consequences

- A compromise of the public bot cannot read other services' secrets or trigger
  deliveries.
- Families join without the owner; limits cap children per chat, active children in total
  and feedback per day.
- Two bots to run and monitor instead of one.

## Revisit when

Families need a web interface, or sign-up opens beyond invitations.
