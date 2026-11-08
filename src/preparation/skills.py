"""Developmental skills and the ages they apply to.

Deterministic on purpose: the application decides which skills are on the table for a
child of a given age; the model picks one of them and the validator enforces it. The
bands follow common milestone guidance (CDC "Learn the Signs. Act Early.", AAP), kept
coarse: a skill becomes available from the month its typical play starts.

Keys are stable identifiers stored in the database; labels are what parents read.
"""

from collections import Counter

# key: (from_months, Russian label, what it means — given to the model)
SKILLS = {
    "coordination": (0, "движение и координация",
                     "gross motor: head control, rolling, crawling, walking, balance, throwing, jumping"),
    "sensory": (0, "сенсорика",
                "exploring textures, sounds, light, temperature and movement through the senses"),
    "social_emotional": (0, "эмоции и общение",
                         "eye contact, turn-taking, naming and showing feelings, playing together"),
    "speech": (0, "речь",
               "listening, babbling, sounds, first words, phrases, understanding instructions"),
    "fine_motor": (3, "мелкая моторика",
                   "grasping, transferring, pincer grip, stacking, pouring, scribbling"),
    "cognitive": (4, "мышление",
                  "cause and effect, object permanence, sorting, matching, simple problem solving"),
    "self_care": (12, "самостоятельность",
                  "doing it myself: drinking from a cup, washing hands, dressing, tidying up toys"),
    "imagination": (18, "воображение",
                    "pretend play, role play, telling a story with toys, creative making"),
}

LABELS = {key: label for key, (_, label, _) in SKILLS.items()}
ALL = set(SKILLS)
STREAK = 2  # the candidate must not repeat the skill of both of the last STREAK games


def for_age(age_months):
    return [key for key, (start, _, _) in SKILLS.items() if age_months >= start]


def prompt_context(age_months, history, window=14):
    """What the model is told: the skills available now and how recent games used them."""
    allowed = for_age(age_months)
    recent = Counter(g.get("skill") for g in history[-window:] if g.get("skill"))
    return {
        "allowed_skills": {key: SKILLS[key][2] for key in allowed},
        "recent_skill_counts": {key: recent.get(key, 0) for key in allowed},
        "last_skills": [g.get("skill") for g in history[-STREAK:]],
    }


def violations(skill, age_months, history):
    if skill not in ALL:
        return ["invalid primary skill"]
    errors = []
    if skill not in for_age(age_months):
        errors.append(f"skill {skill} is not offered at {age_months} months: choose one of {', '.join(for_age(age_months))}")
    last = [g.get("skill") for g in history[-STREAK:]]
    if len(last) == STREAK and all(s == skill for s in last):
        errors.append(f"skill {skill} was used in each of the last {STREAK} games: choose another skill")
    return errors
