"""Graceful degradation for daily preparation: a day is never lost to the model.

Chain (game_preparation.obtain_candidate):
  primary model (PREPARATION_CLAUDE_MODEL, validated, corrective retries)
  -> fallback model (PREPARATION_FALLBACK_MODEL, same gate), skipped when the
     subscription quota is exhausted (the same quota) or the daily budget is spent
  -> the library: a game that was already validated and sent, chosen by code.

Library choice is deterministic: the source game was made for an age 0-3 months below
the child's (widened to 6 if nothing fits), its skill is offered at the child's age and
does not repeat both of the last two skills, and neither its title nor its mechanics are
in the child's history. Among those, the skill seen least recently wins, then the closest
age, then a stable per-day order. Library copies are never used as sources themselves.
"""

import hashlib
import json
import os
import re
from collections import Counter

from src.preparation import mechanics, skills

# Claude Code reports an exhausted subscription with these words; the same quota serves
# the fallback model, so it is skipped and the library answers at once.
QUOTA_PATTERN = re.compile(r"usage limit|limit reached|out of (extra )?usage|quota", re.IGNORECASE)
WINDOWS = (3, 6)


def fallback_model():
    return os.environ.get("PREPARATION_FALLBACK_MODEL", "haiku").strip()


def daily_budget_usd():
    value = os.environ.get("PREPARATION_DAILY_BUDGET_USD", "3").strip()
    return float(value) if value else None


def is_quota_error(exc):
    return bool(QUOTA_PATTERN.search(str(exc)))


def spent_today(con, today):
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_runs'").fetchone() is None:
        return 0.0
    return con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM model_runs WHERE dry_run = 0 AND target_date = ?",
        (str(today),),
    ).fetchone()[0]


def normalize(text):
    return " ".join(str(text or "").lower().split())


def library_candidate(con, history, age_months, target_date, child_id=None):
    """A validated past game for this child, as a candidate dict, or None."""
    columns = {r[1] for r in con.execute("PRAGMA table_info(games)")}
    origin_filter = "AND COALESCE(origin, 'model') != 'library'" if "origin" in columns else ""
    tag = "mechanic_tag" if "mechanic_tag" in columns else "NULL"
    rows = [{**dict(r), "kind": "game"} for r in con.execute(
        f"""
        SELECT id, title, age_months, skill, mechanics, source_name, source_url, message_text,
               {tag} AS mechanic_tag
        FROM games
        WHERE message_text IS NOT NULL AND TRIM(message_text) != ''
          AND age_months IS NOT NULL AND skill IS NOT NULL
          AND source_url IS NOT NULL
          {origin_filter}
        """.replace("{tag}", tag)
    )]
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'reserve_games'").fetchone():
        rows += [{**dict(r), "kind": "reserve"} for r in con.execute(
            "SELECT id, title, age_months, skill, mechanics, source_name, source_url, message_text, "
            + ("mechanic_tag" if "mechanic_tag" in {r[1] for r in con.execute("PRAGMA table_info(reserve_games)")} else "NULL")
            + " AS mechanic_tag FROM reserve_games"
        )]
    seen_titles = {normalize(g.get("title")) for g in history}
    seen_mechanics = {normalize(g.get("mechanics")) for g in history if g.get("mechanics")}
    recent = Counter(g.get("skill") for g in history[-14:])

    for window in WINDOWS:
        pool = [
            r for r in rows
            if age_months - window <= r["age_months"] <= age_months
            and not skills.violations(r["skill"], age_months, history)
            and not mechanics.violations(r.get("mechanic_tag"), history)
            and normalize(r["title"]) not in seen_titles
            and normalize(r["mechanics"]) not in seen_mechanics
        ]
        if pool:
            def order(r):
                tie = hashlib.sha256(f"{target_date}:{child_id}:{r['kind']}{r['id']}".encode()).hexdigest()
                return (recent.get(r["skill"], 0), age_months - r["age_months"], tie)

            best = min(pool, key=order)
            return {
                "title": best["title"],
                "age_months": best["age_months"],
                "skill": best["skill"],
                "mechanics": best["mechanics"] or best["title"],
                "source_name": best["source_name"] or "",
                "source_url": best["source_url"],
                "message_text": best["message_text"],
                "library_source": f"{best['kind']} #{best['id']}",
                "mechanic_tag": best.get("mechanic_tag"),
            }
    return None


def describe(origin, reason):
    labels = {"model": "подбор моделью", "fallback_model": "резервная модель", "library": "библиотека (резерв)",
              "swap": "замена по просьбе семьи"}
    return labels.get(origin, origin) + (f" — {reason}" if reason else "")


def candidate_json(candidate):
    return json.dumps({k: v for k, v in candidate.items() if k != "message_text"}, ensure_ascii=False)


def eligible_count(con, history, age_months, target_date):
    """How many distinct library games would fit this child (for the reserve top-up)."""
    count, seen = 0, list(history)
    while count < 10:
        pick = library_candidate(con, seen, age_months, target_date)
        if pick is None:
            break
        count += 1
        seen = seen + [{"title": pick["title"], "mechanics": pick["mechanics"], "skill": None}]
    return count


def add_reserve(con, candidate, made_by):
    """Store a validated candidate in the warm reserve; False if it is already there."""
    cur = con.execute(
        "INSERT OR IGNORE INTO reserve_games(age_months, skill, title, mechanics, source_name, source_url, "
        "message_text, candidate_json, made_by, mechanic_tag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate["age_months"], candidate["skill"], candidate["title"].strip(), candidate["mechanics"],
         candidate.get("source_name"), candidate["source_url"], candidate["message_text"],
         candidate_json(candidate), made_by, candidate.get("mechanic_tag")),
    )
    return cur.rowcount == 1
