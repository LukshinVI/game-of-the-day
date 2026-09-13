# Игра дня

Персональный сервис, который ежедневно подбирает безопасную развивающую игру для ребёнка с учётом возраста, истории игр и обратной связи пользователя. Проект также используется как практический стенд для разработки AI/agent-систем и production automation.

## Статус проекта

### Production delivery

```text
Daily automation
→ Google Sheets
→ Google Apps Script
→ VK
```

Работает end-to-end.

### Production feedback

```text
VK message_new
→ Cloudflare Worker
→ validation + TEST/PROD routing
→ Cloudflare Queue
→ Apps Script Feedback API: save_raw
→ Google Sheets K/L
→ Feedback Agent v2
→ Cloudflare Workers AI / Gemma
→ Apps Script Feedback API: save_analysis
→ Google Sheets M/N
```

13.09.2026 подтверждён полный PROD smoke через Queue: raw feedback сохранился, Gemma корректно классифицировала semantic signals, analysis сохранился в журнал.

Ключевой принцип:

> **AI failure != product failure**

Raw feedback сохраняется до AI. Медленный inference больше не выполняется внутри HTTP `waitUntil()` — для durable execution используется Cloudflare Queue.

## Текущая работа

Текущая активная задача — **#26 Simplify project structure and reduce architectural weight**.

Feedback Agent v2 production-ready, но после его развития Worker стал чрезмерно раздроблен для масштаба продукта. Перед следующими product features проводится behavior-preserving архитектурное упрощение.

Текущий маршрут:

```text
#26 architecture simplification
→ #14 request feedback UX
→ #7 clarification workflow
→ #8 personalization
```

Параллельный reliability track:

```text
#9 AI quota / recovery / graceful degradation
```

Следующий продуктовый слой:

```text
#22 selectable development focus
→ #23 child development track
→ #24 goals & progress
```

Telegram (#21) — следующий полноценный channel expansion. #20 остаётся сквозным ограничением: новый state должен быть tenant-, child- и channel-aware без преждевременной большой миграции.

- [Product roadmap](docs/product-roadmap.md) — продуктовая эволюция и зависимости.
- [Архитектура](docs/architecture.md) — source of truth по текущей и целевой архитектуре.
- [GitHub Issues](../../issues) — backlog и статусы задач.

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

Игра должна:

- соответствовать возрасту;
- быть безопасной;
- занимать примерно 5–10 минут;
- не повторять ранее использованную механику без явного запроса;
- иметь проверяемый первичный источник;
- учитывать выбранный development focus, когда он появится;
- не использовать мелкие опасные предметы, острые/хрупкие или токсичные материалы, горячие предметы/жидкости и сценарии с риском падения.

Age и safety — hard constraints.

## Feedback Agent v2

```text
feedback.text
→ deterministic segmentation + provenance
→ AI classification of existing segments
→ deterministic validation
→ deterministic policy
→ guarded target linking where applicable
```

Ключевые свойства:

- LLM не создаёт evidence spans;
- metadata — context, не evidence;
- неподтверждённый target остаётся `UNSPECIFIED`;
- extraction не создаёт durable preference;
- clarification/product decisions принадлежат application layer.

Текущая production model: `@cf/google/gemma-4-26b-a4b-it`.

## Reliability

Cloudflare Queue используется как durable boundary для feedback processing.

Текущие настройки:

- `max_batch_size = 1`;
- `max_retries = 2`;
- `retry_delay = 60`;
- `max_concurrency = 1`.

Оставшаяся работа #9:

- AVAILABLE / BUDGET_LOW / UNAVAILABLE;
- quota-aware policy;
- recovery после exhausted retries;
- pending feedback reprocessing;
- UX graceful degradation.

## Бесплатность

Hard requirement проекта: **0 ₽ без отдельного явного решения пользователя**.

Используются бесплатные quotas Cloudflare Workers, Queues и Workers AI. Исчерпание бесплатной квоты должно приводить к controlled failure/pending state, а не к автоматическому платному overage.

## Каналы и product state

VK — текущий production channel.

Целевая граница:

```text
channel connection
→ tenant/family
→ recipient
→ child profile
→ shared product state
```

Channel — transport, а не product identity. История, feedback, preferences, goals и progress должны быть общими для ребёнка независимо от VK/Telegram.

## Engineering principles

- deterministic code — state, guardrails, provenance, retries, side effects;
- LLM — только semantic/ambiguous задачи;
- structured AI output валидируется приложением;
- persistent state живёт вне LLM;
- raw user input сохраняется до AI;
- idempotency и retry проектируются явно;
- не добавлять agents/frameworks без конкретной проблемы;
- архитектура должна соответствовать масштабу продукта;
- product state отделён от delivery channel;
- secrets не хранятся в Git.

## Структура репозитория

```text
game-of-the-day/
├── docs/
│   ├── architecture.md
│   └── product-roadmap.md
├── src/
│   └── sendGameOfTheDay.js
├── worker/
│   ├── src/
│   │   ├── index.js
│   │   ├── feedbackApi.js
│   │   └── agents/
│   │       ├── feedbackAgent.js
│   │       ├── feedbackPolicy.js
│   │       └── feedback/...
│   ├── evals/
│   ├── package.json
│   ├── wrangler.jsonc
│   └── wrangler.evals.jsonc
├── README.md
└── .gitignore
```

`experiments/` используется локально и намеренно не входит в production repository structure.
