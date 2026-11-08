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

from src.preparation import skills

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
    rows = [dict(r) for r in con.execute(
        f"""
        SELECT id, title, age_months, skill, mechanics, source_name, source_url, message_text
        FROM games
        WHERE message_text IS NOT NULL AND TRIM(message_text) != ''
          AND age_months IS NOT NULL AND skill IS NOT NULL
          AND source_url IS NOT NULL
          {origin_filter}
        """
    )]
    seen_titles = {normalize(g.get("title")) for g in history}
    seen_mechanics = {normalize(g.get("mechanics")) for g in history if g.get("mechanics")}
    recent = Counter(g.get("skill") for g in history[-14:])

    for window in WINDOWS:
        pool = [
            r for r in rows
            if age_months - window <= r["age_months"] <= age_months
            and not skills.violations(r["skill"], age_months, history)
            and normalize(r["title"]) not in seen_titles
            and normalize(r["mechanics"]) not in seen_mechanics
        ]
        if pool:
            def order(r):
                tie = hashlib.sha256(f"{target_date}:{child_id}:{r['id']}".encode()).hexdigest()
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
                "library_source_game_id": best["id"],
            }
    return None


def describe(origin, reason):
    labels = {"model": "подбор моделью", "fallback_model": "резервная модель", "library": "библиотека (резерв)"}
    return labels.get(origin, origin) + (f" — {reason}" if reason else "")


def candidate_json(candidate):
    return json.dumps({k: v for k, v in candidate.items() if k != "message_text"}, ensure_ascii=False)
