import { analyzeFeedback } from './agents/feedbackAgent.js';

const VK_GROUP_ID = 123456789;

export default {
    async fetch(request, env, ctx) {
        if (request.method !== 'POST') {
            return new Response(
                'webhook-ok',
                { status: 200 }
            );
        }

        let event;

        try {
            event = await request.json();
        } catch {
            console.log(
                'Rejected callback: invalid JSON'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        /*
         * VK использует confirmation
         * при первоначальном подключении
         * Callback URL.
         */
        if (
            event.type ===
            'confirmation'
        ) {
            return new Response(
                env.VK_CONFIRMATION_CODE,
                { status: 200 }
            );
        }

        /*
         * Принимаем события только
         * от нашего сообщества
         * и только с корректным secret.
         */
        if (
            event.secret !==
                env.VK_CALLBACK_SECRET ||
            event.group_id !==
                VK_GROUP_ID
        ) {
            console.log(
                'Rejected callback'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        /*
         * Сейчас нас интересуют
         * только входящие сообщения.
         */
        if (
            event.type !==
            'message_new'
        ) {
            console.log(
                'Ignored event:',
                event.type
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        const message =
            event.object?.message;

        if (!message) {
            console.log(
                'Ignored: message is missing'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        const peerId =
            String(message.peer_id);

        let environment;

        if (
            peerId ===
            env.VK_TEST_PEER_ID
        ) {
            environment = 'TEST';
        } else if (
            peerId ===
            env.VK_PROD_PEER_ID
        ) {
            environment = 'PROD';
        } else {
            console.log(
                'Ignored unknown peer'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        const feedbackText =
            typeof message.text ===
                'string'
                ? message.text.trim()
                : '';

        console.log(
            'Feedback received:',
            {
                environment,
                hasText:
                    feedbackText.length > 0
            }
        );

        /*
         * Пустое сообщение
         * не является обратной связью,
         * пригодной для анализа.
         */
        if (!feedbackText) {
            console.log(
                'Ignored empty feedback'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        /*
         * TEST никогда не пишет
         * данные в PROD Google Sheet.
         *
         * Пока тестовый peer используется
         * только для проверки ingress.
         */
        if (
            environment === 'TEST'
        ) {
            console.log(
                'TEST feedback accepted'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        /*
         * Для PROD отвечаем VK сразу.
         *
         * Сохранение ОС и AI-анализ
         * выполняются после ответа
         * через waitUntil().
         *
         * Это уменьшает риск того,
         * что VK сочтёт callback
         * неуспешным из-за долгого AI.
         */
        ctx.waitUntil(
            processProductionFeedback(
                env,
                event,
                message,
                feedbackText
            )
        );

        return new Response(
            'ok',
            { status: 200 }
        );
    }
};

async function processProductionFeedback(
    env,
    event,
    message,
    feedbackText
) {
    /*
     * Сначала строим стабильный ID
     * входящего VK-события.
     *
     * Приоритет:
     * 1. callback event_id;
     * 2. peer_id + message.id;
     * 3. peer_id +
     *    conversation_message_id.
     *
     * Без idempotency key RAW ОС
     * не сохраняем: иначе повторная
     * доставка VK может связаться
     * уже с другой игрой.
     */
    const eventId =
        buildFeedbackEventId(
            event,
            message
        );

    if (!eventId) {
        console.log(
            'Feedback processing skipped:',
            'event id is missing'
        );

        return;
    }

    let rawResult;

    /*
     * ВАЖНО:
     *
     * сначала сохраняем RAW ОС,
     * и только потом вызываем AI.
     *
     * AI failure != feedback loss.
     */
    try {
        rawResult =
            await callFeedbackApi(
                env,
                {
                    action: 'save_raw',
                    eventId,
                    text: feedbackText
                }
            );
    } catch (error) {
        console.log(
            'Raw feedback save failed:',
            safeErrorMessage(error)
        );

        return;
    }

    if (
        !rawResult ||
        rawResult.ok !== true
    ) {
        console.log(
            'Raw feedback rejected:',
            rawResult?.error ??
                'UNKNOWN_ERROR'
        );

        return;
    }

    /*
     * Если VK повторно прислал
     * уже полностью обработанное событие,
     * AI повторно не вызываем.
     *
     * Если RAW уже сохранён,
     * но analysis раньше упал,
     * processed=false и мы можем
     * безопасно повторить AI.
     */
    if (
        rawResult.duplicate === true &&
        rawResult.processed === true
    ) {
        console.log(
            'Duplicate feedback already processed:',
            {
                row: rawResult.row
            }
        );

        return;
    }

    if (
        !rawResult.game ||
        typeof rawResult.game !==
            'object'
    ) {
        console.log(
            'Feedback processing stopped:',
            'game context is missing'
        );

        return;
    }

    let analysis;

    try {
        analysis =
            await analyzeFeedback(
                env,
                {
                    game:
                        rawResult.game,

                    feedback: {
                        text:
                            feedbackText
                    }
                }
            );
    } catch (error) {
        /*
         * RAW уже находится
         * в K/L Google Sheet.
         *
         * M/N остаются пустыми.
         * Значит feedback можно
         * обработать повторно позже.
         */
        console.log(
            'Feedback AI failed:',
            safeErrorMessage(error)
        );

        return;
    }

    try {
        const analysisResult =
            await callFeedbackApi(
                env,
                {
                    action:
                        'save_analysis',

                    row:
                        rawResult.row,

                    analysis
                }
            );

        if (
            !analysisResult ||
            analysisResult.ok !== true
        ) {
            console.log(
                'Feedback analysis save rejected:',
                analysisResult
                    ?.error ??
                    'UNKNOWN_ERROR'
            );

            return;
        }

        console.log(
            'Feedback processed:',
            {
                row:
                    rawResult.row,

                decision:
                    analysis.decision
            }
        );
    } catch (error) {
        console.log(
            'Feedback analysis save failed:',
            safeErrorMessage(error)
        );
    }
}

function buildFeedbackEventId(
    event,
    message
) {
    if (
        event.event_id !== undefined &&
        event.event_id !== null &&
        String(
            event.event_id
        ).trim() !== ''
    ) {
        return String(
            event.event_id
        );
    }

    if (
        message.id !== undefined &&
        message.id !== null
    ) {
        return [
            'message',
            message.peer_id,
            message.id
        ].join(':');
    }

    if (
        message
            .conversation_message_id !==
            undefined &&
        message
            .conversation_message_id !==
            null
    ) {
        return [
            'conversation',
            message.peer_id,
            message
                .conversation_message_id
        ].join(':');
    }

    return null;
}

async function callFeedbackApi(
    env,
    payload
) {
    if (!env.FEEDBACK_API_URL) {
        throw new Error(
            'FEEDBACK_API_URL_NOT_CONFIGURED'
        );
    }

    if (
        !env.FEEDBACK_API_SECRET
    ) {
        throw new Error(
            'FEEDBACK_API_SECRET_NOT_CONFIGURED'
        );
    }

    const response =
        await fetch(
            env.FEEDBACK_API_URL,
            {
                method: 'POST',

                headers: {
                    'Content-Type':
                        'application/json'
                },

                body: JSON.stringify({
                    ...payload,
                    secret:
                        env
                            .FEEDBACK_API_SECRET
                })
            }
        );

    if (!response.ok) {
        throw new Error(
            'FEEDBACK_API_HTTP_' +
            response.status
        );
    }

    let result;

    try {
        result =
            await response.json();
    } catch {
        throw new Error(
            'FEEDBACK_API_INVALID_JSON'
        );
    }

    return result;
}

function safeErrorMessage(
    error
) {
    if (
        error &&
        typeof error.message ===
            'string'
    ) {
        return error.message;
    }

    return 'UNKNOWN_ERROR';
}