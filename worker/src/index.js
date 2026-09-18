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

        console.log(
            'Incoming VK peer:',
            {
                peerId
            }
        );

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
                'TEST feedback routed to feedback pipeline'
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
                'Queue delivery:',
                {
                    queue: batch.queue,
                    eventId:
                        payload?.eventId ??
                        payload?.event_id
                }
            );

            try {
                if (
                    batch.queue ===
                    'game-of-the-day-feedback-prod'
                ) {
                    await processQueuedFeedback(
                        env,
                        payload
                    );
                } else if (
                    batch.queue ===
                    'game-of-the-day-feedback-enriched-prod'
                ) {
                    await processEnrichedFeedback(
                        env,
                        payload
                    );
                } else {
                    throw new Error(
                        'UNKNOWN_QUEUE: ' +
                        batch.queue
                    );
                }
            } catch (error) {
                console.log(
                    'Queue processing failed:',
                    {
                        queue: batch.queue,
                        error:
                            safeErrorMessage(error)
                    }
                );

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

    /*
     * Migration shadow path.
     *
     * Persist RAW feedback through Cloudflare Queue -> VPS
     * independently of the legacy Feedback API.
     */
    try {
        await env
            .FEEDBACK_STORAGE_QUEUE
            .send({
                version: 'feedback-storage-v1',
                event_id: eventId,
                channel: 'vk',
                raw_text: feedbackText
            });

        console.log(
            'RAW feedback storage event queued:',
            { eventId }
        );
    } catch (error) {
        console.log(
            'RAW feedback storage queue publish failed:',
            safeErrorMessage(error)
        );
    }

    let rawResult;

    try {
        rawResult =
            await callFeedbackApi(
                env,
                '/v1/feedback/raw',
                {
                    event_id: eventId,
                    channel: 'vk',
                    raw_text: feedbackText
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
        !['created', 'existing'].includes(
            rawResult.status
        )
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
        rawResult.status === 'existing' &&
        rawResult.processed === true
    ) {
        console.log(
            'Duplicate feedback already processed:',
            {
                feedbackId:
                    rawResult.feedback_id
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
         * либо Feedback API вернул
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
                '/v1/feedback/analysis',
                {
                    event_id: eventId,
                    channel: 'vk',
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
        !['updated', 'existing'].includes(
            analysisResult.status
        )
    ) {
        throw new Error(
            'ANALYSIS_SAVE_REJECTED: ' +
            (
                analysisResult?.error ??
                'UNKNOWN_ERROR'
            )
        );
    }

    /*
     * Shadow-write the completed feedback event to the
     * storage Queue.
     *
     * During migration the existing Feedback API remains
     * authoritative. A storage Queue failure must therefore
     * not retry an event that has already been persisted.
     */
    const storageEnvelope = {
        version: 'feedback-storage-v1',
        event_id: eventId,
        channel: 'vk',
        raw_text: feedbackText,
        analysis
    };

    try {
        await env
            .FEEDBACK_STORAGE_QUEUE
            .send(storageEnvelope);

        console.log(
            'Feedback storage event queued:',
            {
                eventId
            }
        );
    } catch (error) {
        console.log(
            'Feedback storage shadow publish failed:',
            {
                eventId,
                error:
                    safeErrorMessage(error)
            }
        );
    }

    console.log(
        'Feedback processed:',
        {
            feedbackId:
                rawResult.feedback_id,

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


async function processEnrichedFeedback(
    env,
    payload
) {
    const eventId =
        typeof payload?.event_id === 'string'
            ? payload.event_id
            : '';

    const feedbackText =
        typeof payload?.raw_text === 'string'
            ? payload.raw_text.trim()
            : '';

    const game =
        payload?.game &&
        typeof payload.game === 'object'
            ? payload.game
            : null;

    if (
        payload?.version !== 'feedback-enriched-v1' ||
        !eventId ||
        !feedbackText ||
        !game
    ) {
        throw new Error(
            'ENRICHED_FEEDBACK_INVALID_PAYLOAD'
        );
    }

    let analysis;

    try {
        analysis = await analyzeFeedback(
            env,
            {
                game,
                feedback: {
                    text: feedbackText
                }
            }
        );
    } catch (error) {
        throw new Error(
            'FEEDBACK_AI_FAILED: ' +
            safeErrorMessage(error)
        );
    }

    try {
        await env
            .FEEDBACK_STORAGE_QUEUE
            .send({
                version: 'feedback-storage-v1',
                event_id: eventId,
                channel: 'vk',
                raw_text: feedbackText,
                analysis
            });
    } catch (error) {
        throw new Error(
            'ANALYSIS_STORAGE_QUEUE_FAILED: ' +
            safeErrorMessage(error)
        );
    }

    console.log(
        'Enriched feedback analyzed and queued for storage:',
        {
            eventId,
            decision: analysis.decision,
            model: analysis.meta?.model,
            modelCalls:
                analysis.meta?.model_calls
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
    path,
    payload
) {
    if (!env.FEEDBACK_API_URL) {
        throw new Error(
            'FEEDBACK_API_URL_NOT_CONFIGURED'
        );
    }

    if (!env.FEEDBACK_API_SECRET) {
        throw new Error(
            'FEEDBACK_API_SECRET_NOT_CONFIGURED'
        );
    }

    const baseUrl =
        env.FEEDBACK_API_URL
            .replace(/\/+$/, '');

    const requestUrl = baseUrl + path;

    console.log(
        'Feedback API request:',
        {
            url: requestUrl
        }
    );

    const response = await fetch(
        requestUrl,
        {
            method: 'POST',

            headers: {
                'Content-Type':
                    'application/json',

                'Authorization':
                    'Bearer ' +
                    env.FEEDBACK_API_SECRET
            },

            body: JSON.stringify(payload)
        }
    );

    const responseText = await response.text();

    console.log(
        'Feedback API response:',
        {
            url: requestUrl,
            status: response.status,
            contentType:
                response.headers.get('content-type')
        }
    );

    let result;

    try {
        result = responseText
            ? JSON.parse(responseText)
            : {};
    } catch {
        if (!response.ok) {
            throw new Error(
                'FEEDBACK_API_HTTP_' +
                response.status +
                ': NON_JSON_RESPONSE'
            );
        }

        throw new Error(
            'FEEDBACK_API_INVALID_JSON: HTTP_' +
            response.status
        );
    }

    if (!response.ok) {
        throw new Error(
            'FEEDBACK_API_HTTP_' +
            response.status +
            ': ' +
            (
                result?.error ??
                result?.message ??
                'UNKNOWN_ERROR'
            )
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
