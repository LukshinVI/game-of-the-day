# Игра дня

Персональный сервис, который ежедневно подбирает безопасную развивающую игру для ребёнка с учётом возраста, истории игр и обратной связи пользователя. Проект также используется как практический стенд для AI/agent-систем и production automation.

## Статус проекта

### Current production

Delivery:

```text
Daily automation → Google Sheets → Google Apps Script → VK
```

Feedback:

```text
VK → Cloudflare Worker → Queue → Apps Script save_raw → Google Sheets
                           ↓
                    Feedback Agent v2
                           ↓
                 Workers AI / Gemma
                           ↓
              Apps Script save_analysis → Google Sheets
```

Этот production path остаётся активным до контролируемого cutover.

### Active migration — #27

Текущий P0: **полностью вывести Google Sheets / Apps Script из runtime и сделать VPS/SQLite основным persistence/delivery контуром. Cloudflare остаётся.**

VPS-контур уже подготовлен: проект развёрнут в `/srv/game-of-the-day`, SQLite создан, исторические данные импортированы, delivery CLI существует. Он ещё не является production source of truth/scheduler.

Целевая схема:

```text
Game workflow
    ↓
VPS / SQLite
    ↓
VPS delivery + systemd
    ↓
VK
    ↓ feedback
Cloudflare Worker → Queue → Workers AI
    ↓
VPS Feedback API
    ↓
SQLite
```

Migration route:

```text
#28 send-today + recovery + idempotency
→ #29 VPS scheduler

#30 VPS Feedback API
→ #31 Cloudflare → VPS persistence

#28 + #29 + #30 + #31
→ #32 production cutover / disable Google runtime
→ stability period
→ archive/remove Google legacy
```

#28 и #30 могут выполняться параллельно. #32 — единственная точка production cutover.

## После migration

Архитектурное упрощение #26 временно paused, чтобы не смешивать refactor со сменой runtime/storage boundary.

```text
#27 Google → VPS/SQLite
→ #26 architecture simplification
→ #14 request feedback UX
→ #7 clarification
→ #8 personalization
```

Параллельный reliability track: #9 AI quota / recovery / graceful degradation.

Следующий product layer: #22 selectable development focus → #23 child development track → #24 goals/progress. Telegram #21 остаётся отдельным channel expansion. #20 — cross-cutting tenant/child/channel constraint.

- [Product roadmap](docs/product-roadmap.md)
- [Архитектура](docs/architecture.md) — source of truth по runtime и migration.
- [GitHub Issues](../../issues) — backlog и execution state.

## Product vision

```text
ежедневная полезная игра
→ продукт слушает пользователя
→ персональный подбор
→ трек развития ребёнка
→ история целей и прогресса
```

Продукт не является медицинским или диагностическим инструментом.

## Правила игры

Игра должна соответствовать возрасту, быть безопасной, занимать примерно 5–10 минут, не повторять ранее использованную механику без явного запроса, иметь проверяемый источник и учитывать выбранный development focus, когда он появится. Age и safety — hard constraints.

## Feedback Agent v2

```text
feedback.text
→ deterministic segmentation + provenance
→ AI classification
→ deterministic validation
→ deterministic policy
→ guarded target linking
```

LLM не создаёт evidence spans; metadata — context, не evidence; неподтверждённый target остаётся `UNSPECIFIED`; persistent state живёт вне LLM. Production model: `@cf/google/gemma-4-26b-a4b-it`.

Ключевой reliability principle: **AI failure != product failure**. Raw feedback сохраняется до AI, а Cloudflare Queue является durable boundary для медленного processing.

## Бесплатность

Hard requirement: **0 ₽ без отдельного явного решения пользователя**. Никакой автоматической платной эскалации при исчерпании free quota.

## Engineering principles

- deterministic code владеет state, guardrails, provenance, retries и side effects;
- LLM используется только для semantic/ambiguous задач;
- structured AI output валидируется приложением;
- raw user input сохраняется до AI;
- idempotency/retry/recovery проектируются явно;
- product state отделён от delivery channel;
- не добавлять agents/frameworks без конкретной проблемы;
- secrets не хранятся в Git;
- merge, deploy и production verification — разные стадии готовности.