"""The weekly test run of the independent model (Workers AI): what it would choose for
real children, sent to the owner only.

Two active children a week, in turn, with their real age, history and profile (never the
name or the birth date: the prompt carries the age in months). The whole selection path
runs: prompt, model, gate, the known-sources check, its reviewer, corrective retries.
Nothing becomes a game, nothing is sent to a family; the attempts are kept in model_runs
as dry runs. The owner gets one message: what was chosen and what the reviewer said.

    game_preparation.py trial [--children 2]

Its own model settings (PREPARATION_TRIAL_MODEL, PREPARATION_TRIAL_REVIEW_MODEL, default
gpt-oss-120b), so the trial runs while the independent model stays off in the chain.
Calls stay within the daily cap shared with voice transcription.
"""

import os
import sys
from datetime import date

from src.db.children import active_children
from src.preparation import alerts, chain, game_preparation, store, workers_ai

DEFAULT_MODEL = "@cf/openai/gpt-oss-120b"
TELEGRAM_LIMIT = 3900


def trial_model():
    return os.environ.get("PREPARATION_TRIAL_MODEL", "").strip() or DEFAULT_MODEL


def trial_review_model():
    return os.environ.get("PREPARATION_TRIAL_REVIEW_MODEL", "").strip() or trial_model()


def pick(children, target_date, count):
    """`count` children for this week, in turn by ISO week: everyone within a few weeks."""
    if not children:
        return []
    ordered = sorted(children, key=lambda c: c["id"])
    week = date.fromisoformat(str(target_date)).isocalendar()[1]
    start = (week * count) % len(ordered)
    return [ordered[(start + i) % len(ordered)] for i in range(min(count, len(ordered)))]


def describe(child, outcome, run):
    age = chain.child_age_months(outcome["date"], child["birth_date"])
    lines = [f"👶 {child['name']}, {age} мес."]
    blocks = [e for r in run.log for e in r.get("errors") or [] if e.startswith("safety review")]
    rejected = [e for r in run.log if not str(r.get("model", "")).startswith("review:")
                for e in r.get("errors") or []]
    if outcome.get("candidate"):
        c = outcome["candidate"]
        lines.append(f"✅ «{c['title']}» ({c['skill']}), попыток: {outcome['attempts']}")
        lines.append(f"Шаги: {' '.join(c.get('steps') or [])[:400]}")
        lines.append(f"Безопасность: {str(c.get('safety') or '')[:250]}")
        lines.append(f"Источник: {c.get('source_name')}")
    else:
        lines.append(f"⛔ не подобрал: {outcome['error'][:300]}")
    for item in blocks[:4]:
        lines.append(f"🛡 {item[:220]}")
    for item in [e for e in rejected if not e.startswith("safety")][:2]:
        lines.append(f"✖️ {item[:200]}")
    return "\n".join(lines)


def run_trial(target_date, count=2, send=None):
    """Run the trial for this week's children; returns the owner's message (or None)."""
    send = send or alerts.send_ops_notification
    if not (os.environ.get("CF_AI_TOKEN") and os.environ.get("CF_ACCOUNT_ID")):
        print("trial: no Workers AI token, nothing to do")
        return None
    with store.connect_db() as con:
        children = [c for c in active_children(con) if c["recipients"]]
        sources = workers_ai.known_sources(con)
    chosen = pick([c for c in children if chain.child_age_months(target_date, c["birth_date"])
                   <= chain.MAX_AGE_MONTHS_SUPPORTED], target_date, count)
    if not chosen or not sources:
        print("trial: no children or no known sources")
        return None

    parts, used_before = [], None
    for child in chosen:
        with store.connect_db() as con:
            calls, cap = workers_ai.calls_today(con, target_date), workers_ai.daily_calls()
        used_before = calls if used_before is None else used_before
        if calls >= cap:
            parts.append(f"👶 {child['name']}: пропущен, дневной лимит вызовов ({calls} из {cap})")
            continue
        history = store.load_history(child["id"])
        # The same profile, required skill and theme as the night run.
        run = game_preparation.child_run(target_date, child, history)
        run.model, run.sources, run.review_model = trial_model(), sources, trial_review_model()
        run.alert_on_exhaustion = False
        outcome = {"date": str(target_date)}
        try:
            candidate, attempts = chain.select_candidate(history, target_date, child["birth_date"], run=run)
            outcome.update(candidate=candidate, attempts=attempts)
        except Exception as exc:  # noqa: BLE001 - a failed trial is a result
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        parts.append(describe(child, outcome, run))  # before persist_runs, which empties the log
        store.persist_runs(target_date, child["id"], True, run.log)

    with store.connect_db() as con:
        used = workers_ai.calls_today(con, target_date) - (used_before or 0)
    ok = sum(1 for p in parts if "\n✅ " in p)
    details = ("Ничего не записано и не отправлено семьям.\n"
               f"Модель: {trial_model()}, ревьюер: {trial_review_model()}. Вызовов: {used}.\n\n"
               + "\n\n".join(parts))
    if len(details) > TELEGRAM_LIMIT:
        details = details[:TELEGRAM_LIMIT] + "\n…"
    title = f"🧪 Тестовый подбор Workers AI: подобрано {ok} из {len(chosen)}"
    send("info", title, details)
    print(title)
    print(details)
    return details


if __name__ == "__main__":
    sys.exit("run through game_preparation.py trial")
