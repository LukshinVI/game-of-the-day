import { analyzeFeedback } from './agents/feedbackAgent.js';

// Community ID is deployment-specific: set with `wrangler secret put VK_GROUP_ID`.
// If it is missing, Number(undefined) is NaN and every callback is rejected (fail closed).

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
                Number(env.VK_GROUP_ID)
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
            withReactionLabel(
                typeof message.text ===
                    'string'
                    ? message.text.trim()
                    : '',
                message.attachments
            );

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
        typeof payload?.eventId === 'string'
            ? payload.eventId
            : '';

    const feedbackText =
        typeof payload?.feedbackText === 'string'
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
         * Invalid input cannot be repaired by retry.
         * Return successfully so Queue acknowledges it.
         */
        return;
    }

    /*
     * Production feedback ingress.
     *
     * From this point persistence and enrichment are handled
     * by the VPS storage consumer. Google Feedback API is no
     * longer part of the production feedback path.
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
    } catch (error) {
        throw new Error(
            'RAW_STORAGE_QUEUE_FAILED: ' +
            safeErrorMessage(error)
        );
    }

    console.log(
        'RAW feedback queued for VPS storage:',
        { eventId }
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


/*
 * Stickers, photos, videos and voice messages carry no text. They are still a
 * reaction to the game: pass a label on, never the media itself (photos of
 * children are not downloaded or stored). The host asks once per game for a
 * few words and spends no model call on a label.
 */
export function withReactionLabel(
    text,
    attachments
) {
    const labels = {
        sticker: '[стикер]',
        photo: '[фото]',
        video: '[видео]',
        audio_message: '[голосовое]',
        doc: '[файл]'
    };

    const found =
        (Array.isArray(attachments) ? attachments : [])
            .map((item) => labels[item?.type])
            .filter(Boolean);

    const unique =
        [...new Set(found)];

    return [text, ...unique]
        .filter(Boolean)
        .join(' ')
        .trim();
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
