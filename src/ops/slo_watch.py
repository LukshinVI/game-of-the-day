#!/usr/bin/env python3
"""SLO alert: tell the owner when an error budget is running out, not only when a day fails.

The outcome check (src/ops/day_outcome_check.py) reports one bad day as it happens. This
job looks at the budgets of docs/slo.md over two windows, 7 and 28 days, once a day after
the last outcome check, and writes only when a level changes:

- 🟢 ok: the budget still allows a miss (or there are no events, or none is allowed
  and there was none);
- 🟡 last: the budget is used up, the next miss exhausts it;
- 🔴 exhausted: more misses than the target allows.

A change for the worse warns, a change for the better says so too; an unchanged level is
silence, so a budget spent by an old incident does not repeat itself every day. The
first run only records the current levels. Levels are kept in STATE (the service's
state directory); no model, nothing written to the database.

    python3 src/ops/slo_watch.py [--dry-run]
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops import notifier, slo  # noqa: E402

STATE = Path("/var/lib/game-of-the-day/slo-watch.json")
WINDOWS = (7, 28)
WATCHED = ("on_time", "same_day", "feedback")
RANK = {"ok": 0, "last": 1, "out": 2}
MARK = {"ok": "🟢", "last": "🟡", "out": "🔴"}
WORDS = {"ok": "в норме", "last": "запас ошибок израсходован, следующая исчерпает бюджет", "out": "бюджет ошибок исчерпан"}


def level(target, good, total):
    if total == 0:
        return "ok"
    bad = total - good
    allowed = int((1 - target) * total + 1e-9)
    if bad > allowed:
        return "out"
    if allowed and bad == allowed:
        return "last"
    return "ok"


def levels(con, today, now=None):
    out = {}
    for days in WINDOWS:
        start = (today - timedelta(days=days - 1)).isoformat()
        counts = slo.collect(con, start, today.isoformat(), now=now)
        for name in WATCHED:
            title, target = slo.OBJECTIVES[name]
            good, total = counts[name]
            out[f"{name}:{days}"] = {"level": level(target, good, total),
                                     "line": slo.line(title, target, good, total)}
    return out


def changes(previous, current):
    """[(key, old level, new level, line)] for levels that moved."""
    moved = []
    for key, now in current.items():
        old = (previous.get(key) or {}).get("level", "ok")
        if old != now["level"]:
            moved.append((key, old, now["level"], now["line"]))
    return moved


def message(moved):
    worse = any(RANK[new] > RANK[old] for _, old, new, _ in moved)
    head = "Надёжность: бюджет ошибок тает" if worse else "Надёжность: бюджет ошибок восстановился"
    lines = []
    for key, old, new, line in moved:
        name, days = key.split(":")
        lines.append(f"{MARK[new]} За {days} дн.: {WORDS[new]} (было: {WORDS[old]})\n   {line}")
    lines.append("\nПодробно: /slo в ops-боте. Срочный разбор нужен только при 🔴 за 7 дней.")
    return worse, head, "\n".join(lines)


def load(state):
    try:
        return json.loads(state.read_text())
    except (OSError, ValueError):
        return None


def run(db_path=slo.DB_PATH, state=STATE, today=None, now=None, send=None, dry_run=False):
    today = today or date.today()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        current = levels(con, today, now=now)
    finally:
        con.close()
    previous = load(state)
    if previous is None:
        if not dry_run:
            state.write_text(json.dumps(current, ensure_ascii=False))
        return "first run: levels recorded, nothing sent"
    moved = changes(previous, current)
    if not moved:
        return "no change"
    worse, head, details = message(moved)
    if dry_run:
        return f"{head}\n{details}"
    send = send or (notifier.notify_warning if worse else notifier.notify_info)
    send(head, details)
    state.write_text(json.dumps(current, ensure_ascii=False))
    return f"sent: {len(moved)} change(s)"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="slo_watch")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
