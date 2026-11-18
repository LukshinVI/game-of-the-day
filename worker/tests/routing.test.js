import assert from 'node:assert/strict';
import worker from '../src/index.js';

// Every private VK dialog reaches the host with its sender; group chats do not.
const env = {
    VK_GROUP_ID: '42',
    VK_CALLBACK_SECRET: 's',
    VK_TEST_PEER_ID: '1',
    FEEDBACK_STORAGE_QUEUE: { send: async (body) => {
        if (env.storageDown) throw new Error('down');
        env.stored.push(body);
    } },
    stored: []
};

function callback(peerId, text, id) {
    return new Request('https://worker.example/', {
        method: 'POST',
        body: JSON.stringify({
            type: 'message_new',
            secret: 's',
            group_id: 42,
            event_id: `e${id}`,
            object: { message: { peer_id: peerId, text, id } }
        })
    });
}

const silent = console.log;
console.log = () => {};
try {
    let response = await worker.fetch(callback(777, 'Здравствуйте', 1), env);
    assert.equal(response.status, 200);
    // Straight to the host's storage queue, no ingress hop.
    assert.deepEqual(env.stored, [{
        version: 'feedback-storage-v1', event_id: 'e1', channel: 'vk', raw_text: 'Здравствуйте', address: '777'
    }]);

    await worker.fetch(callback(2000000005, 'в беседе', 2), env);
    assert.equal(env.stored.length, 1, 'group chats are ignored');

    response = await worker.fetch(callback(1, 'тест', 3), env);
    assert.equal(env.stored.at(-1).address, '1');

    // The storage queue refuses: VK gets a 500 and delivers the event again.
    env.storageDown = true;
    response = await worker.fetch(callback(777, 'ещё раз', 4), env);
    assert.equal(response.status, 500);
    env.storageDown = false;

    // A reaction button: its payload travels with the label to the host.
    const press = new Request('https://worker.example/', {
        method: 'POST',
        body: JSON.stringify({
            type: 'message_new', secret: 's', group_id: 42, event_id: 'e9',
            object: { message: { peer_id: 777, text: '👍 Понравилось', id: 9, payload: '{"r":"liked","g":65}' } }
        })
    });
    await worker.fetch(press, env);
    assert.equal(env.stored.at(-1).payload, '{"r":"liked","g":65}');

    // A voice message: the label for the text, the link for the host.
    const voiceCall = new Request('https://worker.example/', {
        method: 'POST',
        body: JSON.stringify({
            type: 'message_new', secret: 's', group_id: 42, event_id: 'e10',
            object: { message: { peer_id: 777, text: '', id: 10, attachments: [{ type: 'audio_message',
                audio_message: { duration: 7, link_ogg: 'https://psv4.userapi.com/a.ogg' } }] } }
        })
    });
    await worker.fetch(voiceCall, env);
    assert.equal(env.stored.at(-1).raw_text, '[голосовое]');
    assert.equal(env.stored.at(-1).audio_url, 'https://psv4.userapi.com/a.ogg');
    assert.equal(env.stored.at(-1).audio_seconds, 7);

    // "Ответить" on a game: only the replied message's id travels, never its text.
    const replyCall = new Request('https://worker.example/', {
        method: 'POST',
        body: JSON.stringify({
            type: 'message_new', secret: 's', group_id: 42, event_id: 'e11',
            object: { message: { peer_id: 777, text: 'мячики были классные', id: 11,
                reply_message: { id: 133, text: 'игра дня', from_id: -42 } } }
        })
    });
    await worker.fetch(replyCall, env);
    assert.equal(env.stored.at(-1).reply_to, '133');
    assert.equal(JSON.stringify(env.stored.at(-1)).includes('игра дня'), false);
    await worker.fetch(callback(777, 'без ответа', 12), env);
    assert.equal(env.stored.at(-1).reply_to, undefined);

    // A wrong or missing secret is rejected, and so is everything when the Worker has no
    // secret configured (fail closed).
    const before = env.stored.length;
    const forged = (secret, workerEnv) => worker.fetch(new Request('https://worker.example/', {
        method: 'POST',
        body: JSON.stringify({ type: 'message_new', ...(secret === undefined ? {} : { secret }), group_id: 42,
            event_id: `forged-${secret}`, object: { message: { peer_id: 777, text: 'x', id: 99 } } })
    }), workerEnv);
    await forged('t', env);
    await forged('ss', env);
    await forged(undefined, env);
    await forged(undefined, { ...env, VK_CALLBACK_SECRET: undefined });
    assert.equal(env.stored.length, before, 'forged callbacks never reach the queue');

    // The Worker consumes no queue and makes no model call.
    assert.equal(worker.queue, undefined);
} finally {
    console.log = silent;
}

console.log('routing tests: ok');
