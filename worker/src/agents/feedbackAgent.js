const ALLOWED_DECISIONS = new Set([
    'PREFERENCE_DETECTED',
    'NEED_CLARIFICATION',
    'NO_PREFERENCE'
]);

const FEEDBACK_SCHEMA = {
    type: 'object',
    properties: {
        decision: {
            type: 'string',
            enum: [
                'PREFERENCE_DETECTED',
                'NEED_CLARIFICATION',
                'NO_PREFERENCE'
            ]
        },
        liked_mechanics: {
            type: 'array',
            items: {
                type: 'string'
            }
        },
        disliked_mechanics: {
            type: 'array',
            items: {
                type: 'string'
            }
        },
        clarification_question: {
            type: ['string', 'null']
        }
    },
    required: [
        'decision',
        'liked_mechanics',
        'disliked_mechanics',
        'clarification_question'
    ],
    additionalProperties: false
};

export async function analyzeFeedback(env, input) {
    const response = await env.AI.run(
        '@cf/meta/llama-3.1-8b-instruct-fast',
        {
            messages: [
                {
                    role: 'system',
                    content: `
Ты Feedback Agent сервиса развивающих игр для ребёнка.

Твоя задача — интерпретировать обратную связь о конкретной игре.

Определи:
1. Какие механики ребёнку понравились.
2. Какие механики ребёнку не понравились.
3. Нужно ли уточнение.

Правила принятия решения.

Проверяй обратную связь строго в следующем порядке:

1. Сначала найди поведенческие признаки интереса или потери интереса
   ребёнка к конкретной механике.

   Сильные признаки интереса:
   - просил повторить действие;
   - самостоятельно повторял действие;
   - продолжал действие;
   - возвращался к действию;
   - явно проявлял интерес или радость во время действия.

   Сильные признаки потери интереса:
   - быстро перестал выполнять конкретное действие;
   - явно отказался продолжать конкретное действие;
   - переключился с конкретной механики на другую.

   Если такой сигнал явно связан с конкретной механикой,
   используй PREFERENCE_DETECTED.

2. Затем проверь прямые словесные указания:
   - понравилось;
   - не понравилось;
   - заинтересовало;
   - надоело;
   и аналогичные выражения.

   Если предпочтение относится к конкретной механике,
   используй PREFERENCE_DETECTED.

3. Используй NO_PREFERENCE, если обратная связь понятна,
   но из неё нельзя сделать вывод о предпочтении механики.
   Например:
   - "Сыграли";
   - "Всё получилось";
   - "Играли пять минут".

4. Используй NEED_CLARIFICATION только если:
   - реакция ребёнка описана, но непонятно, к какой механике она относится;
   - сообщение содержит противоречивые сигналы, которые нельзя разрешить;
   - без дополнительной информации нельзя определить смысл обратной связи.

НЕ используй NEED_CLARIFICATION только потому, что пользователь
не написал напрямую "понравилось" или "не понравилось".

Если ребёнок просил повторить конкретное действие, это достаточный
сигнал интереса к этому действию.

Не задавай общий вопрос вроде "Что понравилось?",
если исходная обратная связь уже содержит поведенческий сигнал.

Не выясняй намерения взрослого. Анализируй реакцию ребёнка.

Допустимые значения decision:
- PREFERENCE_DETECTED
- NEED_CLARIFICATION
- NO_PREFERENCE

Не придумывай предпочтения, которых нет в обратной связи.

Обязательные требования к результату:

Если decision = NEED_CLARIFICATION:
- clarification_question ОБЯЗАТЕЛЬНО должен содержать один непустой короткий вопрос на русском языке;
- clarification_question не может быть null;
- liked_mechanics должен быть пустым массивом;
- disliked_mechanics должен быть пустым массивом.

Перед возвратом результата обязательно проверь:
если выбран NEED_CLARIFICATION и clarification_question отсутствует
или равен null, сформулируй уточняющий вопрос.

          `.trim()
                },
                {
                    role: 'user',
                    content: JSON.stringify(input)
                }
            ],

            response_format: {
                type: 'json_schema',
                json_schema: FEEDBACK_SCHEMA
            }
        }
    );

    const result = response?.response;

    try {
        validateFeedbackResult(result);
    } catch (error) {
        error.feedbackResult = result;
        throw error;
    }

    return result;
}

function validateFeedbackResult(result) {
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
        throw new Error('FEEDBACK_RESULT_INVALID');
    }

    if (!ALLOWED_DECISIONS.has(result.decision)) {
        throw new Error('FEEDBACK_DECISION_INVALID');
    }

    if (!Array.isArray(result.liked_mechanics)) {
        throw new Error('LIKED_MECHANICS_INVALID');
    }

    if (!Array.isArray(result.disliked_mechanics)) {
        throw new Error('DISLIKED_MECHANICS_INVALID');
    }

    const hasPreferences =
        result.liked_mechanics.length > 0 ||
        result.disliked_mechanics.length > 0;

    if (result.decision === 'PREFERENCE_DETECTED') {
        if (!hasPreferences || result.clarification_question !== null) {
            throw new Error('PREFERENCE_RESULT_INCONSISTENT');
        }
    }

    if (result.decision === 'NEED_CLARIFICATION') {
        if (
            hasPreferences ||
            typeof result.clarification_question !== 'string' ||
            result.clarification_question.trim() === ''
        ) {
            throw new Error('CLARIFICATION_RESULT_INCONSISTENT');
        }
    }

    if (result.decision === 'NO_PREFERENCE') {
        if (hasPreferences || result.clarification_question !== null) {
            throw new Error('NO_PREFERENCE_RESULT_INCONSISTENT');
        }
    }
}