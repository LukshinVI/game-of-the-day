"""One-tap reactions under the game: two rows of buttons, no model.

    [ 👍 Понравилось ] [ 👎 Не зашло ]
    [ 🪶 Легко ] [ 👌 В самый раз ] [ 🧗 Сложно ]

After «Не зашло» the family is asked once what got in the way (four more buttons or a
few words of their own); the reason steers the next games through the child's profile:

    [ 😐 Не заинтересовало ] [ 🧸 Не было нужного дома ]
    [ ⏱ Долго/сложно готовить ] [ 👶 Не по возрасту ]

A tap becomes the same observation a written "too easy" or "liked it" would, attached to
the exact game the button sits under (so siblings in one chat never mix), and the child's
profile is rebuilt at once. One answer per row and game: a second tap replaces the first.
The feedback row is marked as handled, so the insights model never sees it; a written
message still goes to the model as before.

Telegram buttons carry "r:<game id>:<code>" as callback data; VK text buttons carry
{"r": code, "g": game id} as payload and arrive as an ordinary message from the family.
Off unless GAME_BUTTONS=1 (the buttons are not shown; a stale press is still honoured).
"""

import json
import os
from datetime import datetime, timezone

from src.feedback import profile

CHOICES = {
    "liked": ("👍 Понравилось", "outcome", "liked"),
    "disliked": ("👎 Не зашло", "outcome", "disliked"),
    "easy": ("🪶 Легко", "difficulty", "too_easy"),
    "right": ("👌 В самый раз", "difficulty", "right"),
    "hard": ("🧗 Сложно", "difficulty", "too_hard"),
    "bored": ("😐 Не заинтересовало", "reason", "not_interested"),
    "no_items": ("🧸 Не было нужного дома", "reason", "no_materials"),
    "prep": ("⏱ Долго/сложно готовить", "reason", "prep_heavy"),
    "age": ("👶 Не по возрасту", "reason", "not_for_age"),
}
ROWS = (("liked", "disliked"), ("easy", "right", "hard"))
REASON_ROWS = (("bored", "no_items"), ("prep", "age"))
REASONS = {code for row in REASON_ROWS for code in row}
QUESTION = "Жаль 🙈 Что помешало? Нажмите кнопку или напишите своими словами — подстроим следующие игры."
QUESTION_PLAIN = "Жаль 🙈 Что помешало? Напишите пару слов — подстроим следующие игры."
SKILL_LABELS = {
    "coordination": "движение", "sensory": "сенсорику", "social_emotional": "общение",
    "speech": "речь", "fine_motor": "моторику", "cognitive": "мышление",
    "self_care": "самостоятельность", "imagination": "воображение",
}
FOOTER = "💬 Нажмите кнопку или напишите пару слов — следующие игры подстроятся под ребёнка."


def enabled():
    return os.environ.get("GAME_BUTTONS") == "1"


def rows_for(code):
    """The keyboard a button belongs to: the reasons or the reactions under the game."""
    return REASON_ROWS if code in REASONS else ROWS


def telegram_markup(game_id, chosen=(), rows=ROWS):
    return {"inline_keyboard": [
        [{"text": ("✓ " if code in chosen else "") + CHOICES[code][0], "callback_data": f"r:{game_id}:{code}"}
         for code in row]
        for row in rows
    ]}


def vk_keyboard(game_id, rows=ROWS):
    return json.dumps({"inline": True, "buttons": [
        [{"action": {"type": "text", "label": CHOICES[code][0],
                     "payload": json.dumps({"r": code, "g": game_id})}, "color": "secondary"}
         for code in row]
        for row in rows
    ]}, ensure_ascii=False)


def parse_telegram(data):
    """(game_id, code) from callback data, or None."""
    parts = (data or "").split(":")
    if len(parts) == 3 and parts[0] == "r" and parts[1].isdigit() and parts[2] in CHOICES:
        return int(parts[1]), parts[2]
    return None


def parse_vk(payload):
    """(game_id, code) from a VK button payload, or None (not one of ours)."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else None
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("r") in CHOICES and isinstance(data.get("g"), int):
        return data["g"], data["r"]
    return None


def chosen(con, game_id, channel, address):
    """Codes already pressed for this game in this chat (to mark them ✓)."""
    rows = con.execute(
        "SELECT raw_text FROM feedback WHERE game_id = ? AND channel = ? AND external_event_id LIKE ?",
        (game_id, channel, f"btn:{address}:{game_id}:%"),
    ).fetchall()
    labels = {row[0] for row in rows}
    return {code for code, (label, _, _) in CHOICES.items() if f"[кнопка] {label}" in labels}


def needs_reason(con, game_id, channel, address):
    """True before a «Не зашло» press that should ask why: asked once per game and chat
    (not when the outcome already was «Не зашло», nor when a reason is in)."""
    rows = {row[0]: row[1] for row in con.execute(
        "SELECT external_event_id, raw_text FROM feedback WHERE channel = ? AND external_event_id IN (?, ?)",
        (channel, f"btn:{address}:{game_id}:outcome", f"btn:{address}:{game_id}:reason"))}
    return (f"btn:{address}:{game_id}:reason" not in rows
            and rows.get(f"btn:{address}:{game_id}:outcome") != f"[кнопка] {CHOICES['disliked'][0]}")


def record(con, game_id, code, channel, address, now=None):
    """Store a press and update the child's profile. Returns the reply, or None when the game
    was never sent to this chat (a forged or foreign button)."""
    label, kind, value = CHOICES[code]
    game = con.execute(
        "SELECT g.id, g.skill, g.child_id FROM games g JOIN deliveries d ON d.game_id = g.id "
        "WHERE g.id = ? AND d.channel = ? AND (d.recipient = ? OR d.recipient IS NULL) LIMIT 1",
        (game_id, channel, address),
    ).fetchone()
    if game is None:
        return None
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    event = f"btn:{address}:{game_id}:{kind}"  # one answer per row: a second tap replaces it
    row = con.execute(
        "SELECT id FROM feedback WHERE channel = ? AND external_event_id = ?", (channel, event)
    ).fetchone()
    if row is None:
        feedback_id = con.execute(
            "INSERT INTO feedback (game_id, channel, external_event_id, raw_text, received_at, "
            "insights_at, insights_status) VALUES (?, ?, ?, ?, ?, ?, 'button')",
            (game_id, channel, event, f"[кнопка] {label}", stamp, stamp),
        ).lastrowid
    else:
        feedback_id = row[0]
        con.execute(
            "UPDATE feedback SET raw_text = ?, received_at = ?, insights_at = ? WHERE id = ?",
            (f"[кнопка] {label}", stamp, stamp, feedback_id),
        )
        con.execute("DELETE FROM observations WHERE feedback_id = ?", (feedback_id,))
    skill = game[1] if kind == "difficulty" else None
    con.execute(
        "INSERT INTO observations (feedback_id, child_id, game_id, kind, value, skill, evidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (feedback_id, game[2], game_id, kind, value, skill, label),
    )
    if value == "not_for_age" and game[1]:  # agreed with the owner: the same as «Сложно»
        con.execute(
            "INSERT INTO observations (feedback_id, child_id, game_id, kind, value, skill, evidence) "
            "VALUES (?, ?, ?, 'difficulty', 'too_hard', ?, ?)",
            (feedback_id, game[2], game_id, game[1], label),
        )
    if game[2] is not None:
        profile.rebuild(con, game[2], now)
    return reply(code, game[1])


TOAST = "Спасибо, учли 💛"


def note(code, skill):
    """The sentence worth a chat message after a Telegram press, or None (the ✓ is enough)."""
    return reply(code, skill) if code in {"easy", "hard"} | REASONS else None


def reply(code, skill):
    about = SKILL_LABELS.get(skill)
    if code == "easy" and about:
        return f"Спасибо, учли 💛 Следующая игра на {about} будет чуть посложнее."
    if code in ("hard", "age") and about:
        return f"Спасибо, учли 💛 Следующая игра на {about} будет чуть попроще."
    if code == "bored":
        return "Спасибо, учли 💛 Попробуем зайти к этому с другой стороны."
    if code == "no_items":
        return "Спасибо, учли 💛 В ближайших играх — только то, что найдётся в любом доме."
    if code == "prep":
        return "Спасибо, учли 💛 Ближайшие игры будут проще в подготовке."
    if code == "disliked":
        return "Спасибо, учли 💛 Учтём при подборе следующих игр."
    return "Спасибо, учли 💛"
