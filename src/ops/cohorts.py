"""Do families stay? Retention by monthly cohort, and whether they still talk to us.

A child joins the cohort of the month of its first delivered game. It is retained at
day N when it received a game on day N or later (a pause or a departure stops the
games, so both count as churn until the games come back). A horizon is shown only for
children whose first game is more than N days old. Families that deleted their data are
counted too, from the span kept in `erasures` (src/bot/erasure.py): otherwise every
departure would silently raise the retention. Test children are excluded.

"Talks to us": of the children who got a game in the last 30 days, the share whose
family wrote at least one answer in that time.

Read-only, deterministic. Shown in /metrics and the Monday report
(src/ops/product_metrics.py).
"""

from collections import defaultdict
from datetime import date, timedelta

HORIZONS = (7, 30, 60)


def _has(con, table, column=None):
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is None:
        return False
    return column is None or column in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def spans(con):
    """[(first game date, last game date)] per child, live and erased."""
    not_test = "AND c.is_test = 0" if _has(con, "children", "is_test") else ""
    rows = [(f, l) for f, l in con.execute(
        "SELECT min(g.game_date), max(g.game_date) FROM games g JOIN deliveries d ON d.game_id = g.id "
        f"JOIN children c ON c.id = g.child_id WHERE d.status = 'sent' {not_test} GROUP BY g.child_id")]
    if _has(con, "erasures", "first_game"):
        for first, last, n in con.execute(
                "SELECT first_game, last_game, children FROM erasures "
                "WHERE status IN ('archived', 'purged') AND first_game IS NOT NULL"):
            rows += [(first, last)] * max(n or 1, 1)
    return [(date.fromisoformat(f), date.fromisoformat(l)) for f, l in rows if f and l]


def table(con, today):
    """{cohort month: {"n": children, N: (retained, eligible)}}."""
    out = defaultdict(lambda: {"n": 0, **{h: [0, 0] for h in HORIZONS}})
    for first, last in spans(con):
        row = out[first.strftime("%Y-%m")]
        row["n"] += 1
        for h in HORIZONS:
            if (today - first).days > h:  # today's game may not be out yet
                row[h][1] += 1
                row[h][0] += (last - first).days >= h
    return dict(sorted(out.items()))


def talking(con, today):
    """(children with a game in 30 days, of them with a written or any answer)."""
    since = (today - timedelta(days=29)).isoformat()
    not_test = "AND c.is_test = 0" if _has(con, "children", "is_test") else ""
    got = con.execute(
        "SELECT count(DISTINCT g.child_id) FROM games g JOIN deliveries d ON d.game_id = g.id "
        f"JOIN children c ON c.id = g.child_id WHERE d.status = 'sent' AND g.game_date >= ? {not_test}",
        (since,)).fetchone()[0]
    answered = con.execute(
        "SELECT count(DISTINCT g.child_id) FROM feedback f JOIN games g ON g.id = f.game_id "
        f"JOIN children c ON c.id = g.child_id WHERE g.game_date >= ? {not_test}", (since,)).fetchone()[0]
    return got, answered


def _cell(retained, eligible):
    return f"{retained * 100 // eligible}% ({retained}/{eligible})" if eligible else "—"


def report(con, today=None):
    today = today or date.today()
    rows = table(con, today)
    lines = ["👥 Удержание по когортам (месяц первой игры; дошли до дня 7 / 30 / 60)"]
    if not rows:
        lines.append("• пока нет доставленных игр")
    for cohort, row in rows.items():
        lines.append(f"• {cohort}, детей {row['n']}: " + " · ".join(_cell(*row[h]) for h in HORIZONS))
    total = {h: [sum(r[h][i] for r in rows.values()) for i in (0, 1)] for h in HORIZONS}
    if len(rows) > 1:
        lines.append("• все: " + " · ".join(_cell(*total[h]) for h in HORIZONS))
    got, answered = talking(con, today)
    lines.append(f"• отвечают нам (за 30 дн.): {_cell(answered, got)}")
    return "\n".join(lines)
