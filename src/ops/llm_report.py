#!/usr/bin/env python3
"""Weekly LLM report for daily preparation, from model_runs.

Per model: games prepared, attempts per game, first-attempt pass rate, latency
(median, p95), tokens, cost per game, web searches, and the most frequent
validator rejection reasons. Dry runs are excluded.

    python3 src/ops/llm_report.py            # print the last 7 days
    python3 src/ops/llm_report.py --days 30
    python3 src/ops/llm_report.py --send     # also send it to the owner (ops notifier)

Cost is the API-equivalent figure Claude Code reports; on a subscription it is a
measure of quota use, not money spent.
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "game-of-the-day.db"
NOTIFIER = ROOT / "src" / "ops" / "notifier.py"


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[index]


def seconds(ms):
    return "—" if ms is None else f"{ms / 1000:.0f} с"


def short_reason(error):
    """Group similar validator messages: keep the part before the first colon."""
    return error.split(":")[0].strip()[:80]


def summarise(rows):
    """rows: dicts from model_runs. Returns per-model stats."""
    by_model = {}
    for row in rows:
        by_model.setdefault(row["model"] or row["backend"] or "?", []).append(row)
    stats = {}
    for model, runs in by_model.items():
        days = {(r["target_date"], r["child_id"]) for r in runs}
        passed = [r for r in runs if r["outcome"] == "pass"]
        first = [r for r in runs if r["attempt"] == 1]
        reasons = Counter()
        for r in runs:
            if r["outcome"] == "rejected" and r["errors_json"]:
                reasons.update(short_reason(e) for e in json.loads(r["errors_json"]))
        costs = [r["cost_usd"] for r in runs if r["cost_usd"] is not None]
        walls = [r["wall_ms"] for r in runs if r["wall_ms"] is not None]
        stats[model] = {
            "days": len(days),
            "games": len(passed),
            "failed_days": len(days) - len({(r["target_date"], r["child_id"]) for r in passed}),
            "attempts": len(runs),
            "errors": sum(1 for r in runs if r["outcome"] == "error"),
            "first_pass_rate": (sum(1 for r in first if r["outcome"] == "pass") / len(first)) if first else None,
            "wall_p50": percentile(walls, 0.5),
            "wall_p95": percentile(walls, 0.95),
            "output_tokens": sum(r["output_tokens"] or 0 for r in runs),
            "input_tokens": sum((r["input_tokens"] or 0) + (r["cache_read_tokens"] or 0)
                                + (r["cache_write_tokens"] or 0) for r in runs),
            "web_searches": sum(r["web_searches"] or 0 for r in runs),
            "cost": sum(costs) if costs else None,
            "reasons": reasons.most_common(5),
        }
    return stats


def report(db_path=DB_PATH, days=7, today=None):
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_runs'").fetchone() is None:
            return "Метрик модели пока нет (база до schema 4)."
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM model_runs WHERE dry_run = 0 AND target_date > ? ORDER BY id", (since,)
        )]
        origins = {}
        if "origin" in {r[1] for r in con.execute("PRAGMA table_info(games)")}:
            origins = dict(con.execute(
                "SELECT origin, count(*) FROM games WHERE game_date > ? GROUP BY origin", (since,)
            ).fetchall())
    finally:
        con.close()
    reserve = ""
    if origins.get("fallback_model") or origins.get("library"):
        reserve = (f"\n🟠 Из резерва: резервной моделью {origins.get('fallback_model', 0)}, "
                   f"из библиотеки {origins.get('library', 0)} (из {sum(origins.values())} игр)")
    if not rows:
        return f"За {days} дн. запусков модели не было." + reserve

    lines = [f"🤖 Подбор игр за {days} дн."]
    for model, s in summarise(rows).items():
        per_game = f"${s['cost'] / s['games']:.2f}" if s["cost"] is not None and s["games"] else "—"
        rate = "—" if s["first_pass_rate"] is None else f"{s['first_pass_rate']:.0%}"
        lines += [
            "",
            f"Модель: {model}",
            f"• игр подготовлено: {s['games']}" + (f", не удалось: {s['failed_days']}" if s["failed_days"] else ""),
            f"• попыток на игру: {s['attempts'] / max(s['games'], 1):.1f}; с первой попытки: {rate}"
            + (f"; сбоев модели: {s['errors']}" if s["errors"] else ""),
            f"• время попытки: медиана {seconds(s['wall_p50'])}, p95 {seconds(s['wall_p95'])}",
            f"• токены: вход {s['input_tokens']:,}, выход {s['output_tokens']:,}; веб-поисков {s['web_searches']}".replace(",", " "),
            f"• стоимость (эквивалент API): всего ${s['cost']:.2f}, на игру {per_game}" if s["cost"] is not None
            else "• стоимость: нет данных",
        ]
        if s["reasons"]:
            lines.append("• причины отказов валидатора: " + "; ".join(f"{r} ×{n}" for r, n in s["reasons"]))
    return "\n".join(lines) + reserve


def main(argv=None):
    parser = argparse.ArgumentParser(prog="llm-report")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--send", action="store_true", help="send to the owner via the ops notifier")
    args = parser.parse_args(argv)
    text = report(days=args.days)
    print(text)
    if args.send:
        title, _, details = text.partition("\n")
        result = subprocess.run([sys.executable, str(NOTIFIER), "info", title, "--details", details.strip()],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
