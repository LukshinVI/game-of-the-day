"""Seasonal and holiday themes: a seasoning for some games, never every game.

The application decides when a theme is even offered: on about one day in THEME_EVERY,
and only while the child had fewer than MAX_PER_WEEK themed games in the last 7 days.
The model then uses it only if it fits the chosen skill naturally (it may decline by
leaving `theme` empty). For a child under YOUNG_MONTHS the theme lives only in words,
songs and colours: no new objects are brought in for it.

Each theme carries words that may not appear in the game (decorations that break,
choke or burn); the validator enforces them like the general dangerous-term gate.

Deterministic code only.
"""

import hashlib
import json
import re
from datetime import date, timedelta

THEME_EVERY = 3
MAX_PER_WEEK = 2
YOUNG_MONTHS = 6

# key: (first MM-DD, last MM-DD, Russian label, hint for the model, banned word starts as
# regular expressions, matched at a word start: "лед" alone would hit "следите")
THEMES = {
    "new_year": ("12-15", "01-10", "Новый год",
                 "New Year at home: the tree seen from a distance, Ded Moroz, snowflakes, wrapping a "
                 "present, a winter song",
                 ("мишур", "дождик", "ёлочн", "елочн", "гирлянд", "свеч", "бенгальск", "конфетти",
                  "хлопушк", "блестк", "блёстк", "серпантин", "хвоин", "мандарин")),
    "winter": ("01-11", "02-28", "зима",
               "winter: snow seen through the window, warm clothes, mittens, animals in winter",
               ("л[её]д(?:ян\\w*)?\\b", "льд", "сосульк")),
    "spring": ("03-01", "05-31", "весна",
               "spring: birds singing, sun, puddles seen on a walk, flowers, waking animals",
               ("семечк", "семен", "земл")),
    "summer": ("06-01", "08-31", "лето",
               "summer: sun, grass, water play at home, a butterfly or a bee in a picture book",
               ("ягод", "песок", "песк")),
    "autumn": ("09-01", "12-14", "осень",
               "autumn: falling leaves, rain, a basket of vegetables in a picture book",
               ("желуд", "каштан", "шишк")),
}


def _in_range(day, first, last):
    key = day.strftime("%m-%d")
    return first <= key <= last if first <= last else (key >= first or key <= last)


def current(day):
    """The theme key for this date (New Year wins over the season it overlaps)."""
    for key in ("new_year", *[k for k in THEMES if k != "new_year"]):
        first, last = THEMES[key][:2]
        if _in_range(day, first, last):
            return key
    return None


def offered_today(day, child_id):
    """About one day in THEME_EVERY, spread differently per child."""
    digest = hashlib.sha256(f"theme:{day.isoformat()}:{child_id}".encode()).hexdigest()
    return int(digest, 16) % THEME_EVERY == 0


def themed_last_week(con, child_id, day):
    start = (day - timedelta(days=7)).isoformat()
    count = 0
    for (raw,) in con.execute(
            "SELECT candidate_json FROM games WHERE child_id IS ? AND game_date >= ? AND game_date < ?",
            (child_id, start, day.isoformat())):
        try:
            count += bool(json.loads(raw or "{}").get("theme"))
        except (ValueError, AttributeError):
            continue
    return count


def context(con, child_id, day, age_months):
    """The `theme` block for the prompt, or None when no theme is offered today."""
    key = current(day)
    if key is None or not offered_today(day, child_id) or themed_last_week(con, child_id, day) >= MAX_PER_WEEK:
        return None
    _, _, label, hint, banned = THEMES[key]
    rules = ("Optional seasoning, not a requirement. Use it only if it fits the chosen skill "
             "naturally; otherwise leave `theme` null and make an ordinary game. The skill, its "
             "difficulty and every safety rule come first. Never use decorations that break, "
             "choke or burn.")
    if age_months < YOUNG_MONTHS:
        rules += (" The child is very young: the theme may only appear in words, a song or a "
                  "colour; bring in no new objects for it.")
    return {"key": key, "label": label, "hint": hint, "rules": rules,
            "never_use": [re.sub(r"[^\wё]", "", b.split("(")[0].replace("[её]", "е")) for b in banned]}


def violations(candidate):
    """Banned stems of the theme the candidate claims, in its title/mechanics/materials/steps."""
    key = candidate.get("theme")
    if not key:
        return []
    if key not in THEMES:
        return [f"unknown theme: {key}"]
    blob = " ".join([str(candidate.get("title", "")), str(candidate.get("mechanics", "")),
                     " ".join(candidate.get("materials") or []), " ".join(candidate.get("steps") or [])]).casefold()
    return [f"theme gate triggered: {stem}" for stem in THEMES[key][4] if re.search(r"\b" + stem, blob)]
