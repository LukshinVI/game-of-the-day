"""Non-text feedback: stickers, photos, videos, voice messages.

They show the game happened, but carry nothing a model could reliably read. The
channel adapters turn them into labels ("[стикер 👍]", "[фото]"); a reaction alone
gets one polite request to write a few words, at most once per game, and costs no
model call. Text sent along with a reaction is analysed as usual. Media are never
downloaded or stored: these are photos of children, the fact is enough.
"""

import re

LABEL = re.compile(r"\[(?:стикер|фото|видео|голосовое|файл)[^\]]*\]")
NUDGE = (
    "Спасибо за реакцию! 💛 Напишите, пожалуйста, пару слов текстом: понравилось ли, "
    "было ли легко или сложно. Так мы точнее подстроим следующие игры."
)


def strip_labels(text):
    return " ".join(LABEL.sub(" ", text or "").split())


def is_reaction(text):
    return bool(LABEL.search(text or "")) and not strip_labels(text)


def telegram_label(message):
    """A label for a Telegram message without text, or None."""
    if message.get("sticker"):
        emoji = (message["sticker"] or {}).get("emoji") or ""
        return f"[стикер {emoji}]".replace(" ]", "]")
    for key, label in (("photo", "[фото]"), ("video", "[видео]"), ("video_note", "[видео]"),
                       ("animation", "[видео]"), ("voice", "[голосовое]"), ("audio", "[голосовое]"),
                       ("document", "[файл]")):
        if message.get(key):
            return label
    return None


def already_nudged(con, game_id):
    return con.execute(
        "SELECT 1 FROM feedback WHERE game_id = ? AND insights_status = 'reaction' AND reply_sent_at IS NOT NULL",
        (game_id,),
    ).fetchone() is not None
