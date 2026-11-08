import assert from 'node:assert/strict';
import worker from '../src/index.js';

// Every private VK dialog reaches the host with its sender; group chats do not.
const env = {
    VK_GROUP_ID: '42',
    VK_CALLBACK_SECRET: 's',
    VK_TEST_PEER_ID: '1',
    sent: [],
    FEEDBACK_QUEUE: { send: async (body) => env.sent.push(body) },
    FEEDBACK_STORAGE_QUEUE: { send: async (body) => env.stored.push(body) },
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
    assert.deepEqual(env.sent, [{ eventId: 'e1', feedbackText: 'Здравствуйте', address: '777' }]);

    await worker.fetch(callback(2000000005, 'в беседе', 2), env);
    assert.equal(env.sent.length, 1, 'group chats are ignored');

    response = await worker.fetch(callback(1, 'тест', 3), env);
    assert.equal(env.sent.at(-1).address, '1');

    await worker.queue({ queue: 'game-of-the-day-feedback-prod', messages: [{ body: env.sent[0] }] }, env);
    assert.deepEqual(env.stored, [{
        version: 'feedback-storage-v1', event_id: 'e1', channel: 'vk', raw_text: 'Здравствуйте', address: '777'
    }]);

    // An event queued before this change (no address) is stored without one.
    await worker.queue({ queue: 'game-of-the-day-feedback-prod', messages: [{ body: { eventId: 'old', feedbackText: 'x' } }] }, env);
    assert.equal('address' in env.stored.at(-1), false);
} finally {
    console.log = silent;
}

console.log('routing tests: ok');
