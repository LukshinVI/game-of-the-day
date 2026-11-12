# Privacy and data retention

Game of the Day serves families with children from birth to 3 years. The rule is to
keep as little as the service needs, for as short as it needs, and to let a family
leave with its data. This page is the engineering side of that promise; families see a
short version in `/faq` (`src/bot/faq.txt`) and the VK help.

## What is stored

| data | where | why |
|---|---|---|
| child's name or nickname, birth date | `children`, `applications`, `onboarding` | the age is computed daily; the name tells siblings' messages apart |
| Telegram chat id, VK peer id, Telegram display name | `recipients`, `members` | where to send the game |
| feedback text | `feedback.raw_text` | what went well, what was hard |
| quotes from feedback | `observations.evidence`, `wishes.evidence` | why a conclusion was drawn |
| conclusions | `observations` (kind/value), `child_profiles` | the next games adapt |
| games and deliveries | `games`, `deliveries` | no repeats, delivery state |
| why a family left (optional) | `exit_surveys` | product decisions; not linked to a child or a chat |
| «полезно ли?» score 1–5 and comment (optional, monthly) | `pulses` | is the service useful |

**Not stored:** photos, videos, voice messages, stickers. Only a label (`[фото]`) is
kept: these are pictures of children, the fact that they arrived is enough
(`src/feedback/reactions.py`).

## Who sees it

- The owner (operator of the service), through the ops bot and the database.
- The model provider (Anthropic) receives **feedback texts** for analysis and the age in
  months with the history of games for selection. **Names and birth dates are never sent
  to a model** by the code (`src/preparation/game_preparation.py` builds the prompt from the age).
  A feedback text goes as the parent wrote it, so it may name the child.
- Families never see each other: every reply is bound to the chat it came from
  (`tests/test_product_bot.py: a reply cannot reach a game sent to another chat`).

## Terms

| what | term | then | code |
|---|---|---|---|
| feedback texts and quotes | 180 days | blanked; conclusions stay | `src/ops/retention.py` |
| a family with no active recipient and no activity | 365 days | deleted entirely | `retention.inactive_families` |
| an archived family (asked to be deleted) | 14 days | purged; a hash of the address and the counts stay | `src/bot/erasure.py` |
| unfinished questionnaire | 7 days | deleted | `retention.collect` |
| rejected application, queued and handled messages, unused link codes, VK visitors who never subscribed | 30 days | deleted | `retention.collect` |
| owner-facing event texts | 90 days | text and chat removed; the kind stays for the counts | `retention.collect` |
| exit survey free text, «полезно ли?» comment | 180 days | removed; the reason or the score stays | `retention.collect` |
| local backups | 14 days (at least 7 copies) | rotated | `ops/backup/game-backup.sh` |
| off-site backups | 14 days, one commit without history | rotated | `ops/backup/offsite-git.sh` |

Retention runs nightly at 03:10 MSK (`game-retention.timer`), before the 03:30 backup,
in one transaction. The owner gets a one-line summary with counts only. `--dry-run`
reports the counts without changing anything.

## Deleting on request

`/forget` in Telegram, «удалить наши данные» in VK:

1. The bot names the children it covers and asks for «удалить».
2. **Archive.** The family's rows leave the live tables for `erasures.archive_json`.
   The family is everything reachable from the chat: its children, every chat that
   receives them (a family in VK and Telegram is one family) and their children.
   Games, digests and greetings stop at once; reports, metrics and the dashboard read
   the live tables only, so the family disappears from them too. Owner events about
   the family lose their text; model spend stays measured without the child link.
3. **Exit survey**, one optional question with numbered answers. The answer is stored
   without a child or a chat. It never blocks the deletion.
4. **The way back.** For 14 days «вернуть» from any chat of the family restores every
   row (ids taken meanwhile are remapped) and the games resume.
5. **Purge.** «удалить сейчас» at once, or the nightly run after 14 days. The erasure
   row keeps `sha256(channel:address)`, the number of children and the dates.

The family is told both dates: until when it can come back, and when the last backup
holding its data rotates out (purge + 14 days, since the archive itself is in the
backups of those days). The owner is told only that a family left or came back, and
the survey answer.

**What cannot be deleted by us:** the chat history kept by VK or Telegram themselves;
objects GitHub may keep for a while after a force-push (ciphertext only, the key never
leaves the host and the owner's offline copy).

## Tests

`tests/test_erasure.py`: the whole family leaves and no other family is touched; after
a purge no table holds the name, the chat ids or the feedback; restore is exact even
when ids were taken; the survey is optional and anonymous; every retention term and the
dry run. `tests/test_offsite_backup.py`: the off-site branch has a single commit and old
copies are unreachable.
