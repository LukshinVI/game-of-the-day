"""Selection for one child: prompt -> model -> gate -> safety review -> corrective
retries, and the chain primary model -> fallback model -> library. ChildRun carries the
child's profile, theme, model and attempt log explicitly: no module state."""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.preparation import alerts, backend, fallback, gate, layout, review, skills, store, workers_ai
from src.preparation import mechanics as mechanic_tags


ROOT = Path(__file__).resolve().parents[2]

PROMPT_PATH = ROOT / "src" / "preparation" / "prompts" / "game_selector.md"

MOSCOW = ZoneInfo("Europe/Moscow")

# The first attempt is often spent on a formatting rejection, so two independent validation
# rejections used to exhaust the daily run. One more bounded corrective attempt is allowed.
MAX_ATTEMPTS = 3

# The age range the prompt and the candidate schema are designed for.
MAX_AGE_MONTHS_SUPPORTED = json.loads(backend.SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]["age_months"]["maximum"]


def child_age_months(on_date, birth_date=None):
    """Completed months of age on `on_date` (a date or YYYY-MM-DD string).

    The birth date comes from CHILD_BIRTH_DATE (YYYY-MM-DD, or YYYY-MM meaning the
    1st of that month) so the age advances on its own and stays out of the repository.
    A missing or invalid value is an error: guessing an age would silently select
    activities for the wrong developmental stage.
    """
    raw = birth_date if birth_date is not None else os.environ.get("CHILD_BIRTH_DATE", "")
    raw = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            birth = date.fromisoformat(raw + "-01")
        else:
            birth = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            "CHILD_BIRTH_DATE must be set as YYYY-MM-DD or YYYY-MM, got "
            f"{raw!r}"
        ) from None

    if isinstance(on_date, str):
        on_date = date.fromisoformat(on_date)

    months = (on_date.year - birth.year) * 12 + (on_date.month - birth.month)
    if on_date.day < birth.day:
        months -= 1

    if months < 0:
        raise ValueError(f"CHILD_BIRTH_DATE {raw} is after {on_date.isoformat()}")

    return months


def build_prompt(history, age_months, today=None, profile=None, theme=None):
    base = PROMPT_PATH.read_text(encoding="utf-8")

    context = {
        "today": today or datetime.now(MOSCOW).date().isoformat(),
        "child_age_months": age_months,
        **skills.prompt_context(age_months, history),
        "mechanic_tags": {tag: label for tag, label in mechanic_tags.TAGS.items() if tag not in mechanic_tags.recent(history)},
        "recent_mechanic_tags": mechanic_tags.recent(history),
        **({"child_profile": profile} if profile else {}),
        **({"theme": theme} if theme else {}),
        "previous_games": history,
    }

    return (
        base
        + "\n\nINPUT CONTEXT:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )


@dataclass
class ChildRun:
    """What one preparation carries through the chain, passed explicitly: no module
    state, so two preparations can never see each other's child.

    profile: the child's profile from feedback (src/feedback/profile.py), with
        required_next when code requires a skill today;
    theme: the seasonal theme offered today (src/preparation/themes.py), or None;
    model: the fallback model once the chain moved to it, else None (the configured one);
    alert_on_exhaustion: select_candidate() alerts when its attempts run out, unless the
        caller (obtain_candidate, the reserve) alerts once for the whole chain;
    log: one record per model or reviewer attempt, persisted to model_runs;
    notes: the run's collector of owner alerts (one message per night run), or None to
        send each alert at once;
    sources: while the independent model runs, the verified sources it may cite (it has
        no web search), else None;
    down: shared by every child of one run: {"claude": reason} once Claude did not answer
        (quota, timeout, outage), so the rest of the run goes straight to the next link.
    """
    profile: dict = field(default_factory=dict)
    theme: dict | None = None
    model: str | None = None
    alert_on_exhaustion: bool = True
    log: list = field(default_factory=list)
    notes: list | None = None
    sources: list | None = None
    down: dict = field(default_factory=dict)


class Exhausted(RuntimeError):
    """The model answered every time and the gate or the reviewer rejected every answer:
    the provider works, so this is never an outage."""


def build_retry_prompt(original_prompt, candidate, errors):
    feedback = {
        "rejected_candidate": candidate,
        "deterministic_validation_errors": errors,
    }
    if review.tiers_enabled() and errors and all(e.startswith("safety review:") for e in errors):
        # Only the safety reviewer objected: repair this game instead of inventing a new
        # one with new risks (production 2026-11-11: six new games, six new objections).
        return (
            original_prompt
            + "\n\nIMPORTANT: The candidate below passed every check except the safety review.\n"
            + "Keep the same activity, skill, mechanic and source. Change only what is needed "
              "to remove each safety problem listed (replace or drop the risky material or "
              "step), keep the rest as it is, and return the corrected candidate.\n\n"
            + json.dumps(feedback, ensure_ascii=False, indent=2)
        )

    return (
        original_prompt
        + "\n\nIMPORTANT: A previous candidate was rejected.\n"
        + "Correct every validation error below. "
          "Do not repeat the rejected result. "
          "Re-check all original requirements before returning a new candidate.\n\n"
        + json.dumps(feedback, ensure_ascii=False, indent=2)
    )


def send_failure_alert(errors, notes=None):
    details = (
        f"Подготовка игры завершилась ошибкой после {MAX_ATTEMPTS} попыток.\n"
        "Игра не была записана в production SQLite.\n"
        "Отправка в VK не выполнялась.\n\n"
        "Ошибки последней проверки:\n- "
        + "\n- ".join(errors)
    )
    alerts.send_ops_notification("critical", "Подготовка игры не удалась", details, notes)


def record_attempt(run, meta, attempt, outcome, errors, started):
    run.log.append({
        **meta,
        "attempt": attempt,
        "outcome": outcome,
        "errors": errors,
        "wall_ms": int((time.monotonic() - started) * 1000),
    })


def select_candidate(history, target_date, birth_date=None, age_months=None, run=None):
    """Propose, validate and retry. age_months overrides the birth-date computation
    (used by the selection evals, which describe cases by age). run carries the
    child's profile, theme, model and attempt log (a fresh ChildRun when None)."""
    run = ChildRun() if run is None else run
    if age_months is None:
        age_months = child_age_months(target_date, birth_date)
    original_prompt = build_prompt(history, age_months, today=str(target_date),
                                   profile=run.profile, theme=run.theme)
    if run.sources is not None:
        original_prompt += workers_ai.sources_note(run.sources)
    prompt = original_prompt
    last_errors = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print()
        print(f"=== ПОПЫТКА {attempt}/{MAX_ATTEMPTS} ===")

        started = time.monotonic()
        meta = {}
        try:
            candidate = layout.with_rendered_message(backend.run_model(prompt, model=run.model, meta=meta))
        except Exception as exc:
            record_attempt(run, meta, attempt, "error", [f"{type(exc).__name__}: {str(exc)[:300]}"], started)
            raise
        errors = gate.validate_candidate(candidate, history, age_months)
        if not errors and run.sources is not None:
            errors = [e for e in [workers_ai.source_error(candidate, run.sources)] if e]
        record_attempt(run, meta, attempt, "rejected" if errors else "pass", errors, started)
        required = (run.profile or {}).get("required_next")
        if not errors and required and candidate.get("skill") != required["skill"]:
            if required["direction"] == "theme":
                errors = [f"the family chose {required['skill']} as the theme of the month: today's game "
                          f"must train {required['skill']}, not {candidate.get('skill')}"]
            else:
                errors = [f"after the parent's feedback on «{required['after']}» the next game must train "
                          f"{required['skill']} ({required['direction']} than before), not {candidate.get('skill')}"]
            run.log[-1]["outcome"], run.log[-1]["errors"] = "rejected", errors
        independent = run.sources is not None
        if not errors and (review.tiers_enabled() or independent):
            if independent:
                # Another provider's game is reviewed on that provider, and never sent
                # unreviewed: no answer from the reviewer means the library.
                name = workers_ai.review_model()
                problems, fixes, meta = review.review_tiered(
                    candidate, age_months, runner=workers_ai.review_runner(name), name=name)
            else:
                problems, fixes, meta = review.review_tiered(candidate, age_months)
            if independent and (meta is None or meta["error"]):
                if meta is not None:
                    run.log.append({**meta, "attempt": attempt, "outcome": "error", "errors": [meta["error"]]})
                raise review.ReviewUnavailable(
                    f"ревьюер {name} не ответил: {meta['error'] if meta else 'выключен'}")
            if meta is not None:
                run.log.append({**meta, "attempt": attempt, "outcome": "rejected" if problems else
                                ("error" if meta["error"] else "pass"),
                                "errors": [f"safety review{' (rule)' if p in meta.get('by_rules', ()) else ''}: {p}"
                                           for p in problems]
                                + [f"safety fix: {f}" for f in fixes if not problems]
                                or ([meta["error"]] if meta["error"] else [])})
            errors = [f"safety review: {p}" for p in problems]
            if not errors and fixes:
                for fix in fixes:
                    print(f"SAFETY FIX: {fix}")
                candidate = layout.with_rendered_message(review.with_fixes(candidate, fixes))
        elif not errors:
            problems, meta = review.review(candidate, age_months)
            if meta is not None:
                run.log.append({**meta, "attempt": attempt, "outcome": "rejected" if problems else
                                ("error" if meta["error"] else "pass"),
                                "errors": [f"safety review: {p}" for p in problems] or
                                ([meta["error"]] if meta["error"] else [])})
            errors = [f"safety review: {p}" for p in problems]

        print()
        print("=== КАНДИДАТ ===")
        print(json.dumps(candidate, ensure_ascii=False, indent=2))

        print()
        print("=== ПРОВЕРКА ===")

        if not errors:
            print("PASS")
            return candidate, attempt

        for error in errors:
            print(f"FAIL: {error}")

        last_errors = errors

        if attempt < MAX_ATTEMPTS:
            print()
            print(
                "Кандидат отклонён. "
                "Выполняется корректирующая попытка."
            )
            prompt = build_retry_prompt(
                original_prompt,
                candidate,
                errors,
            )

    if run.alert_on_exhaustion:
        send_failure_alert(last_errors, run.notes)

    raise Exhausted(
        f"Подготовка игры не удалась после {MAX_ATTEMPTS} попыток: "
        + "; ".join(last_errors)[:300]
    )


def obtain_candidate(history, target_date, child, run=None):
    """Primary model -> fallback model -> independent model (another provider, when
    configured) -> library. Returns (candidate, attempts, origin, reason).

    Raises (after one failure alert) only when nothing fits at all.
    """
    run = ChildRun() if run is None else run
    birth_date = child["birth_date"] if child else None
    child_id = child["id"] if child else None
    reason = None

    with store.connect_db() as con:
        budget = fallback.daily_budget_usd()
        spent = fallback.spent_today(con, target_date)
    if budget is not None and spent >= budget:
        reason = f"дневной бюджет модели исчерпан (${spent:.2f} из ${budget:.2f})"

    if reason is None and run.down.get("claude"):
        reason = f"Claude не отвечает в этом прогоне ({run.down['claude']})"

    # The chain alerts once, at its end, not after each model's attempts.
    saved = run.alert_on_exhaustion
    run.alert_on_exhaustion = False
    try:
        if reason is None:
            down = False
            try:
                candidate, attempts = select_candidate(history, target_date, birth_date, run=run)
                return candidate, attempts, "model", None
            except Exception as exc:  # noqa: BLE001 - every failure moves down the chain
                quota, down = fallback.is_quota_error(exc), fallback.is_outage(exc)
                what = "лимит подписки исчерпан" if quota else "Claude не ответил" if down else "основная модель не справилась"
                reason = f"{what}: {type(exc).__name__}: {str(exc)[:200]}"
                if down:
                    run.down["claude"] = what
                print(f"FALLBACK: {reason}", file=sys.stderr)

            if not down and fallback.fallback_model():
                run.model = fallback.fallback_model()
                try:
                    candidate, attempts = select_candidate(history, target_date, birth_date, run=run)
                    return candidate, attempts, "fallback_model", f"{run.model}; {reason}"
                except Exception as exc:  # noqa: BLE001
                    reason += f"; резервная модель {run.model}: {str(exc)[:200]}"
                    if fallback.is_outage(exc):
                        run.down["claude"] = "Claude не ответил"
                    print(f"FALLBACK: {reason}", file=sys.stderr)
                finally:
                    run.model = None

        if workers_ai.enabled():
            with store.connect_db() as con:
                calls, cap = workers_ai.calls_today(con, target_date), workers_ai.daily_calls()
                run.sources = workers_ai.known_sources(con)
            if calls >= cap:
                reason = (reason or "") + f"; независимая модель: дневной лимит вызовов ({calls} из {cap})"
            elif run.sources:
                run.model = workers_ai.model()
                try:
                    candidate, attempts = select_candidate(history, target_date, birth_date, run=run)
                    return candidate, attempts, "independent_model", f"{run.model}; {reason}"
                except Exception as exc:  # noqa: BLE001
                    reason = (reason or "") + f"; независимая модель {run.model}: {str(exc)[:200]}"
                    print(f"FALLBACK: {reason}", file=sys.stderr)
                finally:
                    run.model, run.sources = None, None
            run.sources = None
    finally:
        run.alert_on_exhaustion = saved

    age = child_age_months(target_date, birth_date)
    with store.connect_db() as con:
        candidate = fallback.library_candidate(con, history, age, target_date, child_id)
    if candidate is not None:
        return candidate, 0, "library", reason

    send_failure_alert([reason or "неизвестная причина", "в библиотеке нет подходящей игры"], run.notes)
    raise RuntimeError(f"Подготовка игры не удалась, резерва нет: {reason}")
