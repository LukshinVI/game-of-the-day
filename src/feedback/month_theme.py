"""Theme of the month: what a family would like to play more of, never a goal.

The parent may pick a skill for the month ("больше игр про речь") or a mix, skip it, or
change it at any time. Nothing is promised and nothing is measured:

- selection: on about every other day the game trains the theme's skill (when it is
  open at the child's age and was not the previous game's skill); a difficulty answer
  on the latest game still comes first;
- the weekly digest names only warm moments the parent wrote or tapped about the theme,
  and says nothing about the theme when there were none: no "achieved / not achieved",
  no percentages, no comparisons, no age norms;
- in the first days of a month without a theme, the game carries one line inviting the
  family to pick one ("тема" in either messenger, /theme in Telegram).

A theme is stored per child and month (month_themes); a chat with several children sets
it for all of them. Off unless GAME_MONTH_THEME=1.
"""

import os
from datetime import date

from src.preparation import skills

MIX = "mix"
MONTHS_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
             "сентябре", "октябре", "ноябре", "декабре"]
INVITE_DAYS = 3
WORDS = ("тема", "тема месяца", "/theme")


def enabled():
    return os.environ.get("GAME_MONTH_THEME") == "1"


def month_of(on):
    on = date.fromisoformat(on) if isinstance(on, str) else on
    return on.strftime("%Y-%m")


def _has_table(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = 'month_themes'").fetchone() is not None


def current(con, child_id, on):
    """The theme's skill for this child and month, MIX, or None (no theme chosen)."""
    if child_id is None or not _has_table(con):
        return None
    row = con.execute(
        "SELECT skill FROM month_themes WHERE child_id = ? AND month = ?", (child_id, month_of(on))
    ).fetchone()
    return None if row is None else (row[0] or MIX)


def chat_children(con, channel, address):
    return con.execute(
        "SELECT c.id, c.name, c.birth_date FROM recipients r JOIN children c ON c.id = r.child_id "
        "WHERE r.channel = ? AND r.address = ? AND r.active = 1 AND c.active = 1 ORDER BY c.id",
        (channel, address),
    ).fetchall()


def _age(birth_iso, on):
    birth = date.fromisoformat(birth_iso)
    return (on.year - birth.year) * 12 + on.month - birth.month - (1 if on.day < birth.day else 0)


def options(con, channel, address, on):
    """Skills open for at least one child of the chat, in the usual order."""
    on = date.fromisoformat(on) if isinstance(on, str) else on
    open_skills = set()
    for child in chat_children(con, channel, address):
        open_skills.update(skills.for_age(_age(child["birth_date"], on)))
    return [key for key in skills.SKILLS if key in open_skills]


def label(key):
    return "разное" if key == MIX else skills.LABELS[key]


def parse(text):
    """None (not about the theme), "" (show the choice) or a skill key / MIX."""
    words = " ".join((text or "").casefold().replace("ё", "е").strip(" .!?,").split())
    if words in WORDS:
        return ""
    for prefix in ("тема месяца ", "тема "):
        if words.startswith(prefix):
            wanted = words[len(prefix):]
            if wanted in ("разное", "микс", "любое", "нет", "без темы"):
                return MIX
            for key, text_label in skills.LABELS.items():
                plain = text_label.replace("ё", "е")
                if wanted == plain or wanted in plain.split() or wanted in plain.split(" и "):
                    return key
            return ""
    return None


def ask(con, channel, address, on):
    """The question with the choices, for a family that wrote «тема»."""
    on = date.fromisoformat(on) if isinstance(on, str) else on
    keys = options(con, channel, address, on)
    if not keys:
        return "Тема месяца появится, когда начнутся игры 🙂"
    chosen = {current(con, c["id"], on) for c in chat_children(con, channel, address)} - {None}
    now = f"Сейчас: {label(chosen.pop())}.\n\n" if len(chosen) == 1 else ""
    text = (f"🧭 Тема месяца — во что в {MONTHS_IN[on.month - 1]} играть почаще.\n\n{now}"
            "Это не цель и не проверка: просто игры этой темы будут чаще. "
            "Сменить или отменить можно в любой момент.")
    if channel == "vk":
        example = "речь" if "speech" in keys else label(keys[0])
        text += (f"\n\nНапишите, например: «тема {example}». "
                 "Варианты: " + ", ".join(label(k) for k in keys) + ", разное.")
    return text


def telegram_markup(con, address, on):
    keys = options(con, "telegram", address, on) + [MIX]
    rows = [[{"text": ("🎲 Разное" if k == MIX else skills.LABELS[k].capitalize()), "callback_data": f"t:{k}"}
             for k in keys[i:i + 2]] for i in range(0, len(keys), 2)]
    return {"inline_keyboard": rows}


def parse_callback(data):
    parts = (data or "").split(":")
    if len(parts) == 2 and parts[0] == "t" and (parts[1] == MIX or parts[1] in skills.SKILLS):
        return parts[1]
    return None


def choose(con, channel, address, key, on):
    """Set the theme for every child of the chat this month. Returns the reply."""
    on = date.fromisoformat(on) if isinstance(on, str) else on
    children = chat_children(con, channel, address)
    if not children:
        return "Тема месяца появится, когда начнутся игры 🙂"
    if key != MIX and key not in options(con, channel, address, on):
        return "Такой темы пока нет для этого возраста. Напишите «тема», покажу варианты."
    for child in children:
        con.execute(
            "INSERT INTO month_themes (child_id, month, skill) VALUES (?, ?, ?) "
            "ON CONFLICT(child_id, month) DO UPDATE SET skill = excluded.skill, chosen_at = CURRENT_TIMESTAMP",
            (child["id"], month_of(on), None if key == MIX else key),
        )
    if key == MIX:
        return "Хорошо, в этом месяце будет всего понемногу 🎲 Сменить можно в любой момент: «тема»."
    return (f"Отлично! В {MONTHS_IN[on.month - 1]} будет больше игр про {label(key)} 🧭 "
            "Сменить или отменить можно в любой момент: «тема».")


def invitation(con, child_id, on):
    """One line under the game in the first days of a month without a theme, or ''."""
    on = date.fromisoformat(on) if isinstance(on, str) else on
    if not enabled() or child_id is None or on.day > INVITE_DAYS or not _has_table(con):
        return ""
    if current(con, child_id, on) is not None:
        return ""
    return (f"🧭 Хотите, чтобы в {MONTHS_IN[on.month - 1]} было больше игр про что-то одно — "
            "речь, движение, моторику? Напишите «тема».")


def required(con, child_id, history, on, age_months):
    """{"skill", "direction": "theme", "after": None} on a theme day, else None."""
    if not enabled():
        return None
    skill = current(con, child_id, on)
    if skill in (None, MIX) or skill not in skills.for_age(age_months):
        return None
    if history and history[-1].get("skill") == skill:
        return None  # every other day at most: the theme is a seasoning, not the menu
    return {"skill": skill, "direction": "theme", "after": None}


def digest_line(con, child_id, game_ids, on):
    """A warm line about the theme for the weekly digest, or None (then say nothing)."""
    if not enabled() or not game_ids:
        return None
    skill = current(con, child_id, on)
    if skill in (None, MIX):
        return None
    marks = ",".join("?" * len(game_ids))
    themed = [r[0] for r in con.execute(
        f"SELECT id FROM games WHERE id IN ({marks}) AND skill = ?", (*game_ids, skill))]
    if not themed:
        return None
    marks = ",".join("?" * len(themed))
    quotes = [r[0] for r in con.execute(
        f"SELECT DISTINCT evidence FROM observations WHERE game_id IN ({marks}) AND evidence != '' "
        f"AND ((kind = 'skill' AND value = 'went_well') OR (kind = 'outcome' AND value = 'liked') "
        f"OR kind = 'like') AND evidence NOT LIKE '👍%' LIMIT 2", themed)]
    liked_taps = con.execute(
        f"SELECT count(*) FROM observations WHERE game_id IN ({marks}) AND kind = 'outcome' "
        f"AND value = 'liked'", themed).fetchone()[0]
    if not quotes and not liked_taps:
        return None
    line = f"🧭 Тема месяца — {label(skill)}: на этой неделе {len(themed)} {_games(len(themed))}."
    if quotes:
        line += " Вы писали: " + "; ".join(f"«{q}»" for q in quotes) + "."
    else:
        line += " Вам понравилось 💛"
    return line


def _games(n):
    if n % 10 == 1 and n % 100 != 11:
        return "игра"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "игры"
    return "игр"
