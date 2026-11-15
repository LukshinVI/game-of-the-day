import assert from 'node:assert/strict';
import worker from '../src/index.js';

// Every private VK dialog reaches the host with its sender; group chats do not.
const env = {
    VK_GROUP_ID: '42',
    VK_CALLBACK_SECRET: 's',
    VK_TEST_PEER_ID: '1',
    sent: [],
    FEEDBACK_QUEUE: { send: async (body) => env.sent.push(body) },
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
    assert.deepEqual(env.sent, []);
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

    // A message queued on the ingress queue before the change is still passed on.
    await worker.queue({ queue: 'game-of-the-day-feedback-prod', messages: [{ body: {
        eventId: 'e0', feedbackText: 'старое', address: '777' } }] }, env);
    assert.equal(env.stored.at(-1).event_id, 'e0');

    // An event queued before this change (no address) is stored without one.
    await worker.queue({ queue: 'game-of-the-day-feedback-prod', messages: [{ body: { eventId: 'old', feedbackText: 'x' } }] }, env);
    assert.equal('address' in env.stored.at(-1), false);

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

    // The Worker makes no model call: the retired enrichment queue is not consumed here.
    await assert.rejects(
        worker.queue({ queue: 'game-of-the-day-feedback-enriched-prod', messages: [{ body: {} }] }, env),
        /UNKNOWN_QUEUE/
    );
} finally {
    console.log = silent;
}

console.log('routing tests: ok');
