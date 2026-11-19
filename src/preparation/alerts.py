"""Owner alerts from preparation: per-child alerts, collected into one message per
night run, and the run summary with the safety reviewer's work."""

import json
import subprocess
import sys
from pathlib import Path

from src.preparation import store


ROOT = Path(__file__).resolve().parents[2]


def send_ops_notification(level, title, details, notes=None):
    """Alert the owner. During a night run notes is the run's collector: per-child alerts
    go into one message at the end (50 children must not mean 50 messages, at night)."""
    if notes is not None:
        notes.append((level, title, details))
        return
    notify_now(level, title, details)


def notify_now(level, title, details, silent=False):
    notifier = ROOT / "src" / "ops" / "notifier.py"

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(notifier),
                level,
                title,
                "--details",
                details,
            ] + (["--silent"] if silent else []),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        print(
            f"WARNING: Ops notification failed: {exc}",
            file=sys.stderr,
        )
        return

    if result.returncode != 0:
        print(
            "WARNING: Ops notification failed:\n"
            + result.stderr[-2000:],
            file=sys.stderr,
        )
    else:
        print(result.stdout.strip())


def send_preparation_success_alert(
    legacy_id,
    target_date,
    candidate,
    attempts,
    child_name=None,
    origin="model",
    origin_reason=None,
    notes=None,
):
    details = (
        (f"Ребёнок: {child_name}\n" if child_name else "")
        + f"ID: {legacy_id}\n"
        f"Дата: {target_date}\n"
        f"Название: {candidate['title']}\n"
        f"Навык: {candidate['skill']}\n"
        + (f"Попыток подбора: {attempts}\n" if origin != "library" else
           f"Из библиотеки: копия {candidate.get('library_source')}\n")
        + (f"Причина резерва: {origin_reason}\n" if origin != "model" else "")
        + "Статус: ожидает отправки"
    )
    titles = {
        "model": ("info", "Игра подготовлена"),
        "fallback_model": ("warning", "Игра подготовлена резервной моделью"),
        "independent_model": ("warning", "Игра подготовлена независимой моделью (Workers AI)"),
        "library": ("warning", "Игра из резерва (библиотека), а не подобрана"),
    }
    level, title = titles.get(origin, titles["model"])

    send_ops_notification(
        level,
        title,
        details,
        notes,
    )


TELEGRAM_LIMIT = 3900


def run_summary(total, notes, failures, review_text=None):
    """One message for the whole run: (level, title, details, silent), or None when the
    run prepared nothing (the retry runs after a good night stay quiet).

    All children prepared by the model: one silent 🟢 line per child. A game from the
    reserve or a failure makes the message loud (🟡 / 🔴) and lists the problems first.
    """
    if not notes and not failures:
        return None
    prepared = [n for n in notes if n[1] != "Подготовка игры не удалась"]
    fallback = [n for n in prepared if n[0] == "warning"]
    level = "critical" if failures else "warning" if fallback else "info"
    title = (f"Подготовка: готово {len(prepared)} из {total}"
             + (f", из резерва {len(fallback)}" if fallback else "")
             + (f", ошибок {len(failures)}" if failures else ""))

    def line(details):
        fields = dict(row.split(": ", 1) for row in details.splitlines() if ": " in row)
        who = fields.get("Ребёнок", "")
        return f"{who + ': ' if who else ''}«{fields.get('Название', '?')}» ({fields.get('Навык', '?')})"

    lines = []
    for failure in failures:
        lines.append(f"⛔ {failure[:300]}")
    for _, title_, details in fallback:
        reason = next((r.split(": ", 1)[1] for r in details.splitlines()
                       if r.startswith("Причина резерва: ")), "")
        lines.append(f"📚 {line(details)}: {title_.lower()}" + (f" — {reason[:200]}" if reason else ""))
    lines += [f"✅ {line(d)}" for lvl, _, d in prepared if lvl == "info"]
    if review_text:
        lines += ["", "Проверка безопасности:", review_text]
    details = "\n".join(lines)
    if len(details) > TELEGRAM_LIMIT:
        details = details[:TELEGRAM_LIMIT] + "\n…"
    return level, title, details, level == "info"


def review_summary(target_date, since):
    """The observation week of the review tiers: what the reviewer did in this run, per
    child, and which games came from the library. None when the run reviewed nothing
    (the game was already prepared)."""
    with store.connect_db() as con:
        rows = con.execute(
            "SELECT r.child_id, c.name, r.outcome, r.errors_json FROM model_runs r "
            "LEFT JOIN children c ON c.id = r.child_id WHERE r.target_date = ? AND r.at >= ? "
            "AND r.model LIKE 'review:%' AND r.dry_run = 0 ORDER BY r.id",
            (str(target_date), since),
        ).fetchall()
        library = [r[0] for r in con.execute(
            "SELECT COALESCE(c.name, '?') FROM games g LEFT JOIN children c ON c.id = g.child_id "
            "WHERE g.game_date = ? AND g.origin = 'library'", (str(target_date),))]
        spend = con.execute(
            "SELECT r.model LIKE 'review:%', count(*), COALESCE(sum(r.input_tokens), 0) "
            "+ COALESCE(sum(r.cache_read_tokens), 0) + COALESCE(sum(r.cache_write_tokens), 0), "
            "COALESCE(sum(r.output_tokens), 0), COALESCE(sum(r.cost_usd), 0) FROM model_runs r "
            "WHERE r.target_date = ? AND r.at >= ? AND r.dry_run = 0 GROUP BY 1 ORDER BY 1",
            (str(target_date), since),
        ).fetchall()
    if not rows:
        return None
    rejected = sum(1 for r in rows if r[2] == "rejected")
    by_rules = sum(1 for r in rows if r[2] == "rejected" and "safety review (rule): " in (r[3] or ""))
    lines = [f"Проверок: {len(rows)}, отклонено: {rejected} ({round(100 * rejected / len(rows))}%)"
             + (f", из них правилом кода: {by_rules}." if by_rules else ".")]
    for child_id, name, outcome, errors_json in rows:
        for item in json.loads(errors_json or "[]"):
            if item.startswith("safety review: "):
                lines.append(f"⛔ {name or child_id}: {item[len('safety review: '):][:200]}")
            elif item.startswith("safety review (rule): "):
                lines.append(f"⛔ {name or child_id} (правило кода, модель считала это оговоркой): "
                             f"{item[len('safety review (rule): '):][:200]}")
            elif item.startswith("safety fix: "):
                lines.append(f"✏️ {name or child_id}: {item[len('safety fix: '):][:200]}")
    if library:
        lines.append("📚 Из библиотеки: " + ", ".join(library))
    k = lambda n: f"{n / 1000:.0f}k" if n >= 1000 else str(n)  # noqa: E731
    total = 0.0
    for is_review, calls, tokens_in, tokens_out, cost in spend:
        total += cost
        what = "проверка" if is_review else "подготовка"
        lines.append(f"💰 {what}: {calls} вызовов, токены {k(tokens_in)} вход / {k(tokens_out)} выход, ${cost:.3f}")
    if spend:
        lines.append(f"💰 итого за прогон: ${total:.3f}")
    return "\n".join(lines)
