"""«другую»: today's game does not fit (no ball at home, a sick day), send another one now.

The replacement comes from the library of games already validated and delivered (or the
warm reserve), for the child's age, never one the child already had, and not the same
mechanic as today's. No model call, so the answer is immediate and costs nothing.

One swap per child per day. The games row then holds the replacement, so feedback
written after it is about the game the family actually played; the original is kept in
`swaps`. The owner sees every swap: a swapped game is a signal about the selection.

Deterministic code only. Runs in the caller's transaction; the caller commits.
"""

import json
from datetime import date

from src.preparation import fallback

ORIGINAL_FIELDS = ("title", "age_months", "skill", "mechanics", "source_name", "source_url",
                   "message_text", "origin", "origin_reason", "mechanic_tag")


def _norm(text):
    return " ".join(str(text or "").casefold().replace("ё", "е").split())


def age_months(birth, today):
    months = (today.year - birth.year) * 12 + today.month - birth.month
    return months - 1 if today.day < birth.day else months


def children_of(con, channel, address):
    return con.execute(
        "SELECT c.id, c.name, c.birth_date FROM recipients r JOIN children c ON c.id = r.child_id "
        "WHERE r.channel = ? AND r.address = ? AND r.active = 1 AND c.active = 1 ORDER BY c.id",
        (channel, address),
    ).fetchall()


def history(con, child_id):
    return [dict(r) for r in con.execute(
        "SELECT title, skill, mechanics, mechanic_tag, game_date, age_months FROM games "
        "WHERE child_id = ? ORDER BY game_date, id", (child_id,))]


def swap(con, channel, address, name, today, notify):
    """The reply for the family, or None when this is not a swap request after all (the
    words after «другую» are not a child's name: the caller treats the text as feedback).
    notify(text) records an owner-facing event."""
    kids = children_of(con, channel, address)
    if not kids:
        return None
    if name:
        kids = [k for k in kids if _norm(k["name"]) == _norm(name)]
        if not kids:
            return None  # «другой раз попробуем…» is feedback, not a request: let it through
    elif len(kids) > 1:
        names = ", ".join(k["name"] for k in kids)
        return f"Для кого заменить игру? Напишите, например, «другую {kids[0]['name']}» ({names})."
    kid = kids[0]
    day = today.isoformat()
    game = con.execute(
        "SELECT g.* FROM games g JOIN deliveries d ON d.game_id = g.id "
        "WHERE g.child_id = ? AND g.game_date = ? AND d.channel = ? AND d.recipient = ? AND d.status = 'sent'",
        (kid["id"], day, channel, address),
    ).fetchone()
    if game is None:
        return "Сегодняшняя игра ещё не пришла — заменить пока нечего 🙂"
    if con.execute("SELECT 1 FROM swaps WHERE child_id = ? AND game_date = ?", (kid["id"], day)).fetchone():
        return "Сегодня игру уже заменяли. Завтра утром придёт новая 🧸"

    age = age_months(date.fromisoformat(kid["birth_date"]), today)
    pick = fallback.library_candidate(con, history(con, kid["id"]), age, day, kid["id"])
    if pick is None:
        notify(f"🔄 {kid['name']}: просили другую игру вместо «{game['title']}», замены в библиотеке нет")
        return ("Сейчас подходящей замены нет 🙏 Можно сыграть в сегодняшнюю попроще: "
                "взять похожий предмет из дома или сделать только первые шаги.")

    con.execute(
        "INSERT INTO swaps(child_id, game_id, game_date, channel, address, original_json, replacement_source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kid["id"], game["id"], day, channel, address,
         json.dumps({f: game[f] for f in ORIGINAL_FIELDS if f in game.keys()}, ensure_ascii=False),
         pick["library_source"]),
    )
    con.execute(
        "UPDATE games SET title = ?, age_months = ?, skill = ?, mechanics = ?, source_name = ?, source_url = ?, "
        "message_text = ?, mechanic_tag = ?, origin = 'swap', origin_reason = ?, candidate_json = ? WHERE id = ?",
        (pick["title"], pick["age_months"], pick["skill"], pick["mechanics"], pick["source_name"],
         pick["source_url"], pick["message_text"], pick.get("mechanic_tag"),
         f"вместо «{game['title']}»", fallback.candidate_json(pick), game["id"]),
    )
    notify(f"🔄 {kid['name']}: заменили «{game['title']}» → «{pick['title']}»")
    return "Вот другая игра на сегодня 👇\n\n" + pick["message_text"]


def parse(text):
    """(True, name or None) for «другую [имя]» / «другая игра» / /another, else (False, None)."""
    words = [w.strip(".,!?;:()") for w in _norm(text).split()]
    words = [w for w in words if w]
    if not words:
        return False, None
    if words[0] in ("/another", "другую", "другая", "другой"):
        rest = [w for w in words[1:] if w not in ("игру", "игра", "для", "пожалуйста", "плиз")]
        return True, (" ".join(rest) or None)
    return False, None
