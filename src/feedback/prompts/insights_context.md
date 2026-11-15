CONTEXT. The input also has:
- `this_game_id`: the game this feedback was attached to (usually the newest one);
- `recent_games`: the child's games of the last weeks (id, date, title, skill), newest
  first;
- `previous_observations`: what was recently noted about this child (id, game, kind,
  value, evidence), which the parent may be correcting now.

Every observation has `game`:
- for outcome, difficulty, skill and reason: the id from `recent_games` of the game the
  words are about. Parents often write about an earlier game: "та игра с мячиками была
  классная" is about the game whose title or idea matches (balls), not about this game.
  Use `this_game_id` when the feedback is about this game or does not say which;
  when it clearly speaks about an earlier game that you cannot find in `recent_games`,
  do not add game-specific observations for it;
- for the other kinds: null.

Two more kinds:
- setting: where or when the family plays, a condition for the next games and never a
  dislike: value bedtime (they play before sleep, in the evening, need calm games) |
  outdoors (on a walk, outside) | on_the_go (on the road, in a queue, with no time to
  prepare); skill null. "играем перед сном, нужно менее активно" → setting bedtime, and
  NO dislike of active games: the child may love them, the time of day is what matters;
- recent: how the recent games went in general, not one particular game: value worse
  ("потом всё стало не очень", "последние игры не заходят") | better ("в последнее время
  игры нравятся больше"); skill null. Never turn such a general remark into an outcome
  of this game.

`retract`: ids from `previous_observations` that the parent says are wrong ("нет, не
так", "я не это имел в виду", "он не не любит, просто..."). Only when the parent clearly
corrects that very point; otherwise an empty list. Add what the parent meant instead as
a new observation.
