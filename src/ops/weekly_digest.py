#!/usr/bin/env python3
"""Sunday digest for parents: the week in games, what went well, what to try next.

For every active, non-test child with at least MIN_GAMES games this week, one message
per recipient: games and skills of the week, what the parents' feedback said went well,
and one or two home ideas for next week (a liked game to repeat, skills played least).
Deliberately no age norms or milestones: a list of what children "usually do" by an age
reads as a test and worries parents whose child is not there yet. On the first Sunday of a month it also
asks one question: what is missing in the service (the answer arrives as ordinary
feedback and the insights job routes it to the owner as a wish).

Once per recipient, ever, a digest may also ask to star the project on GitHub — only after
the child has had games for at least a month and the family's recent feedback is
positive (see star_request_due). Off unless GAME_DIGEST_STAR_REQUEST=1. Never in the same digest as the monthly question.

On the third Sunday of a month the digest may ask «насколько полезно?» (1-5, src/ops/pulse.py),
off unless GAME_DIGEST_PULSE=1; never together with the star request.

What is new for parents (src/ops/news.json, written by hand after a release that
parents can notice) goes into the digest of the week it was dated, at most NEWS_MAX
items, with a link to the community wall post when GAME_NEWS_LINK is set. No separate
message ever: news ride on the digest that is sent anyway.

Deterministic: no model call. Idempotent per child, recipient and week.

    python3 src/ops/weekly_digest.py run [--date YYYY-MM-DD] [--dry-run]
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.delivery import game_delivery  # noqa: E402
from src.ops import pulse  # noqa: E402
from src.preparation import skills  # noqa: E402

DB_PATH = ROOT / "data" / "game-of-the-day.db"
MOSCOW = ZoneInfo("Europe/Moscow")
# A digest of one or two games (a family that just joined, a week after an outage)
# says nothing useful; wait for a real week.
MIN_GAMES = 3

REPO_URL = "https://github.com/LukshinVI/game-of-the-day"
# Ask for a star only after a month of games and only when the last month's feedback
# says it is going well: at least one "liked" and no more "disliked" than "liked".
STAR_MIN_DAYS = 30
STAR_TEXT = ("⭐ Вы с нами уже больше месяца. Если сервис вам нравится, поставьте, пожалуйста, "
             f"звёздочку проекту на GitHub: {REPO_URL}\n"
             "Это бесплатно и очень помогает автору. Нет аккаунта на GitHub — просто напишите "
             "в ответ пару слов, что нравится, это тоже ценно 🙂")

# Star request stays off until the owner decides to send it (set GAME_DIGEST_STAR_REQUEST=1).
STAR_ENABLED_ENV = "GAME_DIGEST_STAR_REQUEST"

# "What to try next week": one home idea per skill and age band, chosen by code.
# No norms and no "by N months your child should": parents must not read the digest
# as a test their child can fail. Ideas use only big, safe household things.
# skill -> [(from_months, idea)], the last band whose from_months <= age wins.
IDEAS = {
    "coordination": [
        (0, "почаще выкладывайте малыша на животик, положите перед ним яркую игрушку и лягте рядом лицом к лицу"),
        (6, "положите игрушку чуть дальше, чем малыш может дотянуться, пусть потянется или поползёт за ней"),
        (12, "покатайте мяч друг другу по полу и поиграйте в «догонялки» шагом"),
        (24, "попрыгайте вместе «как зайчики» и пройдите по дорожке из скотча на полу"),
    ],
    "sensory": [
        (0, "дайте потрогать разные ткани: мягкий плед, гладкую наволочку, махровое полотенце, и называйте их"),
        (6, "поиграйте с водой в тазике (только рядом с вами): переливайте из стаканчика, шлёпайте ладошками"),
        (12, "сделайте дорожку из коврика, полотенца и подушки и пройдите по ней босиком"),
        (24, "спрячьте знакомую игрушку в мешочек и предложите угадать её на ощупь"),
    ],
    "social_emotional": [
        (0, "повторяйте за малышом его звуки и выражения лица, это ваш первый «разговор»"),
        (6, "поиграйте в «ку-ку» с платочком и в «ладушки»"),
        (12, "играйте по очереди: «твоя очередь бросить мяч в корзину, теперь моя»"),
        (24, "называйте чувства в книжках и в жизни: «мишка грустит», «ты обрадовался»"),
    ],
    "speech": [
        (0, "рассказывайте малышу, что вы делаете, с паузами, чтобы он мог «ответить» гулением"),
        (6, "называйте то, на что малыш смотрит или показывает: «это мяч», «это кошка»"),
        (12, "разглядывайте книжки с картинками и спрашивайте: «где собачка?»"),
        (24, "добавляйте к словам малыша ещё одно: «машина» — «красная машина едет»"),
    ],
    "fine_motor": [
        (3, "подавайте погремушку то с одной, то с другой стороны, чтобы малыш тянулся и хватал"),
        (6, "дайте вынимать платочки из коробки или крупные кубики из миски"),
        (12, "стройте вместе башню из больших кубиков и складывайте игрушки в коробку"),
        (24, "порисуйте вместе мелками на большом листе или полепите из теста"),
    ],
    "cognitive": [
        (4, "покажите, как звенит погремушка, когда её трясёшь, и дайте попробовать самому"),
        (6, "спрячьте игрушку под платочек на глазах у малыша и предложите найти"),
        (12, "разложите вместе вещи по корзинкам: носочки отдельно, мячики отдельно"),
        (24, "соберите простой пазл из 2–4 крупных частей или найдите одинаковые картинки"),
    ],
    "self_care": [
        (12, "дайте малышу самому попить из кружки и вытереть ручки полотенцем"),
        (24, "предложите самому снять носки и шапку, а потом вместе убрать игрушки"),
    ],
    "imagination": [
        (18, "покормите вместе мишку или куклу игрушечной ложкой и уложите спать"),
        (24, "поиграйте с игрушками в «магазин» или «доктора»"),
    ],
}
MAX_IDEAS = 2


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def age_months(birth, today):
    birth = date.fromisoformat(birth)
    months = (today.year - birth.year) * 12 + today.month - birth.month
    return months - (1 if today.day < birth.day else 0)


def idea_for(skill, age):
    """The idea for this skill at this age, or None if the skill is not for this age yet."""
    idea = None
    for start, text in IDEAS.get(skill, []):
        if age >= start:
            idea = text
    return idea


def next_week_ideas(age, games, liked, today):
    """Up to MAX_IDEAS ideas: repeat a liked game, then the skills least played this week.

    Ties rotate with the week number so a family does not read the same idea every Sunday.
    """
    ideas = []
    if liked:
        ideas.append(f"повторите «{liked[0]}»: малыши любят знакомые игры, а вы можете добавить что-то новое")
    played = Counter(g["skill"] for g in games if g["skill"])
    allowed = [k for k in skills.for_age(age) if idea_for(k, age)]
    week = today.isocalendar().week
    order = {k: (i - week) % len(allowed) for i, k in enumerate(allowed)}
    for key in sorted(allowed, key=lambda k: (played.get(k, 0), order[k])):
        if len(ideas) >= MAX_IDEAS:
            break
        ideas.append(f"{skills.LABELS[key]}: {idea_for(key, age)}")
    return ideas


NEWS_PATH = Path(__file__).resolve().parent / "news.json"
NEWS_MAX = 2


def news_for(today, path=None):
    """News dated within the digest's week (today and the six days before)."""
    try:
        items = json.loads(Path(path or NEWS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    week_start = today - timedelta(days=6)
    fresh = []
    for item in items:
        try:
            when = date.fromisoformat(str(item.get("date")))
        except (ValueError, AttributeError):
            continue
        text = str(item.get("text") or "").strip()
        if text and week_start <= when <= today:
            fresh.append((when, text))
    return [text for _, text in sorted(fresh, reverse=True)[:NEWS_MAX]]


def compose(con, child, today):
    week_start = today - timedelta(days=6)
    games = con.execute(
        "SELECT id, title, skill FROM games WHERE child_id = ? AND game_date BETWEEN ? AND ? ORDER BY game_date",
        (child["id"], week_start.isoformat(), today.isoformat()),
    ).fetchall()
    if len(games) < MIN_GAMES:
        return None
    ids = [g["id"] for g in games]
    marks = ",".join("?" * len(ids))
    went_well = [r[0] for r in con.execute(
        f"SELECT DISTINCT evidence FROM observations WHERE game_id IN ({marks}) AND kind = 'skill' "
        f"AND value = 'went_well' LIMIT 3", ids)] if _has(con, "observations") else []
    liked = [r[0] for r in con.execute(
        f"SELECT DISTINCT g.title FROM observations o JOIN games g ON g.id = o.game_id WHERE o.game_id IN ({marks}) "
        f"AND o.kind = 'outcome' AND o.value = 'liked' LIMIT 3", ids)] if _has(con, "observations") else []

    counts = Counter(skills.LABELS.get(g["skill"], g["skill"]) for g in games if g["skill"])
    lines = [f"🗓 Итоги недели — {child['name']}: {len(games)} {_games_word(len(games))}",
             "Навыки: " + ", ".join(f"{label} ×{n}" for label, n in counts.most_common())]
    if liked:
        lines.append("❤️ Понравилось: " + ", ".join(f"«{t}»" for t in liked))
    if went_well:
        lines.append("🌱 Получалось: " + "; ".join(went_well))
    ideas = next_week_ideas(age_months(child["birth_date"], today), games, liked, today)
    if ideas:
        lines += ["", "✨ Что попробовать на следующей неделе:", *[f"• {_cap(i)}" for i in ideas]]
    news = news_for(today)
    if news:
        lines += ["", "🆕 Что нового:", *[f"• {_cap(n)}" for n in news]]
        link = os.environ.get("GAME_NEWS_LINK", "").strip()
        if link:
            lines.append(f"Подробнее — в нашей группе: {link}")
    if today.day <= 7:
        lines += ["", "💡 Раз в месяц спрашиваем: чего не хватает в сервисе, что улучшить? "
                      "Ответьте на это сообщение одним предложением."]
    else:
        lines += ["", "💬 Отзыв на любую игру помогает подбирать следующие."]
    return "\n".join(lines)


def star_request_due(con, child, channel, address, today):
    """True when this recipient should get the one-time GitHub star request today."""
    if os.environ.get(STAR_ENABLED_ENV) != "1":
        return False  # off until the owner decides
    if today.day <= 7 or not _has(con, "star_requests"):
        return False  # the first Sunday carries the monthly question; one ask at a time
    if con.execute("SELECT 1 FROM star_requests WHERE channel = ? AND address = ?",
                   (channel, address)).fetchone():
        return False
    first = con.execute(
        "SELECT min(g.game_date) FROM games g JOIN deliveries d ON d.game_id = g.id "
        "WHERE g.child_id = ? AND d.status = 'sent'", (child["id"],)).fetchone()[0]
    if not first or (today - date.fromisoformat(first)).days < STAR_MIN_DAYS:
        return False
    if not _has(con, "observations"):
        return False
    since = (today - timedelta(days=STAR_MIN_DAYS)).isoformat()
    counts = dict(con.execute(
        "SELECT o.value, count(*) FROM observations o JOIN games g ON g.id = o.game_id "
        "WHERE g.child_id = ? AND g.game_date > ? AND o.kind = 'outcome' "
        "AND o.value IN ('liked', 'disliked') GROUP BY o.value", (child["id"], since)).fetchall())
    liked, disliked = counts.get("liked", 0), counts.get("disliked", 0)
    return liked >= 1 and disliked <= liked


def _cap(text):
    return text[:1].upper() + text[1:]


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def _games_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "игра"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "игры"
    return "игр"


def send(channel, address, text, key):
    if channel == "vk":
        random_id = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        return game_delivery.vk_send(text, random_id, peer_id=address)
    if channel == "telegram":
        return game_delivery.telegram_send(text, address)
    raise RuntimeError(f"unknown channel {channel}")


def run(today=None, dry_run=False, db_path=None, sender=send):
    today = today or datetime.now(MOSCOW).date()
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    con = connect(db_path)
    sent = 0
    try:
        children = con.execute(
            "SELECT id, name, birth_date FROM children WHERE active = 1 AND is_test = 0 ORDER BY id"
        ).fetchall()
        for child in children:
            text = compose(con, child, today)
            if text is None:
                continue
            recipients = con.execute(
                "SELECT channel, address FROM recipients WHERE child_id = ? AND active = 1", (child["id"],)
            ).fetchall()
            for r in recipients:
                done = con.execute("SELECT 1 FROM digests WHERE child_id = ? AND channel = ? AND address = ? AND week = ?",
                                   (child["id"], r["channel"], r["address"], week)).fetchone()
                if done:
                    continue
                ask_pulse = pulse.due(con, child["id"], r["channel"], r["address"], today)
                ask_star = not ask_pulse and star_request_due(con, child, r["channel"], r["address"], today)
                message = (f"{text}\n\n{pulse.QUESTION}" if ask_pulse
                           else f"{text}\n\n{STAR_TEXT}" if ask_star else text)
                if dry_run:
                    print(f"--- {child['name']} → {r['channel']}\n{message}\n")
                    continue
                try:
                    sender(r["channel"], r["address"], message, f"digest-{week}-{child['id']}-{r['address']}")
                except Exception as exc:  # noqa: BLE001 - one recipient failing must not stop the others
                    print(f"digest to {r['channel']} failed: {type(exc).__name__}", file=sys.stderr)
                    continue
                con.execute("INSERT INTO digests(child_id, channel, address, week) VALUES (?, ?, ?, ?)",
                            (child["id"], r["channel"], r["address"], week))
                if ask_pulse:
                    pulse.asked(con, child["id"], r["channel"], r["address"], today)
                if ask_star:
                    con.execute("INSERT OR IGNORE INTO star_requests(channel, address, child_id) VALUES (?, ?, ?)",
                                (r["channel"], r["address"], child["id"]))
                con.commit()
                sent += 1
    finally:
        con.close()
    print(f"digests sent: {sent}")
    return sent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="weekly-digest")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--date")
    r.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run(date.fromisoformat(args.date) if args.date else None, args.dry_run)


if __name__ == "__main__":
    main()
