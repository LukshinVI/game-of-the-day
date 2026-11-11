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
:root { --bg:#fff8f1; --card:#ffffff; --ink:#4a3832; --soft:#8a7369; --line:#efe2d8;
        --a:#f0806c; --b:#7ec4aa; --c:#78aadc; --d:#ffc454; --bad:#d9534f; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1f1a18; --card:#2a2320; --ink:#f3e9e2; --soft:#b9a69c; --line:#3a312c; } }
* { box-sizing: border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--ink);
       font:15px/1.4 -apple-system, "Segoe UI", Roboto, sans-serif; }
h1 { font-size:22px; margin:0 0 4px; } .sub { color:var(--soft); margin:0 0 16px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin-bottom:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }
.kpi b { display:block; font-size:26px; } .kpi span { color:var(--soft); font-size:13px; }
h2 { font-size:16px; margin:18px 0 8px; }
svg { width:100%; height:auto; display:block; }
table { width:100%; border-collapse:collapse; font-size:14px; }
td, th { text-align:left; padding:6px 4px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--soft); font-weight:600; }
.ok { color:#2e9d6f; } .bad { color:var(--bad); }
.legend { color:var(--soft); font-size:12px; }
"""


def bars(values, color, days, unit=""):
    """A small bar chart, 28 bars, with the max and the date range labelled."""
    width, height, pad = 560, 120, 18
    top = max(values) or 1
    step = (width - 2 * pad) / len(values)
    parts = [f'<svg viewBox="0 0 {width} {height + 28}" role="img">']
    for i, v in enumerate(values):
        h = (height - 10) * v / top
        x = pad + i * step
        parts.append(f'<rect x="{x + 1:.1f}" y="{height - h:.1f}" width="{step - 2:.1f}" height="{h:.1f}" '
                     f'rx="2" style="fill:var({color})"><title>{days[i]}: {v}{unit}</title></rect>')
    parts.append(f'<line x1="{pad}" y1="{height}" x2="{width - pad}" y2="{height}" style="stroke:var(--line)"/>')
    parts.append(f'<text x="{pad}" y="{height + 18}" font-size="12" style="fill:var(--soft)">{days[0][5:]}</text>')
    parts.append(f'<text x="{width - pad}" y="{height + 18}" font-size="12" style="fill:var(--soft)" '
                 f'text-anchor="end">{days[-1][5:]}</text>')
    parts.append(f'<text x="{pad}" y="12" font-size="12" style="fill:var(--soft)">макс {top}{unit}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render(data):
    e = html.escape
    sent7 = sum(data["sent"][-7:])
    fb7 = sum(data["feedback"][-7:])
    cost7 = sum(data["cost"][-7:])
    on_good, on_total = data["slo"]["on_time"]
    on_time = f"{on_good * 100 // on_total}%" if on_total else "—"
    kpis = [(data["families"], "получателей"), (len(data["children"]), "детей"),
            (sent7, "игр за 7 дней"), (fb7, "отзывов за 7 дней"),
            (on_time, "вовремя, 28 дней"), (f"${cost7:.2f}", "квота за 7 дней*")]
    rows = "".join(
        f"<tr><td>{e(c['name'])}</td><td>{c['age']} мес</td><td>{e(c['channels'])}</td>"
        f"<td>{e(c['last_game'])}</td><td>{c['feedback_7d']}</td></tr>" for c in data["children"])
    slo_rows = ""
    for name, (title, target) in slo.OBJECTIVES.items():
        good, total = data["slo"][name]
        share = good / total if total else None
        mark = "—" if share is None else (f'<span class="ok">{share:.0%}</span>' if share >= target
                                          else f'<span class="bad">{share:.0%}</span>')
        slo_rows += f"<tr><td>{e(title)}</td><td>{mark} ({good}/{total})</td><td>{target:.0%}</td></tr>"
    total_games = sum(data["origins"].values()) or 1
    origin_rows = "".join(
        f"<tr><td>{e(ORIGIN_LABELS.get(k, k))}</td><td>{v}</td><td>{v * 100 // total_games}%</td></tr>"
        for k, v in sorted(data["origins"].items(), key=lambda kv: -kv[1]))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Игра дня — дашборд</title><style>{CSS}</style></head><body>
<h1>Игра дня — дашборд</h1><p class="sub">на {e(data['today'])}, окно {DAYS} дней</p>
<div class="grid">{''.join(f'<div class="card kpi"><b>{e(str(v))}</b><span>{e(t)}</span></div>' for v, t in kpis)}</div>
<div class="card"><h2>Доставлено игр в день</h2>{bars(data['sent'], '--a', data['days'])}</div>
<div class="card"><h2>Отзывы в день</h2>{bars(data['feedback'], '--b', data['days'])}</div>
<div class="card"><h2>Расход модели в день, $ (эквивалент API)</h2>{bars(data['cost'], '--c', data['days'], '$')}</div>
<div class="card"><h2>Отклонено проверкой безопасности</h2>{bars(data['rejected'], '--d', data['days'])}</div>
<h2>Надёжность</h2><div class="card"><table><tr><th>цель</th><th>факт</th><th>норма</th></tr>{slo_rows}</table></div>
<h2>Откуда игры</h2><div class="card"><table><tr><th>источник</th><th>игр</th><th>доля</th></tr>{origin_rows}</table></div>
<h2>Дети</h2><div class="card"><table><tr><th>имя</th><th>возраст</th><th>каналы</th><th>последняя игра</th><th>отзывов 7д</th></tr>{rows}</table></div>
<p class="legend">* доллары — эквивалент цены API; на подписке это расход квоты, а не деньги.</p>
</body></html>"""


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
