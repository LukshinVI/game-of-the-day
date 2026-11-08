"""Mechanic tags: what the child actually does, so "the same idea under a new name" is
caught, not only an identical title.

The model sets one tag per activity; the gate rejects a tag used in the child's last
WINDOW games; the library choice applies the same rule. Older games without a tag are
backfilled once (`python3 src/preparation/mechanics.py backfill`).
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
WINDOW = 7

TAGS = {
    "hide_and_find": "прятать и находить (ку-ку, под платком)",
    "stacking_building": "строить, ставить друг на друга",
    "ball_throw_roll": "бросать, катать, ловить мяч",
    "push_pull": "толкать, тянуть, катать машинку или каталку",
    "container_in_out": "класть внутрь и доставать, вкладыши, крышки",
    "pour_transfer": "пересыпать, переливать, перекладывать",
    "sort_match": "сортировать, подбирать пары, по цвету или форме",
    "cause_effect": "нажать, дёрнуть — получить отклик",
    "sound_imitation": "звукоподражание, повторять звуки",
    "turn_taking_talk": "разговор по очереди, гуление",
    "naming_pointing": "называть, показывать, «где…?»",
    "songs_rhymes_gestures": "потешки, песенки, ладушки, пальчиковые игры",
    "book_pictures": "книжка, картинки",
    "pretend_care": "сюжетная игра: кормить, укладывать, лечить игрушку",
    "role_play_dress_up": "ролевая игра, переодевание",
    "crawl_course": "ползать, пролезать, полоса препятствий",
    "walk_balance": "ходить, балансировать, идти по линии",
    "jump_dance_move": "прыгать, танцевать, двигаться под музыку",
    "tummy_time": "на животе, опора на руки",
    "touch_textures": "трогать разные материалы",
    "water_play": "игры с водой",
    "art_making": "рисовать, лепить, клеить",
    "self_care_routine": "сам: пить, мыть руки, одеваться, убирать",
    "faces_emotions": "лица, зеркало, эмоции",
}


def recent(history, window=WINDOW):
    return [g.get("mechanic_tag") for g in history[-window:] if g.get("mechanic_tag")]


def violations(tag, history):
    if tag is None:
        return []  # older candidates and fixtures without a tag; the model schema requires one
    if tag not in TAGS:
        return [f"invalid mechanic_tag: {tag}"]
    if tag in recent(history):
        return [f"mechanic {tag} ({TAGS[tag]}) was used in the last {WINDOW} games: choose a different kind of activity"]
    return []


BACKFILL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tags"],
    "properties": {"tags": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "tag"],
        "properties": {"id": {"type": "integer"}, "tag": {"type": "string", "enum": list(TAGS)}}}}},
}


def backfill(db_path=DB_PATH, model="haiku"):
    """Tag games and reserve games that have no tag, in one model call per table."""
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        for table in ("games", "reserve_games"):
            rows = con.execute(f"SELECT id, title, mechanics FROM {table} WHERE mechanic_tag IS NULL").fetchall()
            if not rows:
                continue
            items = [{"id": r["id"], "title": r["title"], "mechanics": (r["mechanics"] or "")[:300]} for r in rows]
            prompt = ("Assign one mechanic tag to each children's activity: what the child mainly does.\n"
                      f"TAGS: {json.dumps(TAGS, ensure_ascii=False)}\nACTIVITIES: {json.dumps(items, ensure_ascii=False)}")
            command = [os.environ.get("CLAUDE_BIN", "claude"), "--print", "--output-format", "json",
                       "--json-schema", json.dumps(BACKFILL_SCHEMA), "--model", model, "--tools", "",
                       "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
            result = subprocess.run(command, input=prompt, text=True, capture_output=True, cwd=ROOT, timeout=600)
            tags = (json.loads(result.stdout or "{}").get("structured_output") or {}).get("tags", [])
            known = {r["id"] for r in rows}
            con.executemany(f"UPDATE {table} SET mechanic_tag = ? WHERE id = ? AND mechanic_tag IS NULL",
                            [(t["tag"], t["id"]) for t in tags if t["id"] in known and t["tag"] in TAGS])
            con.commit()
            print(f"{table}: tagged {len(tags)} of {len(rows)}")
    finally:
        con.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["backfill"]:
        backfill()
    else:
        sys.exit("usage: mechanics.py backfill")
