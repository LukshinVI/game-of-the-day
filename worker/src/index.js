import { analyzeFeedback } from './agents/feedbackAgent.js';

const VK_GROUP_ID = 123456789;

export default {
    async fetch(request, env) {
        if (request.method !== 'POST') {
            return new Response(
                'webhook-ok',
                { status: 200 }
            );
        }

        let event;

        try {
            event =
                await request.json();
        } catch {
            console.log(
                'Rejected callback: invalid JSON'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        if (
            event.type ===
            'confirmation'
        ) {
            return new Response(
                env.VK_CONFIRMATION_CODE,
                { status: 200 }
            );
        }

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

        if (!feedbackText) {
            console.log(
                'Ignored empty feedback'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

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

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        try {
            await env
                .FEEDBACK_QUEUE
                .send({
                    eventId,
                    feedbackText
                });

            console.log(
                'Feedback queued:',
                {
                    eventId
                }
            );
        } catch (error) {
            console.log(
                'Feedback queue publish failed:',
                safeErrorMessage(error)
            );

            /*
             * Queue не приняла сообщение.
             *
             * Не отвечаем VK успешным 200:
             * иначе событие будет потеряно.
             *
             * При повторной доставке eventId
             * останется тем же, а дальнейшая
             * обработка идемпотентна.
             */
            return new Response(
                'retry',
                { status: 500 }
            );
        }

        return new Response(
            'ok',
            { status: 200 }
        );
    },

    async queue(
        batch,
        env
    ) {
        for (
            const message
            of batch.messages
        ) {
            const payload =
                message.body;

            console.log(
                'Feedback queue delivery:',
                {
                    eventId:
                        payload?.eventId
                }
            );

            try {
                await processQueuedFeedback(
                    env,
                    payload
                );
            } catch (error) {
                console.log(
                    'Feedback queue processing failed:',
                    safeErrorMessage(error)
                );

                /*
                 * Пробрасываем ошибку наружу.
                 *
                 * Cloudflare Queue сама
                 * выполнит retry согласно
                 * wrangler.jsonc.
                 */
                throw error;
            }
        }
    }
};

async function processQueuedFeedback(
    env,
    payload
) {
    const eventId =
        typeof payload?.eventId ===
            'string'
            ? payload.eventId
            : '';

    const feedbackText =
        typeof payload?.feedbackText ===
            'string'
            ? payload.feedbackText.trim()
            : '';

    if (
        !eventId ||
        !feedbackText
    ) {
        console.log(
            'Queued feedback rejected:',
            'invalid payload'
        );

        /*
         * Некорректное сообщение
         * повтором не исправится.
         * Возвращаемся успешно,
         * чтобы Queue его ack-нула.
         */
        return;
    }

    let rawResult;

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
        throw new Error(
            'RAW_SAVE_FAILED: ' +
            safeErrorMessage(error)
        );
    }

    if (
        !rawResult ||
        rawResult.ok !== true
    ) {
        throw new Error(
            'RAW_SAVE_REJECTED: ' +
            (
                rawResult?.error ??
                'UNKNOWN_ERROR'
            )
        );
    }

    if (
        rawResult.duplicate === true &&
        rawResult.processed === true
    ) {
        console.log(
            'Duplicate feedback already processed:',
            {
                row:
                    rawResult.row
            }
        );

        return;
    }

    if (
        !rawResult.game ||
        typeof rawResult.game !==
            'object'
    ) {
        /*
         * Здесь retry бессмысленен:
         * feedback не удалось связать
         * с игрой.
         *
         * RAW уже либо сохранён,
         * либо Apps Script вернул
         * существующее событие.
         */
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
         * В Queue это теперь
         * полноценная retryable ошибка.
         *
         * RAW уже сохранён.
         * Повторная доставка снова
         * получит ту же строку через
         * eventId и попробует AI ещё раз.
         */
        throw new Error(
            'FEEDBACK_AI_FAILED: ' +
            safeErrorMessage(error)
        );
    }

    let analysisResult;

    try {
        analysisResult =
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
    } catch (error) {
        throw new Error(
            'ANALYSIS_SAVE_FAILED: ' +
            safeErrorMessage(error)
        );
    }

    if (
        !analysisResult ||
        analysisResult.ok !== true
    ) {
        throw new Error(
            'ANALYSIS_SAVE_REJECTED: ' +
            (
                analysisResult?.error ??
                'UNKNOWN_ERROR'
            )
        );
    }

    console.log(
        'Feedback processed:',
        {
            row:
                rawResult.row,

            decision:
                analysis.decision,

            model:
                analysis.meta?.model,

            modelCalls:
                analysis.meta
                    ?.model_calls
        }
    );
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