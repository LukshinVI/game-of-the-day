"""Alternative looks for the owner dashboard (src/ops/dashboard.py collects the data).

Each view is one self-contained HTML page: inline CSS and SVG, no scripts, no network.
Views share the chart pieces below and differ in layout and tone:

- ops:     dark, dense, everything on one screen, like a monitoring console;
- report:  a calm weekly report that reads top to bottom, numbers in sentences;
- product: product analytics, the funnel and the child-by-day map up front.
"""

import html

from src.ops import slo

e = html.escape

HEAT_LABELS = {"fb": "был отзыв", "sent": "игра пришла", "swap": "замена", "miss": "пропуск"}
REACTION_LABELS = {"outcome:liked": "понравилось", "outcome:neutral": "нейтрально", "outcome:disliked": "не понравилось",
                   "difficulty:too_easy": "слишком легко", "difficulty:right": "в самый раз",
                   "difficulty:too_hard": "слишком сложно", "attention:short": "трудно удержать внимание",
                   "attention:tired": "был уставший"}


def ddmm(day):
    return f"{day[8:]}.{day[5:7]}"


def spark(values, var="--accent", width=220, height=44, unit=""):
    """A sparkline: 2px line, soft area, the last point marked; hover shows each day."""
    top = max(values) or 1
    step = width / (len(values) - 1)
    pts = [(i * step, 4 + (height - 8) * (1 - v / top)) for i, v in enumerate(values)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    x, y = pts[-1]
    return (f'<svg viewBox="0 0 {width} {height}" class="spark" role="img">'
            f'<polygon points="0,{height} {path} {width},{height}" style="fill:var({var});opacity:.14"/>'
            f'<polyline points="{path}" fill="none" style="stroke:var({var})" stroke-width="2" stroke-linejoin="round"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" style="fill:var({var})"/>'
            + "".join(f'<rect x="{px - step / 2:.1f}" y="0" width="{step:.1f}" height="{height}" fill="transparent">'
                      f'<title>{v}{unit}</title></rect>' for (px, _), v in zip(pts, values))
            + "</svg>")


def columns(values, days, var="--accent", width=560, height=120, unit=""):
    """Thin columns with 2px gaps, max and dates labelled."""
    top = max(values) or 1
    slot = width / len(values)
    out = [f'<svg viewBox="0 0 {width} {height + 18}" role="img">']
    for i, v in enumerate(values):
        h = (height - 14) * v / top
        out.append(f'<rect x="{i * slot + 1:.1f}" y="{height - h:.1f}" width="{slot - 2:.1f}" height="{h:.1f}" rx="2" '
                   f'style="fill:var({var})"><title>{ddmm(days[i])}: {v}{unit}</title></rect>')
    out.append(f'<line x1="0" x2="{width}" y1="{height}" y2="{height}" style="stroke:var(--grid)"/>')
    out.append(f'<text x="0" y="10" class="ax">макс {top}{unit}</text>'
               f'<text x="0" y="{height + 14}" class="ax">{ddmm(days[0])}</text>'
               f'<text x="{width}" y="{height + 14}" class="ax" text-anchor="end">{ddmm(days[-1])}</text></svg>')
    return "".join(out)


def heatmap(heat, days, cell=14, gap=2, label_w=78):
    """Child x day: the state of each child-day as a coloured square."""
    width = label_w + len(days) * (cell + gap)
    height = len(heat) * (cell + gap) + 18
    out = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for r, row in enumerate(heat):
        y = r * (cell + gap)
        out.append(f'<text x="0" y="{y + cell - 3}" class="lbl">{e(row["name"][:10])}</text>')
        for c, state in enumerate(row["cells"]):
            x = label_w + c * (cell + gap)
            fill = f"var(--h-{state})" if state else "var(--h-none)"
            tip = f"{e(row['name'])}, {ddmm(days[c])}: {HEAT_LABELS.get(state, 'ещё не подписан')}"
            out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" style="fill:{fill}"><title>{tip}</title></rect>')
    y = len(heat) * (cell + gap) + 12
    out.append(f'<text x="{label_w}" y="{y}" class="ax">{ddmm(days[0])}</text>'
               f'<text x="{width}" y="{y}" class="ax" text-anchor="end">{ddmm(days[-1])}</text></svg>')
    return "".join(out)


def heat_legend():
    return '<div class="legend">' + "".join(
        f'<span><i style="background:var(--h-{k})"></i>{v}</span>' for k, v in HEAT_LABELS.items()) + "</div>"


def hbars(rows, var="--accent"):
    """Horizontal bars as HTML (label, value): good for skills and the funnel."""
    top = max((v for _, v in rows), default=0) or 1
    return '<div class="hbars">' + "".join(
        f'<div class="hb"><span class="hb-l">{e(str(label))}</span>'
        f'<span class="hb-t"><span class="hb-f" style="width:{max(2, v * 100 / top):.0f}%;background:var({var})"></span></span>'
        f'<span class="hb-v">{v}</span></div>' for label, v in rows) + "</div>"


def funnel(rows):
    first = rows[0][1] or 1
    out = ['<div class="funnel">']
    for i, (label, v) in enumerate(rows):
        share = f"{v * 100 // first}%" if i else "100%"
        out.append(f'<div class="fn" style="--w:{max(8, v * 100 / first):.0f}%"><div class="fn-bar"></div>'
                   f'<div class="fn-t"><b>{v}</b> {e(label)} <span class="muted">{share}</span></div></div>')
    return "".join(out) + "</div>"


def slo_rows(data):
    out = []
    for name, (title, target) in slo.OBJECTIVES.items():
        good, total = data["slo"][name]
        share = good / total if total else None
        ok = share is not None and share >= target
        status = "нет данных" if share is None else ("в норме" if ok else "ниже цели")
        out.append((title, "—" if share is None else f"{share:.0%}", f"{good}/{total}", f"{target:.0%}",
                    "none" if share is None else ("ok" if ok else "bad"), status))
    return out


def reactions(data):
    return [(REACTION_LABELS.get(k, k), v) for k, v in sorted(data["reactions"].items(), key=lambda kv: -kv[1])]


def totals(data):
    week, prev = slice(-7, None), slice(-14, -7)
    q, ev = data["quality"], data["events"]
    on_good, on_total = data["slo"]["on_time"]
    return {
        "children": len(data["children"]), "families": data["families"],
        "sent7": sum(data["sent"][week]), "sent_prev": sum(data["sent"][prev]),
        "fb7": sum(data["feedback"][week]), "fb_prev": sum(data["feedback"][prev]),
        "cost7": round(sum(data["cost"][week]), 2), "cost28": round(sum(data["cost"]), 2),
        "on_time": f"{on_good * 100 // on_total}%" if on_total else "—", "on_good": on_good, "on_total": on_total,
        "first_pass": f"{q['first_pass'] * 100 // q['games']}%" if q["games"] else "—",
        "attempts": q["attempts"], "wall": f"{q['wall_median_s']} с" if q["wall_median_s"] is not None else "—",
        "review": f"{q['review_rejected']}/{q['reviews']}",
        "sent_time": ev["median_sent"] or "—",
        "backup": f"{ev['backup_hours']:.0f} ч" if ev.get("backup_hours") is not None else "—",
        "fb_rate": f"{sum(data['feedback']) * 100 // sum(data['sent'])}%" if sum(data["sent"]) else "—",
    }


def delta(now, before):
    diff = now - before
    if diff == 0:
        return "="
    return f"{'+' if diff > 0 else '−'}{abs(diff):g}"


BASE_CSS = """
* { box-sizing:border-box; } html, body { margin:0; background:var(--bg); }
body { padding:20px 16px 32px; color:var(--ink); }
svg { width:100%; height:auto; display:block; overflow:visible; }
svg .ax { font-size:11px; fill:var(--muted); } svg .lbl { font-size:11px; fill:var(--ink2); }
.muted { color:var(--muted); }
.legend { display:flex; flex-wrap:wrap; gap:4px 14px; font-size:12px; color:var(--ink2); margin-top:6px; }
.legend i { width:10px; height:10px; border-radius:3px; display:inline-block; margin-right:5px; vertical-align:-1px; }
.hbars { display:grid; gap:6px; }
.hb { display:grid; grid-template-columns:minmax(90px, 38%) 1fr 32px; gap:8px; align-items:center; font-size:13px; }
.hb-l { color:var(--ink2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.hb-t { height:8px; background:var(--track); border-radius:4px; overflow:hidden; }
.hb-f { display:block; height:100%; border-radius:4px; }
.hb-v { text-align:right; font-variant-numeric:tabular-nums; }
.funnel { display:grid; gap:6px; }
.fn { position:relative; height:34px; }
.fn-bar { position:absolute; inset:0 auto 0 0; width:var(--w); background:var(--accent); opacity:.16; border-radius:8px; }
.fn-t { position:relative; padding:7px 10px; font-size:13px; white-space:nowrap; }
table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
th { text-align:left; font-weight:500; color:var(--muted); padding:7px 6px; border-bottom:1px solid var(--line); }
td { padding:8px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:0; } .num { text-align:right; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
.dot.ok { background:var(--good); } .dot.bad { background:var(--bad); } .dot.none { background:var(--muted); }
"""


def page(title, css, body, font):
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title>'
            f'<style>{BASE_CSS}{css} body {{ font:{font}; }}</style></head><body><div class="wrap">{body}</div></body></html>')


def children_table(data):
    return ("<table><tr><th>имя</th><th class='num'>возраст</th><th>каналы</th><th>последняя игра</th>"
            "<th class='num'>отзывы 7д</th></tr>" + "".join(
                f"<tr><td>{e(c['name'])}</td><td class='num'>{c['age']} мес</td><td>{e(c['channels'])}</td>"
                f"<td>{e(c['last_game'])}</td><td class='num'>{c['feedback_7d']}</td></tr>" for c in data["children"])
            + "</table>")


def slo_table(data):
    return ("<table><tr><th>цель</th><th class='num'>факт</th><th class='num'>события</th><th class='num'>норма</th>"
            "<th>статус</th></tr>" + "".join(
                f"<tr><td>{e(t)}</td><td class='num'>{v}</td><td class='num muted'>{n}</td><td class='num muted'>{g}</td>"
                f"<td><span class='dot {k}'></span>{s}</td></tr>" for t, v, n, g, k, s in slo_rows(data)) + "</table>")


# --- ops: a dark monitoring console ------------------------------------------------

OPS_CSS = """
:root { --bg:#0d1117; --panel:#161b22; --line:#262c36; --grid:#262c36; --track:#21262d;
  --ink:#e6edf3; --ink2:#c9d1d9; --muted:#7d8590; --accent:#58a6ff; --accent2:#3fb950; --accent3:#d29922;
  --accent4:#bc8cff; --good:#3fb950; --bad:#f85149;
  --h-fb:#3fb950; --h-sent:#1f6feb; --h-swap:#d29922; --h-miss:#f85149; --h-none:#21262d; }
.wrap { max-width:1100px; margin:0 auto; }
.top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:12px; }
.top h1 { font-size:15px; font-weight:600; margin:0; letter-spacing:.02em; text-transform:uppercase; color:var(--ink2); }
.top .meta { color:var(--muted); font-size:12px; }
.g { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:10px; margin-bottom:10px; }
.p { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px 12px; }
.p h2 { font-size:11px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; margin:0 0 6px; }
.big { font:600 28px/1.1 ui-monospace, SFMono-Regular, Menlo, monospace; }
.d { font:12px ui-monospace, SFMono-Regular, Menlo, monospace; color:var(--muted); margin-left:6px; }
.kv { display:grid; grid-template-columns:1fr auto; gap:4px 10px; font-size:13px; }
.kv b { font:600 13px ui-monospace, SFMono-Regular, Menlo, monospace; text-align:right; }
.wide { grid-column:1 / -1; }
"""


def ops(data):
    t = totals(data)
    days = data["days"]

    def stat(title, value, extra, series, var):
        return (f'<div class="p"><h2>{e(title)}</h2><div><span class="big">{e(str(value))}</span>'
                f'<span class="d">{e(extra)}</span></div>{spark(series, var)}</div>')

    kv = lambda rows: '<div class="kv">' + "".join(f"<span>{e(k)}</span><b>{e(str(v))}</b>" for k, v in rows) + "</div>"
    body = f"""
<div class="top"><h1>Игра дня · console</h1><div class="meta">{ddmm(data['today'])} · окно 28 дней</div></div>
<div class="g">
{stat("игр за 7 дней", t["sent7"], delta(t["sent7"], t["sent_prev"]) + " к прошлой", data["sent"], "--accent")}
{stat("отзывов за 7 дней", t["fb7"], delta(t["fb7"], t["fb_prev"]) + " к прошлой", data["feedback"], "--accent2")}
{stat("квота за 7 дней", f"${t['cost7']:.2f}", f"28д ${t['cost28']:.2f}", data["cost"], "--accent3")}
{stat("отклонено проверкой", sum(data["rejected"][-7:]), "за 7 дней", data["rejected"], "--accent4")}
</div>
<div class="g">
<div class="p"><h2>надёжность</h2>{slo_table(data)}</div>
<div class="p"><h2>подбор</h2>{kv([("подборов за 28 дней", data["quality"]["games"]), ("с первой попытки", t["first_pass"]),
    ("запусков на игру", t["attempts"]), ("медиана времени", t["wall"]), ("проверка: отклонено", t["review"])])}</div>
<div class="p"><h2>система</h2>{kv([("детей / получателей", f"{t['children']} / {t['families']}"), ("медиана отправки", t["sent_time"]),
    ("последний бэкап", t["backup"]), ("застряло в очереди", data["events"]["outbox_stuck"]),
    ("замен «другую»", data["events"]["swaps"]), ("привязок", data["events"]["links"])])}</div>
</div>
<div class="g"><div class="p wide"><h2>ребёнок × день</h2>{heatmap(data["heat"], days)}{heat_legend()}</div></div>
<div class="g">
<div class="p"><h2>воронка</h2>{hbars(data["funnel"], "--accent")}</div>
<div class="p"><h2>навыки, 28 дней</h2>{hbars(data["skills"], "--accent2")}</div>
<div class="p"><h2>реакции в отзывах</h2>{hbars(reactions(data), "--accent4") if data["reactions"] else '<span class="muted">пока нет</span>'}</div>
</div>
<div class="g"><div class="p wide"><h2>доставки по дням</h2>{columns(data["sent"], days, "--accent")}</div></div>
<div class="g"><div class="p wide"><h2>дети</h2>{children_table(data)}</div></div>
"""
    return page("Игра дня — console", OPS_CSS, body, "13px/1.45 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif")


# --- report: a weekly report that reads top to bottom ------------------------------

REPORT_CSS = """
:root { --bg:#faf9f6; --line:#e7e4dc; --grid:#e7e4dc; --track:#eeebe4; --ink:#1d1b16; --ink2:#47443c;
  --muted:#8a867b; --accent:#2f5d50; --accent2:#b5673b; --good:#2f7d32; --bad:#b3261e;
  --h-fb:#2f5d50; --h-sent:#9fbfb4; --h-swap:#d9a46f; --h-miss:#c7584d; --h-none:#eeebe4; }
@media (prefers-color-scheme: dark) { :root { --bg:#191815; --line:#34322c; --grid:#34322c; --track:#2a2924;
  --ink:#f1eee6; --ink2:#d3cfc4; --muted:#9b978b; --accent:#7fb8a6; --accent2:#e0956a;
  --h-fb:#7fb8a6; --h-sent:#3f6359; --h-swap:#a87447; --h-miss:#c7584d; --h-none:#2a2924; } }
.wrap { max-width:680px; margin:0 auto; }
.kicker { color:var(--accent2); font:600 12px/1 -apple-system, sans-serif; letter-spacing:.12em; text-transform:uppercase; }
h1 { font-size:34px; line-height:1.1; margin:8px 0 6px; font-weight:600; }
.lede { font-size:18px; line-height:1.5; color:var(--ink2); margin:0 0 22px; }
.lede b { color:var(--ink); }
h2 { font-size:21px; margin:30px 0 8px; font-weight:600; border-top:1px solid var(--line); padding-top:18px; }
p { font-size:16px; line-height:1.6; color:var(--ink2); margin:0 0 12px; }
.figs { display:grid; grid-template-columns:repeat(3, 1fr); gap:14px; margin:10px 0 6px; }
.fig b { display:block; font-size:30px; font-weight:600; color:var(--ink); }
.fig span { font:13px -apple-system, sans-serif; color:var(--muted); }
.small { font:13px -apple-system, sans-serif; }
table, .hbars, .legend, .funnel { font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
@media (max-width:480px) { .figs { grid-template-columns:repeat(2, 1fr); } h1 { font-size:28px; } }
"""


def report(data):
    t = totals(data)
    days = data["days"]
    trend = ("больше, чем неделей раньше" if t["sent7"] > t["sent_prev"]
             else "меньше, чем неделей раньше" if t["sent7"] < t["sent_prev"] else "столько же, сколько неделей раньше")
    ok = all(k in ("ok", "none") for *_, k, _ in slo_rows(data))
    body = f"""
<div class="kicker">Игра дня · еженедельный обзор · {ddmm(data['today'])}</div>
<h1>{"Всё идёт по плану" if ok else "Есть над чем поработать"}</h1>
<p class="lede">За последние 7 дней <b>{t['sent7']} игр</b> дошли до <b>{t['children']} детей</b> — {trend}.
Родители оставили <b>{t['fb7']} отзывов</b>; вовремя, до 10:15, пришли <b>{t['on_time']}</b> игр за 28 дней.</p>
<div class="figs">
<div class="fig"><b>{t['sent7']}</b><span>игр за неделю ({delta(t['sent7'], t['sent_prev'])})</span></div>
<div class="fig"><b>{t['fb7']}</b><span>отзывов ({delta(t['fb7'], t['fb_prev'])})</span></div>
<div class="fig"><b>{t['fb_rate']}</b><span>игр с отзывом, 28 дней</span></div>
<div class="fig"><b>{t['first_pass']}</b><span>подобраны с первой попытки</span></div>
<div class="fig"><b>{t['sent_time']}</b><span>обычное время отправки</span></div>
<div class="fig"><b>${t['cost7']:.2f}</b><span>квота за неделю</span></div>
</div>
<h2>Как дети получали игры</h2>
<p>Каждая клетка — день одного ребёнка. Зелёная — игра пришла и родители ответили; светлая — игра пришла; красная — день без игры.</p>
{heatmap(data["heat"], days)}{heat_legend()}
<h2>Что говорят родители</h2>
<p>Из отзывов за 28 дней.</p>
{hbars(reactions(data), "--accent") if data["reactions"] else '<p class="muted">Отзывов с оценками пока нет.</p>'}
<h2>Чем занимались</h2>
<p>Навыки, которые тренировали игры за 28 дней.</p>
{hbars(data["skills"], "--accent2")}
<h2>Путь семьи</h2>
<p>От заявки до регулярных отзывов.</p>
{funnel(data["funnel"])}
<h2>Надёжность</h2>
<p>Цели из docs/slo.md. Проверка безопасности за 28 дней отклонила {t['review']} кандидатов, в среднем {t['attempts']} запуска модели на игру.</p>
{slo_table(data)}
<h2>Семьи</h2>
{children_table(data)}
<p class="small muted" style="margin-top:22px">Доллары — эквивалент цены API; на подписке это расход квоты. Последний бэкап: {t['backup']} назад.</p>
"""
    return page("Игра дня — обзор", REPORT_CSS, body, "16px/1.5 Georgia, 'Times New Roman', serif")


# --- product: analytics, funnel and the map up front -------------------------------

PRODUCT_CSS = """
:root { --bg:#f6f7fb; --card:#ffffff; --line:#e6e8f0; --grid:#e6e8f0; --track:#eef0f6; --ink:#151a2d; --ink2:#3d4460;
  --muted:#7a809a; --accent:#5b5bd6; --accent2:#12a594; --accent3:#e5484d; --good:#12a594; --bad:#e5484d;
  --h-fb:#5b5bd6; --h-sent:#b4b4f0; --h-swap:#f5a524; --h-miss:#e5484d; --h-none:#eef0f6; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1117; --card:#171a23; --line:#262a38; --grid:#262a38;
  --track:#202433; --ink:#eef0f8; --ink2:#c4c8dc; --muted:#8a90aa; --accent:#8b8bf5; --accent2:#2ec4b0;
  --h-fb:#8b8bf5; --h-sent:#3b3b7a; --h-swap:#c98a1d; --h-miss:#e5484d; --h-none:#202433; } }
.wrap { max-width:960px; margin:0 auto; }
.hd { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; flex-wrap:wrap; gap:8px; }
.hd h1 { font-size:22px; margin:0; font-weight:700; letter-spacing:-.02em; }
.chip { background:var(--card); border:1px solid var(--line); border-radius:999px; padding:4px 12px; font-size:12px; color:var(--ink2); }
.row { display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:14px; margin-bottom:14px; }
.c { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px 18px;
  box-shadow:0 1px 2px rgba(20,25,50,.04); }
.c h2 { font-size:14px; margin:0 0 2px; font-weight:650; } .c .s { color:var(--muted); font-size:12px; margin-bottom:12px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:14px; margin-bottom:14px; }
.k { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px 16px; }
.k .l { font-size:12px; color:var(--muted); } .k .v { font-size:28px; font-weight:700; letter-spacing:-.02em; margin:2px 0; }
.k .t { font-size:12px; color:var(--accent2); font-weight:600; }
.full { grid-column:1 / -1; }
"""


def product(data):
    t = totals(data)
    days = data["days"]
    kpis = [("Активные дети", t["children"], f"{t['families']} получателей"),
            ("Игр за 7 дней", t["sent7"], f"{delta(t['sent7'], t['sent_prev'])} к прошлой неделе"),
            ("Отзывов за 7 дней", t["fb7"], f"{delta(t['fb7'], t['fb_prev'])} к прошлой неделе"),
            ("Доля игр с отзывом", t["fb_rate"], "за 28 дней"),
            ("Вовремя", t["on_time"], f"{t['on_good']} из {t['on_total']} дней"),
            ("С первой попытки", t["first_pass"], f"медиана {t['wall']}")]
    body = f"""
<div class="hd"><h1>Игра дня · продукт</h1><span class="chip">{ddmm(data['today'])} · 28 дней</span></div>
<div class="kpis">{''.join(f'<div class="k"><div class="l">{e(l)}</div><div class="v">{e(str(v))}</div><div class="t">{e(s)}</div></div>' for l, v, s in kpis)}</div>
<div class="row">
<div class="c"><h2>Воронка</h2><div class="s">от заявки до регулярных отзывов</div>{funnel(data["funnel"])}</div>
<div class="c"><h2>Реакции родителей</h2><div class="s">из отзывов за 28 дней</div>{hbars(reactions(data), "--accent") if data["reactions"] else '<span class="muted">пока нет</span>'}</div>
</div>
<div class="row"><div class="c full"><h2>Ребёнок × день</h2><div class="s">состояние каждого дня каждого ребёнка</div>{heatmap(data["heat"], days)}{heat_legend()}</div></div>
<div class="row">
<div class="c"><h2>Доставки</h2><div class="s">игр в день</div>{columns(data["sent"], days, "--accent")}</div>
<div class="c"><h2>Отзывы</h2><div class="s">отзывов в день</div>{columns(data["feedback"], days, "--accent2")}</div>
</div>
<div class="row">
<div class="c"><h2>Навыки</h2><div class="s">что тренировали игры</div>{hbars(data["skills"], "--accent2")}</div>
<div class="c"><h2>Надёжность</h2><div class="s">цели docs/slo.md</div>{slo_table(data)}</div>
</div>
<div class="row"><div class="c full"><h2>Дети</h2><div class="s">каналы, последняя игра, отзывы за неделю</div>{children_table(data)}</div></div>
<p class="muted" style="font-size:12px">Квота за 7 дней ${t['cost7']:.2f} (эквивалент цены API) · проверка безопасности отклонила {t['review']} · замен «другую»: {data['events']['swaps']} · бэкап {t['backup']} назад</p>
"""
    return page("Игра дня — продукт", PRODUCT_CSS, body, "14px/1.45 Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif")


VIEWS = {"ops": ops, "report": report, "product": product}
