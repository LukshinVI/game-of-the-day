#!/usr/bin/env python3
"""Daily preparation: the entry point (systemd units, the ops bot and manual runs).

    game_preparation.py prepare [--date D] [--child ID] [--dry-run]
    game_preparation.py reserve import REPORT... | reserve top-up [--date D]
    game_preparation.py shadow

One run prepares today's game for every active child, one after another: the chain
(chain.py) proposes and checks it, store.py writes it with its deliveries, alerts.py
tells the owner once per run. The rest of the package: backend.py (the model call),
gate.py (the deterministic checks), layout.py (the message), review.py (the safety
reviewer), fallback.py (budget and the library).
"""

import argparse
import os
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.children import active_children, has_children  # noqa: E402
from src.feedback import month_theme  # noqa: E402
from src.feedback import profile as child_profile  # noqa: E402
from src.preparation import fallback, review, themes  # noqa: E402
from src.preparation import alerts, backend, chain, gate, layout, store  # noqa: E402

def save_shadow(candidate):
    backend.SHADOW_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(chain.MOSCOW)
    timestamp = now.strftime("%Y%m%d-%H%M%S")

    archive_path = backend.SHADOW_DIR / f"candidate-{timestamp}.json"

    archive_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return archive_path


def validate_target_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise RuntimeError(
            "Дата должна быть в формате YYYY-MM-DD"
        ) from exc


def prepare_run(target_date, dry_run=False, only_child=None):
    """Prepare today's game for every active child (or the single child).

    only_child limits the run to one child id (a manual run for a new or test child
    must not prepare, and then deliver early, everyone else's game).

    Children are independent: a failure for one is reported and does not stop
    the others; the run fails at the end if any child failed.
    """
    target_date = validate_target_date(target_date)

    with store.connect_db() as con:
        multi_child = has_children(con)
        # A child nobody receives games for (all recipients paused) costs a model
        # call for nothing: skip it.
        children = [c for c in active_children(con, include_test=only_child is not None) if c["recipients"]]
    if only_child is not None:
        children = [c for c in children if c["id"] == only_child]
        if not children:
            raise RuntimeError(f"no active child with recipients and id {only_child}")

    # The games (and the candidate schema) cover 0-36 months; an older child would
    # fail every attempt and alert daily. Skip with a note instead.
    grown = [c for c in children if chain.child_age_months(target_date, c["birth_date"]) > chain.MAX_AGE_MONTHS_SUPPORTED]
    for child in grown:
        print(f"{child['name']} (id {child['id']}): старше {chain.MAX_AGE_MONTHS_SUPPORTED} мес., игры не готовятся.")
    children = [c for c in children if c not in grown]

    # Single-child mode only before the first child exists; once children are
    # configured, "nobody to prepare for" means nothing to do, not the env child.
    plan = children if multi_child else [None]
    if not plan:
        print("Нет активных детей с получателями: готовить нечего.")
    failures = []
    run_started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Per-child alerts are collected and sent as one message at the end of the run.
    notes = []
    # One provider outage per run: once Claude did not answer, the other children skip it.
    down = {}
    for child in plan:
        label = f"{child['name']} (id {child['id']})" if child else "single-child mode"
        print()
        print(f"##### {label} #####")
        try:
            prepare_for_child(target_date, child, dry_run, notes=notes, down=down)
        except Exception as exc:  # noqa: BLE001 - reported per child, re-raised below
            print(f"ERROR: {exc}", file=sys.stderr)
            failures.append(f"{label}: {exc}")

    reviewed = alerts.review_summary(target_date, run_started) if not dry_run and review.tiers_enabled() else None
    message = alerts.run_summary(len(plan), notes, failures, reviewed)
    if message:
        alerts.notify_now(*message)

    if failures:
        raise RuntimeError("; ".join(failures))


def prepare_for_child(target_date, child, dry_run=False, notes=None, down=None):
    child_id = child["id"] if child else None

    # Проверяем ДО вызова модели — повторный запуск ничего не тратит.
    existing = store.game_for_date(target_date, child_id)

    if existing:
        print("=== ИГРА УЖЕ ПОДГОТОВЛЕНА ===")
        print(f"Дата:      {existing['game_date']}")
        print(f"ID:        {existing['legacy_id']}")
        print(f"Название:  {existing['title']}")
        print(f"Delivery:  {existing['status']}")
        print()
        print("Модель: НЕ ЗАПУСКАЛАСЬ")
        print("SQLite: НЕ ИЗМЕНЁН")
        print("ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")
        return

    history = store.load_history(child_id)

    print(f"Исторических игр: {len(history)}")
    print(f"Целевая дата: {target_date}")

    run = child_run(target_date, child, history, notes, down)

    try:
        candidate, attempts, origin, origin_reason = chain.obtain_candidate(history, target_date, child, run)
    finally:
        store.persist_runs(target_date, child_id, dry_run, run.log)
    return finish_child(target_date, child, dry_run, notes, candidate, attempts, origin, origin_reason)


def child_run(target_date, child, history, notes=None, down=None):
    """The child's ChildRun: profile, the required skill (difficulty answer or theme of
    the month) and today's seasonal theme."""
    child_id = child["id"] if child else None
    run = chain.ChildRun(notes=notes, down={} if down is None else down)
    with store.connect_db() as con:
        run.profile = child_profile.load(con, child_id)
        required = child_profile.required_next(con, child_id, history)
        if required is None:  # a difficulty answer comes first; then the theme of the month
            try:
                required = month_theme.required(con, child_id, history, date.fromisoformat(str(target_date)),
                                                chain.child_age_months(target_date, child["birth_date"] if child else None))
            except Exception as exc:  # noqa: BLE001 - a theme is optional; never fail the day over it
                print(f"WARN: month theme unavailable: {type(exc).__name__}", file=sys.stderr)
        try:
            run.theme = themes.context(con, child_id, date.fromisoformat(str(target_date)),
                                       chain.child_age_months(target_date, child["birth_date"] if child else None))
        except Exception as exc:  # noqa: BLE001 - a theme is optional; never fail the day over it
            print(f"WARN: theme unavailable: {type(exc).__name__}", file=sys.stderr)
            run.theme = None
    if run.theme:
        print(f"Тема дня предложена: {run.theme['label']}")
    if required:
        run.profile = {**run.profile, "required_next": required}
    if run.profile:
        print(f"Профиль: {json.dumps(run.profile, ensure_ascii=False)}")
    return run


def finish_child(target_date, child, dry_run, notes, candidate, attempts, origin, origin_reason):
    print(f"Источник: {fallback.describe(origin, origin_reason)}")

    channels = ", ".join(r["channel"] for r in child["recipients"]) if child else "vk"

    print()
    print("=== ПЛАН ЗАПИСИ ===")
    print(f"Дата:      {target_date}")
    print(f"Название:  {candidate['title']}")
    print(f"Возраст:   {candidate['age_months']} мес.")
    print(f"Навык:     {candidate['skill']}")
    print(f"Delivery:  {channels} / pending")

    if dry_run:
        print()
        print("=== DRY RUN ===")
        print(f"Попыток модели: {attempts}")
        print("Production SQLite: НЕ ИЗМЕНЁН")
        print("ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")
        return

    game_id, legacy_id = store.persist_candidate(
        candidate,
        target_date,
        child,
        origin=origin,
        origin_reason=origin_reason,
    )

    print()
    print("=== ИГРА ПОДГОТОВЛЕНА ===")
    print(f"ID:        {legacy_id}")
    print(f"DB ID:     {game_id}")
    print(f"Дата:      {target_date}")
    print(f"Название:  {candidate['title']}")
    print("Delivery:  pending")
    print("ОТПРАВКА НЕ ВЫПОЛНЯЛАСЬ")

    alerts.send_preparation_success_alert(
        legacy_id,
        target_date,
        candidate,
        attempts,
        child["name"] if child else None,
        origin=origin,
        origin_reason=origin_reason,
        notes=notes,
    )


def shadow_run():
    history = store.load_history()

    print(f"Loaded {len(history)} historical games.")

    age_months = chain.child_age_months(datetime.now(chain.MOSCOW).date())
    original_prompt = chain.build_prompt(history, age_months)
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, chain.MAX_ATTEMPTS + 1):
        print()
        print(f"=== ATTEMPT {attempt}/{chain.MAX_ATTEMPTS} ===")

        candidate = layout.with_rendered_message(backend.run_model(prompt))
        errors = gate.validate_candidate(candidate, history, age_months)

        print()
        print("=== CANDIDATE ===")
        print(json.dumps(candidate, ensure_ascii=False, indent=2))

        print()
        print("=== VALIDATION ===")

        if not errors:
            print("PASS")

            path = save_shadow(candidate)

            print()
            print("=== SHADOW RESULT ===")
            print(f"Attempts: {attempt}")
            print(f"Saved: {path}")
            print("Production database: NOT MODIFIED")
            print("VK delivery: NOT ATTEMPTED")
            return

        for error in errors:
            print(f"FAIL: {error}")

        last_errors = errors

        if attempt < chain.MAX_ATTEMPTS:
            print()
            print("Candidate rejected. Preparing a corrective retry.")
            prompt = chain.build_retry_prompt(
                original_prompt,
                candidate,
                errors,
            )

    print()
    print("=== PREPARATION FAILED ===")
    print(f"Attempts exhausted: {chain.MAX_ATTEMPTS}/{chain.MAX_ATTEMPTS}")
    print("Production database: NOT MODIFIED")
    print("VK delivery: NOT ATTEMPTED")

    chain.send_failure_alert(last_errors)

    raise RuntimeError(
        f"Подготовка игры не удалась после {chain.MAX_ATTEMPTS} попыток: "
        + "; ".join(last_errors)[:300]
    )


RESERVE_MIN = 3
RESERVE_MONTHS_AHEAD = 2
RESERVE_MAX_NEW_PER_RUN = 6


def reserve_import(paths):
    """Add validated candidates from selection-eval reports to the warm reserve."""
    added = 0
    with store.connect_db() as con:
        for path in paths:
            for record in json.loads(Path(path).read_text(encoding="utf-8")):
                candidate = record.get("candidate")
                if not record.get("passed") or not isinstance(candidate, dict):
                    continue
                candidate = layout.with_rendered_message(candidate)
                errors = gate.validate_candidate(candidate, [], candidate["age_months"])
                if errors:
                    print(f"skip {candidate.get('title')}: {errors}")
                    continue
                if fallback.add_reserve(con, candidate, f"evals:{Path(path).name}"):
                    added += 1
                    print(f"+ {candidate['age_months']} мес. {candidate['skill']}: {candidate['title']}")
    print(f"added {added}")


def reserve_top_up(target_date):
    """Keep at least RESERVE_MIN library games for each child's age over the next months.

    Generates the missing ones with the configured model under the same validator and
    within the daily budget; at most RESERVE_MAX_NEW_PER_RUN per run.
    """
    target_date = validate_target_date(target_date)
    with store.connect_db() as con:
        children = [c for c in active_children(con) if c["recipients"]]
    made = 0
    for child in children:
        history = store.load_history(child["id"])
        base = chain.child_age_months(target_date, child["birth_date"])
        for age in range(base, min(base + RESERVE_MONTHS_AHEAD, chain.MAX_AGE_MONTHS_SUPPORTED) + 1):
            with store.connect_db() as con:
                have = fallback.eligible_count(con, history, age, target_date)
                budget, spent = fallback.daily_budget_usd(), fallback.spent_today(con, target_date)
            print(f"{child['name']}: {age} мес. — в резерве {have}")
            synthetic = list(history)
            while have < RESERVE_MIN and made < RESERVE_MAX_NEW_PER_RUN:
                if budget is not None and spent >= budget:
                    print("budget spent; stopping")
                    return made
                # A reserve gap is not an outage: no alert when the attempts run out.
                run = chain.ChildRun(alert_on_exhaustion=False)
                try:
                    candidate, _ = chain.select_candidate(synthetic, target_date, age_months=age, run=run)
                except Exception as exc:  # noqa: BLE001 - a reserve gap is not an outage
                    print(f"could not make a reserve game for {age} months: {str(exc)[:200]}")
                    break
                finally:
                    store.persist_runs(target_date, None, False, run.log)
                with store.connect_db() as con:
                    if fallback.add_reserve(con, candidate, os.environ.get("PREPARATION_CLAUDE_MODEL", "model")):
                        made += 1
                        have += 1
                    spent = fallback.spent_today(con, target_date)
                synthetic.append(candidate)
                print(f"+ {age} мес. {candidate['skill']}: {candidate['title']}")
    print(f"reserve: {made} new")
    return made


def main():
    parser = argparse.ArgumentParser(prog="game-preparation")
    sub = parser.add_subparsers(dest="command", required=True)

    reserve = sub.add_parser("reserve", help="warm reserve of validated games for graceful degradation")
    reserve_sub = reserve.add_subparsers(dest="reserve_command", required=True)
    imp = reserve_sub.add_parser("import", help="add passed candidates from selection-eval reports")
    imp.add_argument("paths", nargs="+")
    top = reserve_sub.add_parser("top-up", help="generate missing reserve games for upcoming ages")
    top.add_argument("--date", default=datetime.now(chain.MOSCOW).date().isoformat())

    sub.add_parser("shadow")

    trial = sub.add_parser("trial", help="weekly test run of the independent model, owner only")
    trial.add_argument("--date", default=datetime.now(chain.MOSCOW).date().isoformat())
    trial.add_argument("--children", type=int, default=2)

    prepare = sub.add_parser("prepare")
    prepare.add_argument(
        "--date",
        default=datetime.now(chain.MOSCOW).date().isoformat(),
        help="Целевая дата YYYY-MM-DD",
    )
    prepare.add_argument("--child", type=int, help="only this child id (manual runs)")
    prepare.add_argument(
        "--dry-run",
        action="store_true",
        help="Подобрать и проверить игру без записи в SQLite",
    )

    args = parser.parse_args()

    if args.command == "reserve":
        if args.reserve_command == "import":
            reserve_import(args.paths)
        else:
            reserve_top_up(args.date)
    elif args.command == "shadow":
        shadow_run()
    elif args.command == "trial":
        from src.preparation import independent_trial
        independent_trial.run_trial(args.date, args.children)
    elif args.command == "prepare":
        prepare_run(
            target_date=args.date,
            dry_run=args.dry_run,
            only_child=args.child,
        )


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        print(
            f"ERROR: model call timed out after {backend.MODEL_TIMEOUT_SECONDS} seconds",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
