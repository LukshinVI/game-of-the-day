"""The child profile: deterministic aggregation of validated observations.

The model never writes the profile. It only extracts observations from feedback (each
with a verbatim quote, src/feedback/insights.py); this module turns the last 60 days of
them into a small summary that preparation puts into the prompt:

- per skill, the direction for the next game ("harder" / "easier"), from difficulty
  signals weighted by recency (half-life 14 days);
- what went well and what was hard, per skill;
- likes and dislikes;
- games the child liked or disliked.

An empty profile is {} and changes nothing.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone

WINDOW_DAYS = 60
HALF_LIFE_DAYS = 14
DIRECTION_THRESHOLD = 0.8
DIFFICULTY_SCORE = {"too_easy": 1.0, "right": 0.0, "too_hard": -1.0}


def _age_days(created_at, now):
    try:
        then = datetime.fromisoformat(created_at.replace(" ", "T").replace("Z", "+00:00"))
        then = then if then.tzinfo else then.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return 0.0
    return max(0.0, (now - then).total_seconds() / 86400)


def build(observations, now=None):
    """observations: dicts with kind, value, skill, evidence, created_at, game_title."""
    now = now or datetime.now(timezone.utc)
    difficulty = defaultdict(float)
    went_well, hard = defaultdict(list), defaultdict(list)
    likes, dislikes = defaultdict(float), defaultdict(float)
    liked_games, disliked_games = [], []

    for obs in sorted(observations, key=lambda o: o["created_at"], reverse=True):
        age = _age_days(obs["created_at"], now)
        if age > WINDOW_DAYS:
            continue
        weight = 0.5 ** (age / HALF_LIFE_DAYS)
        kind, value, skill = obs["kind"], obs["value"], obs.get("skill")
        if kind == "difficulty" and skill and value in DIFFICULTY_SCORE:
            difficulty[skill] += weight * DIFFICULTY_SCORE[value]
        elif kind == "skill" and skill:
            target = went_well if value == "went_well" else hard if value == "hard" else None
            if target is not None and obs["evidence"] not in target[skill] and len(target[skill]) < 3:
                target[skill].append(obs["evidence"])
        elif kind == "like":
            likes[value] += weight
        elif kind == "dislike":
            dislikes[value] += weight
        elif kind == "outcome" and obs.get("game_title"):
            bucket = liked_games if value == "liked" else disliked_games if value == "disliked" else None
            if bucket is not None and obs["game_title"] not in bucket and len(bucket) < 10:
                bucket.append(obs["game_title"])

    profile = {}
    directions = {}
    for skill, score in difficulty.items():
        if score >= DIRECTION_THRESHOLD:
            directions[skill] = "harder"
        elif score <= -DIRECTION_THRESHOLD:
            directions[skill] = "easier"
    if directions:
        profile["next_difficulty"] = directions
    if went_well:
        profile["went_well"] = dict(went_well)
    if hard:
        profile["hard_for_now"] = dict(hard)
    top = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:5]]  # noqa: E731
    if likes:
        profile["likes"] = top(likes)
    if dislikes:
        profile["dislikes"] = top(dislikes)
    if liked_games:
        profile["liked_games"] = liked_games
    if disliked_games:
        profile["disliked_games"] = disliked_games
    return profile


def rebuild(con, child_id, now=None):
    rows = [dict(r) for r in con.execute(
        # Recency counts from when the parent wrote, not from when it was analysed.
        "SELECT o.kind, o.value, o.skill, o.evidence, COALESCE(f.received_at, o.created_at) AS created_at, "
        "g.title AS game_title FROM observations o LEFT JOIN games g ON g.id = o.game_id "
        "LEFT JOIN feedback f ON f.id = o.feedback_id WHERE o.child_id = ?",
        (child_id,),
    )]
    profile = build(rows, now)
    con.execute(
        "INSERT INTO child_profiles(child_id, profile_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(child_id) DO UPDATE SET profile_json = excluded.profile_json, updated_at = excluded.updated_at",
        (child_id, json.dumps(profile, ensure_ascii=False)),
    )
    return profile


def load(con, child_id):
    if child_id is None:
        return {}
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'child_profiles'").fetchone() is None:
        return {}
    row = con.execute("SELECT profile_json FROM child_profiles WHERE child_id = ?", (child_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def required_next(con, child_id, history):
    """The skill the next game must exercise, from difficulty feedback on the latest game.

    "Too easy" on yesterday's game means the next game trains the same skill one step
    harder ("too hard": easier), unless that skill was used in both of the last two games
    (the rotation rule wins). Returns {"skill", "direction", "after"} or None.
    """
    if child_id is None or not history:
        return None
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'observations'").fetchone() is None:
        return None
    latest = con.execute(
        "SELECT id, title, skill FROM games WHERE child_id = ? ORDER BY game_date DESC, id DESC LIMIT 1",
        (child_id,),
    ).fetchone()
    if latest is None:
        return None
    row = con.execute(
        "SELECT value, COALESCE(skill, ?) FROM observations WHERE game_id = ? AND kind = 'difficulty' "
        "AND value IN ('too_easy', 'too_hard') ORDER BY id DESC LIMIT 1",
        (latest[2], latest[0]),
    ).fetchone()
    if row is None:
        return None
    value, skill = row
    last = [g.get("skill") for g in history[-2:]]
    if len(last) == 2 and all(s == skill for s in last):
        return None
    return {"skill": skill, "direction": "harder" if value == "too_easy" else "easier", "after": latest[1]}
