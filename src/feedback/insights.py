#!/usr/bin/env python3
"""Feedback -> observations -> child profile, with a short reply to the parent.

Runs every 2 minutes (game-feedback-insights.timer):

1. Pending feedback (stored by the queue consumer for VK or by the product bot for
   Telegram) is grouped per game: messages sent within SETTLE_MINUTES of each other are
   merged, and a group is processed only once its newest message is SETTLE_MINUTES old.
2. Caps: at most PER_CHILD_PER_DAY analyses per child and GLOBAL_PER_DAY in total; the
   rest stays pending until the next day. No dialogue: one reply per group.
3. A small model (INSIGHTS_MODEL, Haiku by default) extracts observations into a strict
   schema. Code keeps an observation only if its quote is verbatim in the feedback and
   its fields are valid; the model cannot invent facts.
4. The child's profile is rebuilt by code (src/feedback/profile.py).
5. The parent gets one deterministic reply saying what was taken into account.

With GAME_FEEDBACK_CONTEXT ("1": every child, "test": test children only) the model also
sees the child's games of the last weeks and its own recent observations, so it can:
put an impression on the game it is about ("та игра с мячиками была классная"), note how
the recent games went in general, note where and when the family plays (before sleep:
calmer games, not "dislikes active games"), and take back an observation the parent
corrects ("нет, не так"). Code checks every game id and every retraction against what
the model was shown.

    python3 src/feedback/insights.py run
    python3 src/feedback/insights.py run --no-reply
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.feedback import profile, reactions  # noqa: E402
from src.preparation import skills  # noqa: E402

DB_PATH = ROOT / "data" / "game-of-the-day.db"
PROMPT_PATH = ROOT / "src" / "feedback" / "prompts" / "insights.md"
SETTLE_MINUTES = 3  # the parent is told at once that the message arrived (reactions.ack)
PER_CHILD_PER_DAY = int(os.environ.get("INSIGHTS_PER_CHILD_PER_DAY", "3"))
GLOBAL_PER_DAY = int(os.environ.get("INSIGHTS_GLOBAL_PER_DAY", "20"))
MAX_TEXT_CHARS = 1500
MAX_OBSERVATIONS = 8
REPLY_WITHIN_HOURS = 24
MODEL_TIMEOUT_SECONDS = 180
CONTEXT_PROMPT_PATH = ROOT / "src" / "feedback" / "prompts" / "insights_context.md"
RECENT_GAMES_DAYS = 21
RECENT_GAMES_MAX = 14
CORRECTABLE_DAYS = 3
CORRECTABLE_MAX = 12
MAX_RETRACT = 5

KINDS = {
    "outcome": {"liked", "disliked", "neutral", "not_played"},
    "difficulty": {"too_easy", "right", "too_hard"},
    "skill": {"went_well", "hard"},
    # Focus, tiredness, mood: how the child was, not how hard the game was. Never a
    # difficulty signal (production 2026-11-10: "сложновато удержать внимание" made the
    # next games easier although the child had managed the game).
    "attention": {"short", "tired"},
    # Why the game did not work, the same reasons as the buttons after «Не зашло» (a game
    # "not for the age" is a difficulty): the profile's `why_not`.
    "reason": {"not_interested", "no_materials", "prep_heavy"},
    # Where and when the family plays (GAME_FEEDBACK_CONTEXT): a condition for the next
    # games, never a dislike ("играем перед сном, нужно спокойнее").
    "setting": {"bedtime", "outdoors", "on_the_go"},
    # How the recent games went in general, not one game ("потом всё стало не очень").
    "recent": {"worse", "better"},
    "like": None,      # free short phrase
    "dislike": None,
    "wish": None,      # about the service, not the child: goes to the owner, not the profile
}

CONTEXT_KINDS = {"setting", "recent"}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["observations"],
    "properties": {
        "observations": {
            "type": "array",
            "maxItems": MAX_OBSERVATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "value", "skill", "evidence"],
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "value": {"type": "string", "maxLength": 120},
                    "skill": {"type": ["string", "null"], "enum": [*skills.ALL, None]},
                    "evidence": {"type": "string", "maxLength": 200},
                },
            },
        },
    },
}


def schema(context=False):
    """The output schema; with context an observation names its game and may retract."""
    if not context:
        return {**SCHEMA, "properties": {"observations": {
            **SCHEMA["properties"]["observations"],
            "items": {**SCHEMA["properties"]["observations"]["items"], "properties": {
                **SCHEMA["properties"]["observations"]["items"]["properties"],
                "kind": {"type": "string", "enum": [k for k in KINDS if k not in CONTEXT_KINDS]}}}}}}
    item = SCHEMA["properties"]["observations"]["items"]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["observations", "retract"],
        "properties": {
            "observations": {**SCHEMA["properties"]["observations"], "items": {
                **item,
                "required": [*item["required"], "game"],
                "properties": {**item["properties"], "game": {"type": ["integer", "null"]}},
            }},
            "retract": {"type": "array", "maxItems": MAX_RETRACT, "items": {"type": "integer"}},
        },
    }


def context_on(con, child_id):
    flag = os.environ.get("GAME_FEEDBACK_CONTEXT", "")
    if flag == "1":
        return True
    if flag != "test" or child_id is None:
        return False
    row = con.execute("SELECT is_test FROM children WHERE id = ?", (child_id,)).fetchone()
    return bool(row and row[0])


def recent_context(con, group, now):
    """The child's recent games and the observations the model may take back."""
    child_id, game_id = group[0]["child_id"], group[0]["game_id"]
    since = (now - timedelta(days=RECENT_GAMES_DAYS)).date().isoformat()
    games = [dict(r) for r in con.execute(
        "SELECT DISTINCT g.id, g.game_date AS date, g.title, g.skill FROM games g "
        "JOIN deliveries d ON d.game_id = g.id AND d.status = 'sent' "
        "WHERE g.child_id = ? AND (g.game_date >= ? OR g.id = ?) "
        "ORDER BY g.game_date DESC, g.id DESC LIMIT ?",
        (child_id, since, game_id, RECENT_GAMES_MAX),
    )]
    if not any(g["id"] == game_id for g in games):
        games.insert(0, {"id": game_id, "date": None, "title": group[0]["title"], "skill": group[0]["skill"]})
    after = (now - timedelta(days=CORRECTABLE_DAYS)).isoformat(sep=" ", timespec="seconds")
    previous = [dict(r) for r in con.execute(
        "SELECT o.id, o.game_id AS game, o.kind, o.value, o.evidence FROM observations o "
        "WHERE o.child_id = ? AND o.created_at >= ? ORDER BY o.id DESC LIMIT ?",
        (child_id, after[:19], CORRECTABLE_MAX),
    )]
    return {"this_game_id": game_id, "recent_games": games, "previous_observations": previous}


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def pending_groups(con, now):
    """Pending feedback grouped per game, merged when sent close together."""
    rows = [dict(r) for r in con.execute(
        """
        SELECT f.id, f.game_id, f.channel, f.external_event_id, f.raw_text, f.received_at,
               g.child_id, g.title, g.skill, g.mechanics, g.age_months
        FROM feedback f JOIN games g ON g.id = f.game_id
        WHERE f.insights_at IS NULL
        ORDER BY f.game_id, f.received_at, f.id
        """
    )]
    groups = []
    for row in rows:
        row["at"] = parse_time(row["received_at"])
        last = groups[-1] if groups else None
        if last and last[-1]["game_id"] == row["game_id"] and row["at"] - last[-1]["at"] <= timedelta(minutes=SETTLE_MINUTES):
            last.append(row)
        else:
            groups.append([row])
    return [g for g in groups if now - g[-1]["at"] >= timedelta(minutes=SETTLE_MINUTES)]


def analyses_today(con, now, child_id=None):
    day = now.date().isoformat()
    query = ("SELECT count(DISTINCT f.game_id || '@' || f.insights_at) FROM feedback f JOIN games g ON g.id = f.game_id "
             "WHERE f.insights_status IN ('done', 'reply_failed') AND substr(f.insights_at, 1, 10) = ?")
    params = [day]
    if child_id is not None:
        query += " AND g.child_id = ?"
        params.append(child_id)
    return con.execute(query, params).fetchone()[0]


def build_prompt(group, extra=None):
    game = group[0]
    text = "\n".join(reactions.strip_labels(r["raw_text"]) for r in group if reactions.strip_labels(r["raw_text"]))[:MAX_TEXT_CHARS]
    context = {
        "game": {"title": game["title"], "skill": game["skill"], "mechanics": game["mechanics"],
                 "age_months": game["age_months"]},
        "skills": {key: label for key, label in skills.LABELS.items()},
        **(extra or {}),
        "feedback": text,
    }
    instructions = PROMPT_PATH.read_text(encoding="utf-8")
    if extra:
        instructions += "\n\n" + CONTEXT_PROMPT_PATH.read_text(encoding="utf-8")
    return instructions + "\n\nINPUT:\n" + json.dumps(context, ensure_ascii=False, indent=2), text


def run_model(prompt, context=False):
    command = [
        os.environ.get("CLAUDE_BIN", "claude"), "--print", "--output-format", "json",
        "--json-schema", json.dumps(schema(context)), "--model", os.environ.get("INSIGHTS_MODEL", "haiku"),
        "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
    ]
    result = subprocess.run(command, input=prompt, text=True, capture_output=True, cwd=ROOT,
                            timeout=MODEL_TIMEOUT_SECONDS)
    envelope = json.loads(result.stdout or "{}")
    output = envelope.get("structured_output")
    if result.returncode != 0 or envelope.get("is_error") or not isinstance(output, dict):
        raise RuntimeError(f"insights model failed: {(result.stderr or str(envelope.get('result')))[:300]}")
    return output


def normalize(text):
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def valid_observations(output, text, game_skill, games=None):
    """Keep only well-formed observations whose quote is verbatim in the feedback.

    `games` ({id: skill}, with context only) are the games the model was shown: an
    observation about one of them gets its "game_id"; anything else stays on this game.
    """
    kept, haystack = [], normalize(text)
    for obs in output.get("observations", [])[:MAX_OBSERVATIONS]:
        kind, value = obs.get("kind"), str(obs.get("value", "")).strip()
        evidence = str(obs.get("evidence", "")).strip()
        skill = obs.get("skill")
        if kind not in KINDS or not value or not evidence or normalize(evidence) not in haystack:
            continue
        if kind in CONTEXT_KINDS and games is None:
            continue
        other = obs.get("game") if games and obs.get("game") in games else None
        if other is not None and kind in ("outcome", "difficulty", "skill", "reason"):
            game_skill_here = games[other]
        else:
            other, game_skill_here = None, game_skill
        allowed = KINDS[kind]
        if allowed is not None and value not in allowed:
            continue
        if kind in ("difficulty", "skill"):
            skill = skill if skill in skills.ALL else game_skill_here
            if skill not in skills.ALL:
                continue
        else:
            skill = skill if skill in skills.ALL else None
        if kind in ("like", "dislike"):
            value = value[:60].lower()
        if kind == "wish":
            value = value[:120]
        if kind in ("attention", "reason") or kind in CONTEXT_KINDS:
            skill = None
        kept.append({"kind": kind, "value": value, "skill": skill, "evidence": evidence,
                     **({"game_id": other} if other is not None else {})})
    # "Went well" and "too hard" for the same skill contradict each other: the child did
    # it, so what was hard is something else (usually attention). Keep the fact, drop
    # the signal that would make the next game easier.
    managed = {(o.get("game_id"), o["skill"]) for o in kept if o["kind"] == "skill" and o["value"] == "went_well"}
    return [o for o in kept if not (o["kind"] == "difficulty" and o["value"] == "too_hard"
                                    and (o.get("game_id"), o["skill"]) in managed)]


def valid_retractions(output, shown):
    """Observation ids the model takes back, only among those it was shown."""
    ids = {o["id"] for o in shown}
    return [i for i in dict.fromkeys(output.get("retract") or []) if isinstance(i, int) and i in ids][:MAX_RETRACT]


def describe(obs):
    """A previous observation in the parent's words, for "поправили: ..."."""
    kind, value = obs["kind"], obs["value"]
    if kind == "like":
        return f"любит: {value}"
    if kind == "dislike":
        return f"не любит: {value}"
    if kind == "outcome":
        return {"liked": "игра понравилась", "disliked": "игра не зашла"}.get(value, "прошлый вывод")
    if kind == "difficulty":
        return {"too_easy": "было легко", "too_hard": "было сложно", "right": "сложность в самый раз"}[value] \
            if value in ("too_easy", "too_hard", "right") else "прошлый вывод"
    if kind == "attention":
        return "внимание трудно удержать" if value == "short" else "малыш был уставший"
    return "прошлый вывод"


DIFFICULTY_TEXT = {"too_easy": "было легко → дальше дадим посложнее",
                   "too_hard": "было сложно → дальше дадим попроще",
                   "right": "сложность в самый раз"}


REASON_TEXT = {
    "not_interested": "• не заинтересовало → попробуем зайти к навыку с другой стороны",
    "no_materials": "• не нашлось нужного дома → в ближайших играх только то, что есть в любом доме",
    "prep_heavy": "• долго готовить → ближайшие игры будут проще в подготовке",
}


SETTING_TEXT = {
    "bedtime": "• играете перед сном → подберём игры поспокойнее",
    "outdoors": "• играете на улице → будем предлагать игры для прогулки",
    "on_the_go": "• играете в дороге → игры без подготовки и лишних предметов",
}
RECENT_TEXT = {
    "worse": ("• последние игры зашли хуже → вернёмся к тому, что нравилось. Напишите, что не так: "
              "скучно, сложно или не по возрасту — подстроим точнее"),
    "better": "• последние игры заходят лучше — так и продолжим",
}


def reply_text(game_title, observations, titles=None, retracted=()):
    lines = [f"• поправили: убрали «{describe(obs)}»" for obs in retracted]
    for obs in observations:
        label = skills.LABELS.get(obs.get("skill"), "")
        other = (titles or {}).get(obs.get("game_id"))
        if other and obs["kind"] == "outcome" and obs["value"] == "liked":
            lines.append(f"• игра «{other}» понравилась — похожие будут чаще")
        elif other and obs["kind"] == "outcome" and obs["value"] == "disliked":
            lines.append(f"• игра «{other}» не зашла — такие будем предлагать реже")
        elif obs["kind"] == "setting" and obs["value"] in SETTING_TEXT:
            lines.append(SETTING_TEXT[obs["value"]])
        elif obs["kind"] == "recent" and obs["value"] in RECENT_TEXT:
            lines.append(RECENT_TEXT[obs["value"]])
        elif obs["kind"] == "difficulty":
            lines.append(f"• {label}: {DIFFICULTY_TEXT[obs['value']]}" if label else f"• {DIFFICULTY_TEXT[obs['value']]}")
        elif obs["kind"] == "outcome" and obs["value"] == "liked":
            lines.append("• игра понравилась — похожие будут чаще")
        elif obs["kind"] == "outcome" and obs["value"] == "disliked":
            lines.append("• игра не зашла — такие будем предлагать реже")
        elif obs["kind"] == "skill" and obs["value"] == "went_well":
            lines.append(f"• получается: {obs['evidence']}")
        elif obs["kind"] == "skill" and obs["value"] == "hard":
            lines.append(f"• пока трудно: {obs['evidence']} — вернёмся к этому мягче")
        elif obs["kind"] == "attention" and obs["value"] == "short":
            lines.append("• внимание пока трудно удержать → сделаем игры короче и живее")
        elif obs["kind"] == "attention" and obs["value"] == "tired":
            lines.append("• малыш был уставший или не в настроении — сложность из-за этого не меняем")
        elif obs["kind"] == "reason" and obs["value"] in REASON_TEXT:
            lines.append(REASON_TEXT[obs["value"]])
        elif obs["kind"] == "like":
            lines.append(f"• любит: {obs['value']}")
        elif obs["kind"] == "dislike":
            lines.append(f"• не любит: {obs['value']} — учтём")
        elif obs["kind"] == "wish":
            lines.append(f"• пожелание к сервису передали: {obs['value']}")
    unique = list(dict.fromkeys(lines))[:4]
    if not unique:
        return "Спасибо за отзыв! 🙏 Сохранили его к игре «" + game_title + "»."
    return "Спасибо за отзыв! Учли к следующим играм:\n" + "\n".join(unique)


def send_reply(con, group, text):
    """One reply per group, through the channel the feedback came from."""
    first = group[0]
    if first["channel"] == "telegram":
        _, chat_id, message_id = (first["external_event_id"] or "::").split(":", 2)
        token = os.environ.get("GAME_TELEGRAM_BOT_TOKEN")
        if not token or not chat_id:
            raise RuntimeError("no Telegram token or chat")
        payload = {"chat_id": chat_id, "text": text}
        replied = group[-1]["external_event_id"].rsplit(":", 1)[1]
        if replied.isdigit():  # a feedback restored by hand has no Telegram message to quote
            payload["reply_parameters"] = {"message_id": int(replied), "allow_sending_without_reply": True}
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                         data=json.dumps(payload).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            if not json.loads(response.read()).get("ok"):
                raise RuntimeError("Telegram rejected the reply")
        return
    if first["channel"] == "vk":
        row = con.execute("SELECT recipient FROM deliveries WHERE game_id = ? AND channel = 'vk' "
                          "AND status = 'sent' ORDER BY id LIMIT 1", (first["game_id"],)).fetchone()
        peer = (row and row["recipient"]) or os.environ.get("VK_PEER_ID")
        token = os.environ.get("VK_TOKEN")
        if not token or not peer:
            raise RuntimeError("no VK token or peer")
        random_id = int(hashlib.sha256(f"insights-{first['id']}".encode()).hexdigest()[:8], 16)
        data = urllib.parse.urlencode({"peer_id": peer, "message": text, "random_id": random_id,
                                       "access_token": token, "v": "5.199"}).encode()
        with urllib.request.urlopen(urllib.request.Request("https://api.vk.com/method/messages.send",
                                                           data=data, method="POST"), timeout=20) as response:
            body = json.loads(response.read())
        if "error" in body:
            raise RuntimeError(f"VK error {body['error'].get('error_code')}")
        return
    raise RuntimeError(f"unknown channel {first['channel']}")


def who(con, row):
    name = None
    if row.get("child_id") is not None:
        found = con.execute("SELECT name FROM children WHERE id = ?", (row["child_id"],)).fetchone()
        name = found and found["name"]
    return f"{row['channel']}" + (f", ребёнок {name}" if name else "")


def record_wish(con, source, text, evidence=None, child_id=None, chat_id=None, feedback_id=None, who=""):
    """Store a wish about the service and tell the owner (forwarded by the ops bot)."""
    con.execute(
        "INSERT INTO wishes(source, feedback_id, child_id, chat_id, text, evidence) VALUES (?, ?, ?, ?, ?, ?)",
        (source, feedback_id, child_id, chat_id, text, evidence),
    )
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'user_events'").fetchone():
        con.execute("INSERT INTO user_events(chat_id, kind, text) VALUES (?, 'wish', ?)",
                    (chat_id, f"💡 Пожелание ({who or source}): {text}"))


def mark(con, group, status, now):
    con.executemany("UPDATE feedback SET insights_at = ?, insights_status = ? WHERE id = ?",
                    [(now.isoformat(timespec="seconds"), status, r["id"]) for r in group])


def retry_replies(con, now, sender):
    """Resend the replies that failed, from the observations already stored.

    Status reply_failed is analysed feedback whose thank-you did not go out; it is retried
    every run while the feedback is fresh, then given up as done (no late thank-yous).
    """
    rows = [dict(r) for r in con.execute(
        "SELECT f.id, f.game_id, f.channel, f.external_event_id, f.received_at, g.title "
        "FROM feedback f JOIN games g ON g.id = f.game_id "
        "WHERE f.insights_status = 'reply_failed' ORDER BY f.game_id, f.received_at, f.id")]
    groups = {}
    for row in rows:
        groups.setdefault((row["game_id"], row["external_event_id"].rsplit(":", 1)[0]), []).append(row)
    for group in groups.values():
        ids = [(r["id"],) for r in group]
        if now - parse_time(group[-1]["received_at"]) > timedelta(hours=REPLY_WITHIN_HOURS):
            con.executemany("UPDATE feedback SET insights_status = 'done' WHERE id = ?", ids)
            con.commit()
            continue
        observations = [dict(r) for r in con.execute(
            "SELECT kind, value, skill FROM observations WHERE feedback_id = ?", (group[-1]["id"],))]
        observations += [{"kind": "wish", "value": r["text"], "skill": None} for r in con.execute(
            "SELECT text FROM wishes WHERE feedback_id = ?", (group[-1]["id"],))]
        try:
            sender(con, group, reply_text(group[0]["title"], observations))
        except Exception as exc:  # noqa: BLE001
            print(f"reply retry failed: {type(exc).__name__}", file=sys.stderr)
            continue
        con.executemany("UPDATE feedback SET insights_status = 'done', reply_sent_at = CURRENT_TIMESTAMP "
                        "WHERE id = ?", ids)
        con.commit()


def run(db_path=None, now=None, reply=True, model=run_model, sender=send_reply):
    now = now or datetime.now(timezone.utc)
    con = connect(db_path)
    done = 0
    try:
        for group in pending_groups(con, now):
            child_id = group[0]["child_id"]
            if analyses_today(con, now) >= GLOBAL_PER_DAY:
                print("global daily cap reached; the rest waits for tomorrow")
                break
            if child_id is not None and analyses_today(con, now, child_id) >= PER_CHILD_PER_DAY:
                print(f"child {child_id}: daily cap reached; waits for tomorrow")
                continue
            if all(reactions.is_reaction(r["raw_text"]) for r in group):
                # Stickers / photos only: no model; one request for words per game.
                nudge = reply and not reactions.already_nudged(con, group[0]["game_id"]) \
                    and now - group[-1]["at"] <= timedelta(hours=REPLY_WITHIN_HOURS)
                mark(con, group, "reaction", now)
                con.commit()
                if nudge:
                    try:
                        sender(con, group, reactions.NUDGE)
                        con.executemany("UPDATE feedback SET reply_sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                                        [(r["id"],) for r in group])
                        con.commit()
                    except Exception as exc:  # noqa: BLE001
                        print(f"nudge failed: {type(exc).__name__}", file=sys.stderr)
                continue
            extra = recent_context(con, group, now) if context_on(con, child_id) else None
            prompt, text = build_prompt(group, extra)
            games = {g["id"]: g["skill"] for g in extra["recent_games"]} if extra else None
            try:
                output = model(prompt, context=True) if extra else model(prompt)
                observations = valid_observations(output, text, group[0]["skill"], games)
            except Exception as exc:  # noqa: BLE001 - stays pending, retried next run
                print(f"feedback {group[0]['id']}: {str(exc)[:200]}", file=sys.stderr)
                continue
            retracted = []
            if extra:
                taken_back = set(valid_retractions(output, extra["previous_observations"]))
                retracted = [o for o in extra["previous_observations"] if o["id"] in taken_back]
                con.executemany("DELETE FROM observations WHERE id = ? AND child_id = ?",
                                [(o["id"], child_id) for o in retracted])
            titles = {g["id"]: g["title"] for g in extra["recent_games"]} if extra else None
            about_child = [o for o in observations if o["kind"] != "wish"]
            con.executemany(
                "INSERT INTO observations(feedback_id, child_id, game_id, kind, value, skill, evidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(group[-1]["id"], child_id, o.get("game_id") or group[0]["game_id"], o["kind"], o["value"],
                  o["skill"], o["evidence"])
                 for o in about_child],
            )
            for wish in (o for o in observations if o["kind"] == "wish"):
                record_wish(con, "feedback", wish["value"], wish["evidence"], child_id=child_id,
                            feedback_id=group[-1]["id"], who=who(con, group[0]))
            mark(con, group, "done", now)
            if child_id is not None:
                profile.rebuild(con, child_id, now)
            con.commit()
            done += 1
            print(f"feedback {[r['id'] for r in group]}: {len(observations)} observations")
            # Only fresh feedback gets a reply: a backlog (an import, a long outage) must not
            # wake a family up with thank-yous for messages from weeks ago.
            if reply and now - group[-1]["at"] <= timedelta(hours=REPLY_WITHIN_HOURS):
                try:
                    sender(con, group, reply_text(group[0]["title"], observations, titles, retracted))
                    con.executemany("UPDATE feedback SET reply_sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    [(r["id"],) for r in group])
                    con.commit()
                except Exception as exc:  # noqa: BLE001 - retried by the next runs, then dropped
                    mark(con, group, "reply_failed", now)
                    con.commit()
                    print(f"reply failed: {type(exc).__name__}", file=sys.stderr)
        if reply:
            retry_replies(con, now, sender)
    finally:
        con.close()
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(prog="feedback-insights")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--no-reply", action="store_true")
    args = parser.parse_args(argv)
    print(f"processed {run(reply=not args.no_reply)} feedback group(s)")


if __name__ == "__main__":
    main()
