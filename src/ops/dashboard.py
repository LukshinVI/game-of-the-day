#!/usr/bin/env python3
"""Owner dashboard: one self-contained HTML page (inline SVG, no scripts, no network).

The ops bot sends it as a document on /dash, so nothing is published anywhere: the file
exists only in the owner's chat. It holds children's names, exactly like /users.
Read-only. Standard library only.

    python3 src/ops/dashboard.py > dash.html
"""

import html
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ops import slo  # noqa: E402

DB_PATH = ROOT / "data" / "game-of-the-day.db"
MOSCOW = ZoneInfo("Europe/Moscow")
DAYS = 28
ORIGIN_LABELS = {"model": "модель", "fallback_model": "резервная модель", "library": "библиотека",
                 "reserve": "запас", "swap": "замена по просьбе"}


def _real(alias="g"):
    return f"{alias}.child_id NOT IN (SELECT id FROM children WHERE is_test = 1)"


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def collect(con, today, now=None):
    start = today - timedelta(days=DAYS - 1)
    days = [(start + timedelta(days=i)).isoformat() for i in range(DAYS)]
    span = (days[0], days[-1])

    def per_day(sql, params=span):
        counts = dict(con.execute(sql, params).fetchall())
        return [counts.get(d, 0) for d in days]

    sent = per_day(f"""SELECT g.game_date, count(DISTINCT g.id) FROM games g JOIN deliveries d ON d.game_id = g.id
                       WHERE d.status = 'sent' AND g.game_date BETWEEN ? AND ? AND {_real()} GROUP BY 1""")
    feedback = per_day(f"""SELECT g.game_date, count(*) FROM feedback f JOIN games g ON g.id = f.game_id
                           WHERE g.game_date BETWEEN ? AND ? AND {_real()} GROUP BY 1""")
    cost = [0.0] * DAYS
    rejected = [0] * DAYS
    if _has(con, "model_runs"):
        index = {d: i for i, d in enumerate(days)}
        for day, usd, rej in con.execute(
                "SELECT target_date, COALESCE(sum(cost_usd), 0), COALESCE(sum(outcome = 'rejected' AND model LIKE 'review:%'), 0) "
                "FROM model_runs WHERE dry_run = 0 AND target_date BETWEEN ? AND ? GROUP BY 1", span):
            if day in index:
                cost[index[day]], rejected[index[day]] = round(usd, 2), rej
    origins = dict(con.execute(
        f"SELECT COALESCE(origin, 'model'), count(*) FROM games g WHERE g.game_date BETWEEN ? AND ? AND {_real()} GROUP BY 1",
        span).fetchall())

    children = []
    for row in con.execute(
            """SELECT c.id, c.name, c.birth_date,
                      (SELECT group_concat(r.channel || CASE WHEN r.active THEN '' ELSE ' (пауза)' END, ', ')
                         FROM recipients r WHERE r.child_id = c.id) AS channels,
                      (SELECT g.title FROM games g WHERE g.child_id = c.id ORDER BY g.game_date DESC, g.id DESC LIMIT 1) AS last_game,
                      (SELECT count(*) FROM feedback f JOIN games g ON g.id = f.game_id
                        WHERE g.child_id = c.id AND g.game_date >= ?) AS feedback_7d
               FROM children c WHERE c.is_test = 0 AND c.active = 1 ORDER BY c.id""",
            ((today - timedelta(days=6)).isoformat(),)):
        birth = date.fromisoformat(row[2])
        months = (today.year - birth.year) * 12 + today.month - birth.month - (today.day < birth.day)
        children.append({"name": row[1], "age": months, "channels": row[3] or "—",
                         "last_game": row[4] or "—", "feedback_7d": row[5]})

    slo_counts = slo.collect(con, days[0], days[-1], now=now)
    families = con.execute(
        "SELECT count(DISTINCT r.channel || ':' || r.address) FROM recipients r JOIN children c ON c.id = r.child_id "
        "WHERE r.active = 1 AND c.active = 1 AND c.is_test = 0").fetchone()[0]
    return {"today": today.isoformat(), "days": days, "sent": sent, "feedback": feedback, "cost": cost,
            "rejected": rejected, "origins": origins, "children": children, "slo": slo_counts,
            "families": families}


# --- rendering -------------------------------------------------------------------

CSS = """
.viz-root { color-scheme: light;
  --surface-0:#f4f4f2; --surface-1:#fcfcfb; --border:#e4e3df; --grid:#ecebe7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#7a7974;
  --series-1:#2a78d6; --series-1-fill:rgba(42,120,214,.12); --series-2:#eb6834; --series-3:#1baf7a;
  --good:#0ca30c; --critical:#d03b3b; --good-text:#006300; --critical-text:#b3261e; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root { color-scheme: dark;
    --surface-0:#111110; --surface-1:#1a1a19; --border:#2e2e2b; --grid:#262624;
    --text-primary:#f0efec; --text-secondary:#c3c2b7; --text-muted:#8f8e86;
    --series-1:#3987e5; --series-1-fill:rgba(57,135,229,.18); --series-2:#d95926; --series-3:#199e70;
    --good-text:#5fd35f; --critical-text:#f08080; } }
* { box-sizing:border-box; }
html, body { margin:0; background:var(--surface-0); }
body { padding:20px 16px 32px; color:var(--text-primary);
  font:14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width:880px; margin:0 auto; }
header { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
h1 { font-size:20px; font-weight:650; letter-spacing:-.01em; margin:0; }
.meta { color:var(--text-muted); font-size:13px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:1px;
  background:var(--border); border:1px solid var(--border); border-radius:12px; overflow:hidden; margin-bottom:16px; }
.tile { background:var(--surface-1); padding:14px 16px; }
.tile .label { color:var(--text-secondary); font-size:12px; }
.tile .value { font-size:26px; font-weight:650; letter-spacing:-.02em; font-variant-numeric:tabular-nums; margin-top:2px; }
.tile .delta { color:var(--text-muted); font-size:12px; font-variant-numeric:tabular-nums; }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:16px; margin-bottom:16px; }
.panel { background:var(--surface-1); border:1px solid var(--border); border-radius:12px; padding:14px 16px 10px; }
.panel h2 { font-size:13px; font-weight:600; margin:0; }
.panel .sub { color:var(--text-muted); font-size:12px; margin:2px 0 6px; }
svg { width:100%; height:auto; display:block; overflow:visible; }
svg text { font-size:11px; fill:var(--text-muted); font-variant-numeric:tabular-nums; }
.section { margin:22px 0 8px; font-size:13px; font-weight:600; color:var(--text-secondary); text-transform:uppercase; letter-spacing:.04em; }
table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
th { text-align:left; font-weight:500; color:var(--text-muted); padding:8px 6px; border-bottom:1px solid var(--border); }
td { padding:9px 6px; border-bottom:1px solid var(--grid); vertical-align:top; }
tr:last-child td { border-bottom:0; }
td.num { text-align:right; } th.num { text-align:right; }
.pill { display:inline-flex; align-items:center; gap:5px; font-size:12px; font-weight:600; }
.pill.ok { color:var(--good-text); } .pill.bad { color:var(--critical-text); }
.pill i { width:8px; height:8px; border-radius:50%; display:inline-block; }
.pill.ok i { background:var(--good); } .pill.bad i { background:var(--critical); }
.stack { display:flex; height:10px; border-radius:5px; overflow:hidden; gap:2px; margin:6px 0 10px; }
.legend { display:flex; flex-wrap:wrap; gap:4px 14px; font-size:12px; color:var(--text-secondary); }
.legend i { width:10px; height:10px; border-radius:3px; display:inline-block; margin-right:5px; vertical-align:-1px; }
.muted { color:var(--text-muted); }
footer { color:var(--text-muted); font-size:12px; margin-top:20px; }
"""

SERIES = ("--series-1", "--series-2", "--series-3")


def _ticks(top):
    """A rounded top value and the three gridlines below it."""
    if top <= 0:
        return 1, [0, 1]
    magnitude = 10 ** (len(str(int(top))) - 1) if top >= 1 else 0.1
    for step in (1, 2, 2.5, 5, 10):
        nice = step * magnitude
        if nice * 4 >= top:
            return nice * 4, [nice * i for i in range(5)]
    return top, [0, top]


def _fmt(v, unit):
    if unit == "$":
        return f"${v:.2f}" if v < 10 else f"${v:.0f}"
    return f"{v:g}"


def line_chart(values, days, unit=""):
    """Area + 2px line, recessive grid, per-day hover targets (SVG <title>)."""
    width, height, left, right, top_pad, bottom = 560, 150, 34, 8, 8, 22
    top, ticks = _ticks(max(values))
    inner_w, inner_h = width - left - right, height - top_pad - bottom
    step = inner_w / (len(values) - 1)

    def xy(i, v):
        return left + i * step, top_pad + inner_h * (1 - v / top)

    out = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for t in ticks:
        y = top_pad + inner_h * (1 - t / top)
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" style="stroke:var(--grid)"/>')
        out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(t, unit)}</text>')
    points = [xy(i, v) for i, v in enumerate(values)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    base = top_pad + inner_h
    out.append(f'<polygon points="{left},{base} {path} {points[-1][0]:.1f},{base}" style="fill:var(--series-1-fill)"/>')
    out.append(f'<polyline points="{path}" fill="none" style="stroke:var(--series-1)" stroke-width="2" '
               'stroke-linejoin="round" stroke-linecap="round"/>')
    x, y = points[-1]
    out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" style="fill:var(--series-1);stroke:var(--surface-1)" stroke-width="2"/>')
    for i, (px, _) in enumerate(points):
        out.append(f'<rect x="{px - step / 2:.1f}" y="{top_pad}" width="{step:.1f}" height="{inner_h}" fill="transparent">'
                   f'<title>{days[i][8:]}.{days[i][5:7]}: {_fmt(values[i], unit)}</title></rect>')
    for i in (0, len(days) // 2, len(days) - 1):
        anchor = "start" if i == 0 else ("end" if i == len(days) - 1 else "middle")
        out.append(f'<text x="{points[i][0]:.1f}" y="{height - 4}" text-anchor="{anchor}">{days[i][8:]}.{days[i][5:7]}</text>')
    out.append("</svg>")
    return "".join(out)


def bar_chart(values, days, unit=""):
    """Thin bars anchored to the baseline, 2px gaps, hover titles."""
    width, height, left, right, top_pad, bottom = 560, 150, 34, 8, 8, 22
    top, ticks = _ticks(max(values))
    inner_w, inner_h = width - left - right, height - top_pad - bottom
    slot = inner_w / len(values)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for t in ticks:
        y = top_pad + inner_h * (1 - t / top)
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" style="stroke:var(--grid)"/>')
        out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(t, unit)}</text>')
    base = top_pad + inner_h
    for i, v in enumerate(values):
        h = inner_h * v / top
        x = left + i * slot
        if h > 0:
            r = min(4, h / 2, (slot - 2) / 2)
            out.append(f'<path d="M{x + 1:.1f},{base} V{base - h + r:.1f} Q{x + 1:.1f},{base - h:.1f} {x + 1 + r:.1f},{base - h:.1f} '
                       f'H{x + slot - 1 - r:.1f} Q{x + slot - 1:.1f},{base - h:.1f} {x + slot - 1:.1f},{base - h + r:.1f} V{base} Z" '
                       'style="fill:var(--series-1)"/>')
        out.append(f'<rect x="{x:.1f}" y="{top_pad}" width="{slot:.1f}" height="{inner_h}" fill="transparent">'
                   f'<title>{days[i][8:]}.{days[i][5:7]}: {_fmt(v, unit)}</title></rect>')
    for i in (0, len(days) // 2, len(days) - 1):
        cx = left + i * slot + slot / 2
        anchor = "start" if i == 0 else ("end" if i == len(days) - 1 else "middle")
        out.append(f'<text x="{cx:.1f}" y="{height - 4}" text-anchor="{anchor}">{days[i][8:]}.{days[i][5:7]}</text>')
    out.append("</svg>")
    return "".join(out)


def _delta(now, before, unit=""):
    if before == 0 and now == 0:
        return "как неделей ранее"
    diff = now - before
    sign = "+" if diff > 0 else ("−" if diff < 0 else "±")
    shown = f"${abs(diff):.2f}" if unit == "$" else f"{abs(diff):g}"
    return f"{sign}{shown} к прошлой неделе"


def render(data):
    e = html.escape
    week, prev = slice(-7, None), slice(-14, -7)
    sent7, sent_prev = sum(data["sent"][week]), sum(data["sent"][prev])
    fb7, fb_prev = sum(data["feedback"][week]), sum(data["feedback"][prev])
    cost7, cost_prev = round(sum(data["cost"][week]), 2), round(sum(data["cost"][prev]), 2)
    on_good, on_total = data["slo"]["on_time"]
    tiles = [
        ("Дети", str(len(data["children"])), f"{data['families']} получателей"),
        ("Игр за 7 дней", str(sent7), _delta(sent7, sent_prev)),
        ("Отзывов за 7 дней", str(fb7), _delta(fb7, fb_prev)),
        ("Вовремя, 28 дней", f"{on_good * 100 // on_total}%" if on_total else "—", f"{on_good} из {on_total} дней"),
        ("Квота за 7 дней", f"${cost7:.2f}", _delta(cost7, cost_prev, "$")),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="label">{e(l)}</div><div class="value">{e(v)}</div>'
        f'<div class="delta">{e(d)}</div></div>' for l, v, d in tiles)

    slo_rows = ""
    for name, (title, target) in slo.OBJECTIVES.items():
        good, total = data["slo"][name]
        if not total:
            status = '<span class="muted">нет данных</span>'
            value = "—"
        else:
            share = good / total
            ok = share >= target
            status = (f'<span class="pill {"ok" if ok else "bad"}"><i></i>{"в норме" if ok else "ниже цели"}</span>')
            value = f"{share:.0%}"
        slo_rows += (f"<tr><td>{e(title)}</td><td class='num'>{value}</td><td class='num muted'>{good}/{total}</td>"
                     f"<td class='num muted'>{target:.0%}</td><td>{status}</td></tr>")

    origins = sorted(data["origins"].items(), key=lambda kv: -kv[1])
    total_games = sum(v for _, v in origins) or 1
    colors = {k: SERIES[i] if i < len(SERIES) else "--text-muted" for i, (k, _) in enumerate(origins)}
    stack = "".join(f'<div style="flex:{v};background:var({colors[k]})" title="{e(ORIGIN_LABELS.get(k, k))}: {v}"></div>'
                    for k, v in origins)
    legend = "".join(f'<span><i style="background:var({colors[k]})"></i>{e(ORIGIN_LABELS.get(k, k))} — {v} '
                     f'({v * 100 // total_games}%)</span>' for k, v in origins)

    kids = "".join(
        f"<tr><td>{e(c['name'])}</td><td class='num'>{c['age']} мес</td><td>{e(c['channels'])}</td>"
        f"<td>{e(c['last_game'])}</td><td class='num'>{c['feedback_7d']}</td></tr>" for c in data["children"])

    def panel(title, sub, chart):
        return f'<div class="panel"><h2>{e(title)}</h2><div class="sub">{e(sub)}</div>{chart}</div>'

    days = data["days"]
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Игра дня — дашборд</title><style>{CSS}</style></head>
<body class="viz-root"><div class="wrap">
<header><h1>Игра дня</h1><div class="meta">на {e(data['today'][8:])}.{e(data['today'][5:7])}.{e(data['today'][:4])} · окно {DAYS} дней</div></header>
<div class="tiles">{tiles_html}</div>
<div class="grid2">
{panel("Доставлено игр", "игр в день, без тестовых детей", line_chart(data["sent"], days))}
{panel("Отзывы", "отзывов в день", line_chart(data["feedback"], days))}
{panel("Расход модели", "в день, эквивалент цены API", bar_chart(data["cost"], days, "$"))}
{panel("Отклонено проверкой", "кандидатов в день", bar_chart(data["rejected"], days))}
</div>
<div class="section">Надёжность</div>
<div class="panel"><table><tr><th>цель</th><th class="num">факт</th><th class="num">события</th><th class="num">норма</th><th>статус</th></tr>{slo_rows}</table></div>
<div class="section">Откуда игры</div>
<div class="panel"><div class="stack">{stack}</div><div class="legend">{legend}</div></div>
<div class="section">Дети</div>
<div class="panel"><table><tr><th>имя</th><th class="num">возраст</th><th>каналы</th><th>последняя игра</th><th class="num">отзывов 7д</th></tr>{kids}</table></div>
<footer>Доллары — эквивалент цены API; на подписке это расход квоты, а не деньги. Наведите или нажмите на график, чтобы увидеть значение за день.</footer>
</div></body></html>"""


def build(db_path=None, today=None, now=None):
    today = today or datetime.now(MOSCOW).date()
    con = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True, timeout=10)
    try:
        data = collect(con, today, now=now or datetime.now(timezone.utc))
    finally:
        con.close()
    return render(data), data


def summary(data):
    on_good, on_total = data["slo"]["on_time"]
    return (f"📊 Дашборд на {data['today']}: детей {len(data['children'])}, игр за 7 дней {sum(data['sent'][-7:])}, "
            f"отзывов {sum(data['feedback'][-7:])}, вовремя за 28 дней {on_good}/{on_total}. Откройте файл.")


if __name__ == "__main__":
    print(build()[0])
