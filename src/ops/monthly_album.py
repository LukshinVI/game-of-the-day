#!/usr/bin/env python3
"""Album of the month: one picture on the 1st with how the last month went.

What is on it, all from the database, no model call:
- the month and the child (name, age at the end of the month), on the season's picture;
- games received, games the family marked (any answer, tap or word), areas covered;
- up to three games the family liked, with their skill pictures; with no "liked" mark
  the block shows the last games of the month under a neutral heading instead;
- the areas of the month with how many games each;
- one short quote from the parent about a game that went well, when there is one.

No scores, norms, comparisons or development claims: facts and the parent's own words.
A child gets an album only after at least MIN_DAYS of games in that month. Test children
are skipped by the monthly run. Sent once per child, recipient and month (table albums).
The picture needs Pillow (python3-pil) and the bundled Nunito font (assets/fonts, OFL).
Off unless GAME_ALBUM=1.

    python3 src/ops/monthly_album.py run [--month YYYY-MM] [--dry-run]
    python3 src/ops/monthly_album.py preview --child ID [--month YYYY-MM] --out FILE.jpg
"""

import argparse
import calendar
import hashlib
import io
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.delivery import game_delivery, greetings  # noqa: E402
from src.feedback import month_theme  # noqa: E402
from src.preparation import skills  # noqa: E402

DB_PATH = ROOT / "data" / "game-of-the-day.db"
CARDS = ROOT / "assets" / "cards"
FONT = ROOT / "assets" / "fonts" / "Nunito.ttf"
MOSCOW = timezone(timedelta(hours=3))
MIN_DAYS = 14
FAVOURITES = 3
MAX_QUOTE = 90
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь",
          "Октябрь", "Ноябрь", "Декабрь"]
MONTH_EMOJI = {12: "❄️", 1: "❄️", 2: "❄️", 3: "🌷", 4: "🌷", 5: "🌷", 6: "☀️", 7: "☀️", 8: "☀️",
               9: "🍂", 10: "🍂", 11: "🍂"}
SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
          6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
GENITIVE = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
            "сентября", "октября", "ноября", "декабря"]
SKILL_COLOR = {"speech": (231, 170, 70), "fine_motor": (196, 150, 210), "coordination": (122, 168, 72),
               "sensory": (110, 160, 220), "cognitive": (240, 200, 60), "social_emotional": (232, 120, 140),
               "self_care": (90, 190, 170), "imagination": (150, 130, 220)}


def enabled():
    return os.environ.get("GAME_ALBUM") == "1"


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def previous_month(today):
    first = today.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def age_text(birth_iso, month):
    """The age on the last day of the month: "1 год и 2 месяца", "8 месяцев"."""
    year, mon = map(int, month.split("-"))
    end = date(year, mon, calendar.monthrange(year, mon)[1])
    months = greetings.months_between(date.fromisoformat(birth_iso), end)
    years, rest = divmod(months, 12)
    month_word = f"{rest} {greetings.plural(rest, 'месяц', 'месяца', 'месяцев')}"
    if years == 0:
        return month_word
    year_word = f"{years} {greetings.plural(years, 'год', 'года', 'лет')}"
    return year_word if rest == 0 else f"{year_word} и {month_word}"


def collect(con, child, month):
    """Everything the album shows, or None when the month had too few games."""
    games = [dict(r) for r in con.execute(
        "SELECT DISTINCT g.id, g.game_date, g.title, g.skill FROM games g "
        "JOIN deliveries d ON d.game_id = g.id AND d.status = 'sent' "
        "WHERE g.child_id = ? AND substr(g.game_date, 1, 7) = ? ORDER BY g.game_date, g.id",
        (child["id"], month))]
    if not games:
        return None
    first, last = date.fromisoformat(games[0]["game_date"]), date.fromisoformat(games[-1]["game_date"])
    if (last - first).days + 1 < MIN_DAYS:
        return None
    ids = [g["id"] for g in games]
    marks = ",".join("?" * len(ids))
    marked = con.execute(f"SELECT count(DISTINCT game_id) FROM feedback WHERE game_id IN ({marks})", ids).fetchone()[0]
    liked = {r[0]: r[1] for r in con.execute(
        f"SELECT game_id, count(*) FROM observations WHERE game_id IN ({marks}) "
        f"AND kind = 'outcome' AND value = 'liked' GROUP BY game_id", ids)}
    disliked = {r[0] for r in con.execute(
        f"SELECT game_id FROM observations WHERE game_id IN ({marks}) AND kind = 'outcome' AND value = 'disliked'", ids)}
    favourites = sorted((g for g in games if g["id"] in liked and g["id"] not in disliked),
                        key=lambda g: (-liked[g["id"]], g["game_date"]), reverse=False)[:FAVOURITES]
    favourites_liked = bool(favourites)
    if not favourites:
        favourites = games[-FAVOURITES:][::-1]
    counts = {}
    for g in games:
        if g["skill"]:
            counts[g["skill"]] = counts.get(g["skill"], 0) + 1
    areas = sorted(counts.items(), key=lambda kv: (-kv[1], list(skills.SKILLS).index(kv[0])
                                                    if kv[0] in skills.SKILLS else 99))
    return {
        "name": child["name"],
        "age": age_text(child["birth_date"], month),
        "month": month,
        "received": len(games),
        "marked": marked,
        "areas": areas,
        "favourites": [{"title": g["title"], "skill": g["skill"]} for g in favourites],
        "favourites_liked": favourites_liked,
        "quote": quote(con, ids),
        "theme": month_theme.current(con, child["id"], f"{month}-01"),
    }


def quote(con, game_ids):
    """One short thing the parent wrote about a game that went well, with its date."""
    marks = ",".join("?" * len(game_ids))
    rows = con.execute(
        f"SELECT o.evidence, f.received_at, o.kind, o.value FROM observations o "
        f"JOIN feedback f ON f.id = o.feedback_id WHERE o.game_id IN ({marks}) "
        f"AND ((o.kind = 'skill' AND o.value = 'went_well') OR (o.kind = 'outcome' AND o.value = 'liked')) "
        f"AND f.raw_text NOT LIKE '[кнопка]%' ORDER BY (o.kind = 'skill') DESC, f.received_at DESC",
        game_ids).fetchall()
    for evidence, received_at, _, _ in rows:
        text = " ".join((evidence or "").split()).strip(" .,!")
        if 8 <= len(text) <= MAX_QUOTE:
            when = date.fromisoformat(str(received_at)[:10])
            return {"text": text[0].upper() + text[1:], "date": f"{when.day} {GENITIVE[when.month - 1]}"}
    return None


def caption(data):
    year, mon = map(int, data["month"].split("-"))
    line = f"{MONTH_EMOJI[mon]} Альбом месяца: {MONTHS[mon - 1].lower()} — {data['name']}, {data['age']}."
    return line + "\nСпасибо, что играете вместе! Картинку можно переслать родным 💛"


# --- picture -------------------------------------------------------------------------

W, H = 1080, 1440
CREAM, INK, MUTED, ACCENT, WHITE = (251, 246, 238), (59, 47, 37), (138, 122, 104), (124, 154, 62), (255, 255, 255)


def _font(size, weight="Bold"):
    from PIL import ImageFont
    font = ImageFont.truetype(str(FONT), size)
    try:
        font.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return font


def _cover(image, width, height):
    ratio = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * ratio), round(image.height * ratio)))
    left, top = (resized.width - width) // 2, int((resized.height - height) * 0.4)
    return resized.crop((left, top, left + width, top + height))


def _wrap(draw, text, font, width, lines):
    words, out, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            out.append(current)
            current = word
    out.append(current)
    if len(out) > lines:
        out = out[:lines]
        while out[-1] and draw.textlength(out[-1] + "…", font=font) > width:
            out[-1] = out[-1][:-1]
        out[-1] = out[-1].rstrip() + "…"
    return [line for line in out if line]


def render(data):
    """The album as JPEG bytes."""
    from PIL import Image, ImageDraw

    year, mon = map(int, data["month"].split("-"))
    canvas = Image.new("RGB", (W, H), CREAM)
    draw = ImageDraw.Draw(canvas)

    hero_h = 360
    hero = _cover(Image.open(CARDS / f"season_{SEASON[mon]}.jpg").convert("RGB"), W, hero_h)
    shade = Image.new("L", (1, hero_h))
    for y in range(hero_h):
        shade.putpixel((0, y), int(165 * max(0, (y - hero_h * 0.35) / (hero_h * 0.65))))
    dark = Image.new("RGB", (W, hero_h), (30, 20, 10))
    hero.paste(dark, (0, 0), shade.resize((W, hero_h)))
    canvas.paste(hero, (0, 0))
    title, sub = _font(96, "Black"), _font(40, "Bold")
    draw.text((64, hero_h - 168), MONTHS[mon - 1], font=title, fill=WHITE)
    draw.text((66, hero_h - 62), f"{data['name']} · {data['age']}", font=sub, fill=(255, 243, 224))
    brand = _font(30, "ExtraBold")
    draw.rounded_rectangle((48, 36, 72 + draw.textlength("«Игра дня»", font=brand), 84), 24, fill=(255, 255, 255))
    draw.text((60, 40), "«Игра дня»", font=brand, fill=ACCENT)

    y = hero_h + 36
    stats = [(data["received"], greetings.plural(data["received"], "игра пришла", "игры пришли", "игр пришло")),
             (data["marked"], "отметили вы"),
             (len(data["areas"]), greetings.plural(len(data["areas"]), "направление", "направления", "направлений"))]
    stats = [s for s in stats if s[0]]  # never a reproachful zero
    gap = 24
    box_w = (W - 128 - (len(stats) - 1) * gap) // len(stats)
    big, small = _font(64, "Black"), _font(28, "Bold")
    for i, (value, label) in enumerate(stats):
        x = 64 + i * (box_w + gap)
        draw.rounded_rectangle((x, y, x + box_w, y + 120), 22, fill=WHITE)
        text = str(value)
        draw.text((x + (box_w - draw.textlength(text, font=big)) / 2, y + 4), text, font=big, fill=ACCENT)
        draw.text((x + (box_w - draw.textlength(label, font=small)) / 2, y + 78), label, font=small, fill=MUTED)
    y += 120 + 36

    label_font = _font(28, "ExtraBold")
    heading = "БОЛЬШЕ ВСЕГО ПОНРАВИЛОСЬ" if data["favourites_liked"] else "ИГРЫ МЕСЯЦА"
    draw.text((64, y), heading, font=label_font, fill=MUTED)
    y += 50
    tile = (W - 128 - 2 * 24) // 3
    cap_font = _font(27, "Bold")
    for i, game in enumerate(data["favourites"]):
        x = 64 + i * (tile + 24)
        draw.rounded_rectangle((x, y, x + tile, y + tile + 96), 22, fill=WHITE)
        path = CARDS / f"skill_{game['skill']}.jpg"
        if path.is_file():
            picture = _cover(Image.open(path).convert("RGB"), tile, tile)
            mask = Image.new("L", (tile, tile), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile, tile + 30), 22, fill=255)
            canvas.paste(picture, (x, y), mask)
        for j, line in enumerate(_wrap(draw, game["title"], cap_font, tile - 28, 2)):
            draw.text((x + 14, y + tile + 12 + j * 34), line, font=cap_font, fill=INK)
    y += tile + 96 + 30

    if data["quote"]:
        quote_font, when_font = _font(32, "SemiBold"), _font(25, "Bold")
        lines = _wrap(draw, f"«{data['quote']['text']}»", quote_font, W - 128 - 56, 2)
        height = 30 + len(lines) * 42 + 40
        draw.rounded_rectangle((64, y, W - 64, y + height), 22, fill=WHITE)
        for j, line in enumerate(lines):
            draw.text((92, y + 20 + j * 42), line, font=quote_font, fill=INK)
        draw.text((92, y + 24 + len(lines) * 42), f"вы писали {data['quote']['date']}", font=when_font, fill=MUTED)
        y += height + 26

    chip_font = _font(25, "Bold")
    x = 64
    for skill, count in data["areas"]:
        text = f"{skills.LABELS.get(skill, skill)} · {count}"
        width = draw.textlength(text, font=chip_font) + 64
        if x + width > W - 64:
            x, y = 64, y + 52
        draw.rounded_rectangle((x, y, x + width, y + 42), 21, fill=WHITE)
        draw.ellipse((x + 16, y + 13, x + 32, y + 29), fill=SKILL_COLOR.get(skill, ACCENT))
        draw.text((x + 42, y + 5), text, font=chip_font, fill=INK)
        x += width + 12

    # The height follows the content: no empty band when a month has no quote or few areas.
    canvas = canvas.crop((0, 0, W, min(H, y + 42 + 64)))

    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()


# --- sending -------------------------------------------------------------------------

def _multipart(fields, file_field, filename, payload):
    boundary = uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
             "Content-Type: image/jpeg\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _post(url, body, content_type):
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def send_telegram(chat_id, picture, text):
    token = os.environ.get("GAME_TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("GAME_TELEGRAM_BOT_TOKEN is missing")
    body, kind = _multipart({"chat_id": chat_id, "caption": text}, "photo", "album.jpg", picture)
    data = _post(f"https://api.telegram.org/bot{token}/sendPhoto", body, kind)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data.get('description')}")


def send_vk(peer_id, picture, text, key):
    from src.delivery import cards
    token = os.environ.get("VK_TOKEN")
    server = cards._vk("photos.getMessagesUploadServer", token, peer_id=peer_id)
    body, kind = _multipart({}, "photo", "album.jpg", picture)
    uploaded = _post(server["upload_url"], body, kind)
    saved = cards._vk("photos.saveMessagesPhoto", token, photo=uploaded["photo"],
                      server=uploaded["server"], hash=uploaded["hash"])[0]
    access = f"_{saved['access_key']}" if saved.get("access_key") else ""
    random_id = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    game_delivery.vk_send(text, random_id, peer_id=peer_id, attachment=f"photo{saved['owner_id']}_{saved['id']}{access}")


def send(channel, address, picture, text, key):
    if channel == "telegram":
        return send_telegram(address, picture, text)
    if channel == "vk":
        return send_vk(address, picture, text, key)
    raise RuntimeError(f"unknown channel {channel}")


def run(today=None, month=None, dry_run=False, db_path=None, sender=send, render_fn=None):
    if not enabled() and not dry_run:
        print("album is off (GAME_ALBUM)")
        return 0
    today = today or datetime.now(MOSCOW).date()
    month = month or previous_month(today)
    render_fn = render_fn or render
    con = connect(db_path)
    sent = 0
    try:
        children = con.execute(
            "SELECT id, name, birth_date FROM children WHERE active = 1 AND is_test = 0 ORDER BY id").fetchall()
        for child in children:
            data = collect(con, child, month)
            if data is None:
                continue
            recipients = con.execute(
                "SELECT channel, address FROM recipients WHERE child_id = ? AND active = 1", (child["id"],)).fetchall()
            pending = [r for r in recipients if not con.execute(
                "SELECT 1 FROM albums WHERE child_id = ? AND channel = ? AND address = ? AND month = ?",
                (child["id"], r["channel"], r["address"], month)).fetchone()]
            if not pending:
                continue
            picture, text = render_fn(data), caption(data)
            for r in pending:
                if dry_run:
                    print(f"--- {child['name']} → {r['channel']}: {len(picture)} bytes\n{text}\n")
                    continue
                try:
                    sender(r["channel"], r["address"], picture, text, f"album-{month}-{child['id']}-{r['address']}")
                except Exception as exc:  # noqa: BLE001 - one recipient failing must not stop the others
                    print(f"album to {r['channel']} failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
                    continue
                con.execute("INSERT INTO albums(child_id, channel, address, month) VALUES (?, ?, ?, ?)",
                            (child["id"], r["channel"], r["address"], month))
                con.commit()
                sent += 1
    finally:
        con.close()
    print(f"albums sent: {sent}")
    return sent


def preview(child_id, month, out, db_path=None, min_days=None):
    """Render one child's album to a file, whatever the flag (for the owner to look at)."""
    global MIN_DAYS
    con = connect(db_path)
    try:
        child = con.execute("SELECT id, name, birth_date FROM children WHERE id = ?", (child_id,)).fetchone()
        saved, MIN_DAYS = MIN_DAYS, (MIN_DAYS if min_days is None else min_days)
        try:
            data = collect(con, child, month)
        finally:
            MIN_DAYS = saved
    finally:
        con.close()
    if data is None:
        print("no album: too few games that month")
        return None
    Path(out).write_bytes(render(data))
    print(caption(data))
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(prog="monthly-album")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--month")
    r.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("preview")
    p.add_argument("--child", type=int, required=True)
    p.add_argument("--month", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--min-days", type=int)
    args = parser.parse_args(argv)
    if args.command == "run":
        run(month=args.month, dry_run=args.dry_run)
    else:
        preview(args.child, args.month, args.out, min_days=args.min_days)


if __name__ == "__main__":
    main()
