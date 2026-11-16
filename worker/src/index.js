// Community ID is deployment-specific: set with `wrangler secret put VK_GROUP_ID`.
// If it is missing, Number(undefined) is NaN and every callback is rejected (fail closed).

import { handleWatch, noteCallback, scheduled, Watch } from './watch.js';

export { Watch };

export default {
    scheduled,

    async fetch(request, env, ctx) {
        // The host's pulse and canary receipts (src/ops/heartbeat.py), not VK.
        if (new URL(request.url).pathname.startsWith('/watch/')) {
            return handleWatch(request, env);
        }

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

        if (env.WATCH && ctx?.waitUntil) {
            ctx.waitUntil(noteCallback(env));
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

        /*
         * Every private dialog with the community is passed on with its sender:
         * the host decides whether it is feedback from a subscribed family or a
         * new family onboarding (src/bot/vk_dialog.py). Group chats are ignored.
         */
        if (
            !/^[0-9]+$/.test(peerId) ||
            Number(peerId) >= 2000000000
        ) {
            console.log(
                'Ignored non-private peer'
            );

            return new Response(
                'ok',
                { status: 200 }
            );
        }

        const environment =
            peerId === env.VK_TEST_PEER_ID
                ? 'TEST'
                : 'PROD';

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

        // A reaction button under a game sends its label as text and
        // {"r": code, "g": game id} as payload; the host reads the payload.
        const buttonPayload =
            typeof message.payload === 'string' &&
            message.payload.length <= 200
                ? message.payload
                : null;

        const audio = voiceLink(message.attachments);

        // "Ответить" on a game: the host attaches the feedback to that exact game.
        const replyTo = repliedId(message);

        /*
         * Straight to the host's storage queue: there is no ingress queue any more
         * (it only passed messages on, fed the retired analysis agent, and cost
         * ~10 s, too slow for a button).
         */
        try {
            await storeFeedback(env, {
                eventId,
                feedbackText,
                address: peerId,
                ...(buttonPayload ? { payload: buttonPayload } : {}),
                ...(audio ? { audio: audio.url, audioSeconds: audio.seconds } : {}),
                ...(replyTo ? { replyTo } : {})
            });
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
    }
};

async function storeFeedback(
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

    const address =
        typeof payload?.address === 'string' &&
        /^[0-9]+$/.test(payload.address)
            ? payload.address
            : null;

    const buttonPayload =
        typeof payload?.payload === 'string' &&
        payload.payload.length <= 200
            ? payload.payload
            : null;

    const audio =
        typeof payload?.audio === 'string' &&
        payload.audio.startsWith('https://') &&
        payload.audio.length <= 1000
            ? payload.audio
            : null;

    const replyTo =
        typeof payload?.replyTo === 'string' &&
        /^[0-9]{1,20}$/.test(payload.replyTo)
            ? payload.replyTo
            : null;

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
     * From this point the VPS storage consumer persists the message
     * and src/feedback/insights.py analyses it, the same as Telegram
     * feedback. The Worker makes no model call.
     */
    try {
        await env
            .FEEDBACK_STORAGE_QUEUE
            .send({
                version: 'feedback-storage-v1',
                event_id: eventId,
                channel: 'vk',
                raw_text: feedbackText,
                ...(address ? { address } : {}),
                ...(buttonPayload ? { payload: buttonPayload } : {}),
                ...(audio ? {
                    audio_url: audio,
                    audio_seconds: Number(payload.audioSeconds) || 0
                } : {}),
                ...(replyTo ? { reply_to: replyTo } : {})
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

/*
 * A voice message's audio link for the host, which may transcribe it (GAME_VOICE) and
 * keeps only the text. The Worker itself never downloads it.
 */
export function voiceLink(attachments) {
    const voice = (Array.isArray(attachments) ? attachments : [])
        .find((item) => item?.type === 'audio_message');
    const message = voice?.audio_message;
    const url = message?.link_ogg || message?.link_mp3;
    return typeof url === 'string' && url.startsWith('https://')
        ? { url, seconds: Number(message.duration) || 0 }
        : null;
}

/*
 * The id of the community message the parent replied to, as a string, or null.
 * Only the id is passed on, never the quoted text.
 */
export function repliedId(message) {
    const id = message?.reply_message?.id;
    return Number.isInteger(id) && id > 0 ? String(id) : null;
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
