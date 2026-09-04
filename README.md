# Игра дня

Персональный сервис, который ежедневно подбирает безопасную развивающую игру для ребёнка с учётом возраста, истории игр и обратной связи пользователя.

Проект одновременно используется как практический учебный стенд для разработки AI/agent-систем: от детерминированной автоматизации и webhook-архитектуры до специализированных AI-агентов, structured output, eval, provenance validation, Harness и model routing.

## Статус проекта

### Работает в production

**MVP delivery:**

```text
Daily automation
→ Google Sheets
→ Google Apps Script
→ VK
```

**Production feedback loop:**

```text
VK message_new
→ Cloudflare Worker
→ validation + TEST/PROD routing
→ Apps Script Feedback API: save_raw
→ Google Sheets K/L
→ Feedback Agent
→ Apps Script Feedback API: save_analysis
→ Google Sheets M/N
```

04.09.2026 production E2E подтверждён на реальном сообщении обратной связи.

Raw feedback сохраняется **до AI inference**, поэтому ошибка или недоступность модели не приводит к потере пользовательской ОС.

### Текущая разработка

Текущий P0 — **Feedback Agent v1.3: evidence-bound target / provenance validation**.

Production run выявил, что v1.2 мог вернуть target из metadata игры, даже если этого target не было в `feedback.text`. Поэтому свободный `target` заменяется на `target_text`, который должен быть доказуемым фрагментом исходного текста и проверяется детерминированно приложением.

```text
feedback.text
→ LLM semantic extraction
→ target_text | null
→ deterministic provenance validation
→ resolveDecision()
→ optional later normalization to domain mechanic
```

После стабилизации v1.3 следующий маршрут:

```text
#14 Request feedback UX
→ #7 Clarification workflow
→ #8 Personalization
→ #10 Harness extraction
```

- [Архитектура проекта](docs/architecture.md) — source of truth по текущей и целевой архитектуре.
- [GitHub Issues](../../issues) — backlog, приоритеты, текущие и выполненные задачи.

## Правила продукта

Игра должна:

- соответствовать текущему возрасту ребёнка;
- быть безопасной;
- развивать один из целевых навыков: речь, координацию или мелкую моторику;
- занимать примерно 5–10 минут;
- не повторять ранее использованную механику без явного запроса;
- иметь проверяемый первичный источник;
- не использовать мелкие опасные предметы, острые/хрупкие или токсичные материалы, горячие предметы/жидкости и сценарии с риском падения.

При сомнении в безопасности игра отклоняется.

## Архитектура — кратко

```mermaid
flowchart TD
    A[Daily automation] --> S[(Google Sheets)]
    S --> G[Apps Script delivery]
    G -->|VK API| V[VK]
    V -->|feedback| W[Cloudflare Worker]
    W --> R{TEST / PROD routing}
    R -->|PROD| API[Apps Script Feedback API]
    API -->|save_raw| S
    API --> F[Feedback Agent]
    F --> AI[Cloudflare Workers AI]
    F -->|validated analysis| API
    API -->|save_analysis| S
```

Целевое направление:

```mermaid
flowchart LR
    E[Events] --> H[Harness]
    H --> FA[Feedback Agent]
    H --> SA[Selection Agent]
    H --> SAFE[Safety Agent]
    FA --> MR[Model Router]
    SA --> MR
    SAFE --> RULES[Deterministic safety rules]
    SAFE -. semantic checks .-> MR
    MR --> CF[Cloudflare Workers AI]
    MR --> GM[Gemini Free Tier]
    MR --> O[Other free models]
```

Подробные sequence/state diagrams, границы ответственности и текущее состояние компонентов находятся в [`docs/architecture.md`](docs/architecture.md).

## Feedback Agent

### v1.2

Использовал контракт:

```json
{
  "observations": [
    {
      "reaction": "POSITIVE | NEGATIVE",
      "target": "string | null"
    }
  ]
}
```

Eval baseline: **11/12 PASS**. Этого было достаточно для запуска production feedback loop, но production выявил недостаточную защиту от metadata-grounding.

### v1.3

Целевой контракт:

```json
{
  "observations": [
    {
      "reaction": "POSITIVE | NEGATIVE",
      "target_text": "exact substring from feedback.text | null"
    }
  ]
}
```

Application layer проверяет provenance до принятия decision.

```text
observations = []
→ NO_PREFERENCE

есть target_text = null
→ NEED_CLARIFICATION

все target_text подтверждены исходным текстом
→ PREFERENCE_DETECTED
```

Нормализация `target_text → mechanic` не входит в v1.3 и рассматривается как отдельный последующий этап.

## Надёжность feedback flow

- raw feedback сохраняется до AI;
- AI failure не удаляет K/L;
- повтор уже обработанного callback не вызывает повторный inference;
- event/message ID используется для idempotency;
- TEST peer не изменяет PROD journal;
- Worker отвечает VK до завершения фоновой обработки;
- strict product decision принимается deterministic application layer.

Ключевой принцип:

> **AI failure != product failure**

Недоступность модели не должна останавливать доставку ежедневной игры; необработанная обратная связь сохраняется и может быть обработана позже.

## Бесплатность AI

Для проекта действует ограничение: **используем только бесплатные AI-решения / free-tier quotas**.

Текущий runtime — Cloudflare Workers AI. В backlog находится сравнение с Gemini Free Tier и другими бесплатными кандидатами по:

- semantic accuracy;
- provenance/grounding accuracy;
- ambiguity handling;
- structured output reliability;
- latency;
- расходу free quota;
- operational complexity.

## Структура репозитория

```text
game-of-the-day/
├── docs/
│   └── architecture.md
├── src/
│   └── sendGameOfTheDay.js
├── worker/
│   ├── src/
│   │   ├── index.js
│   │   └── agents/
│   │       └── feedbackAgent.js
│   ├── evals/
│   │   ├── feedbackCases.js
│   │   ├── runFeedbackEvals.js
│   │   └── index.js
│   ├── package.json
│   ├── package-lock.json
│   ├── wrangler.jsonc
│   └── wrangler.evals.jsonc
├── README.md
└── .gitignore
```

`experiments/` используется локально для отдельных исследований и намеренно не считается частью production-структуры репозитория.

## Security

Секреты и API-токены не хранятся в Git.

- VK access token — Google Apps Script Script Properties;
- VK Callback secret — Cloudflare secret;
- TEST/PROD peer IDs — Cloudflare secrets;
- Apps Script Feedback API защищён отдельным shared secret;
- Feedback API URL хранится в runtime config/secret;
- внешние AI API keys, если появятся, — только secret store провайдера/runtime.

Worker не должен логировать peer ID, полный текст пользовательского сообщения или секреты.

## Engineering principles

- deterministic code для строгих правил, state, provenance и guardrails;
- LLM для семантических и неоднозначных задач;
- structured AI output валидируется приложением;
- metadata может быть context, но не evidence;
- LLM не является памятью системы;
- модели передаётся минимально необходимый context;
- retry и idempotency проектируются явно;
- raw user input сохраняется до AI processing;
- AI-компоненты сравниваются на собственном eval-наборе;
- основной продукт продолжает работать при деградации AI.
