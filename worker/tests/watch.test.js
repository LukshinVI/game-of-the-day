import assert from 'node:assert/strict';
import worker from '../src/index.js';
import {
    decide, onBeat, onSeen, ago, Watch,
    HOST_SILENT_MS, CHAIN_SILENT_MS, REMIND_MS
} from '../src/watch.js';

const MIN = 60 * 1000;
const T0 = Date.UTC(2026, 10, 16, 12, 0);

// Not configured (no beat yet): no checks, no canary.
{
    const [s, out, canary] = decide({}, T0, true);
    assert.deepEqual(out, []);
    assert.equal(canary, false);
    assert.equal(s.canaryId, undefined);
}

// A beating host and a canary that comes back: silence.
{
    let [s] = onBeat({}, T0);
    let out, canary;
    [s, out, canary] = decide(s, T0 + MIN, true);
    assert.equal(canary, true);
    assert.deepEqual(out, []);
    const id = s.canaryId;
    [s, out] = onSeen(s, id, T0 + 2 * MIN);
    assert.deepEqual(out, []);
    [s] = onBeat(s, T0 + 5 * MIN);
    [s, out, canary] = decide(s, T0 + 6 * MIN, false);
    assert.deepEqual(out, []);
    assert.equal(canary, false);
}

// The host goes silent: one alert, a reminder after REMIND_MS, recovery on the beat.
{
    let [s] = onBeat({}, T0);
    let out;
    [s, out] = decide(s, T0 + HOST_SILENT_MS - MIN, false);
    assert.deepEqual(out, [], 'not yet');
    [s, out] = decide(s, T0 + HOST_SILENT_MS + MIN, false);
    assert.equal(out.length, 1);
    assert.match(out[0], /🔴 Сервер «Игры дня» молчит 16 мин/);
    [s, out] = decide(s, T0 + HOST_SILENT_MS + 10 * MIN, false);
    assert.deepEqual(out, [], 'no repeat before the reminder');
    [s, out] = decide(s, T0 + HOST_SILENT_MS + MIN + REMIND_MS, false);
    assert.equal(out.length, 1);
    assert.match(out[0], /всё ещё молчит/);
    [s, out] = onBeat(s, T0 + 7 * 60 * MIN);
    assert.equal(out.length, 1);
    assert.match(out[0], /🟢 Сервер снова на связи \(молчал 7 ч\)/);
    assert.equal(s.hostDown, false);
}

// The canary does not come back while the host beats: chain alert, then recovery.
{
    let [s] = onBeat({}, T0);
    let out, canary;
    [s, out, canary] = decide(s, T0, true);
    const first = s.canaryId;
    for (let m = 5; m <= 30; m += 5) {
        [s] = onBeat(s, T0 + m * MIN);
        [s, out] = decide(s, T0 + m * MIN, false);
        assert.deepEqual(out, [], `quiet at ${m} min`);
    }
    [s] = onBeat(s, T0 + 35 * MIN);
    [s, out] = decide(s, T0 + 35 * MIN, false);
    assert.equal(out.length, 1);
    assert.match(out[0], /🔴 Цепочка VK → сервер не работает/);
    assert.match(out[0], /сообщений из VK с запуска не было/);
    // The next hour sends a fresh canary even though the old one never arrived.
    [s] = onBeat(s, T0 + 60 * MIN);
    [s, out, canary] = decide(s, T0 + 60 * MIN, true);
    assert.equal(canary, true);
    assert.notEqual(s.canaryId, first);
    [s, out] = onSeen(s, first, T0 + 61 * MIN);
    assert.deepEqual(out, [], 'a stale canary is not a recovery');
    [s, out] = onSeen(s, s.canaryId, T0 + 62 * MIN);
    assert.equal(out.length, 1);
    assert.match(out[0], /🟢 Цепочка VK → сервер снова работает/);
}

// A host that is down does not also raise the chain alert.
{
    let [s] = onBeat({}, T0);
    let out;
    [s] = decide(s, T0, true);
    [s, out] = decide(s, T0 + CHAIN_SILENT_MS + MIN, false);
    assert.equal(out.length, 1);
    assert.match(out[0], /Сервер «Игры дня» молчит/);
}

// No new canary while the last one is still on its way.
{
    let [s] = onBeat({}, T0);
    [s] = decide(s, T0, true);
    const id = s.canaryId;
    [s] = onBeat(s, T0 + 10 * MIN);
    const [, , canary] = decide(s, T0 + 10 * MIN, true);
    assert.equal(canary, false);
    assert.equal(s.canaryId, id);
}

assert.equal(ago(16 * MIN), '16 мин');
assert.equal(ago(90 * MIN), '1,5 ч');

// The Durable Object: the canary goes on the queue, alerts go to Telegram.
{
    const sent = [];
    const queue = [];
    const store = new Map();
    const env = {
        OPS_BOT_TOKEN: 't', OPS_CHAT_ID: '1',
        FEEDBACK_STORAGE_QUEUE: { send: async (body) => { queue.push(body); } }
    };
    const realFetch = globalThis.fetch;
    globalThis.fetch = async (url, init) => { sent.push(JSON.parse(init.body).text); return new Response('{}'); };
    try {
        const watch = new Watch({ storage: { get: async (k) => store.get(k), put: async (k, v) => { store.set(k, v); } } }, env);
        await watch.beat(T0);
        await watch.tick(T0, true);
        assert.deepEqual(queue, [{ version: 'canary-v1', id: `canary-${T0}` }]);
        await watch.seen(`canary-${T0}`, T0 + MIN);
        await watch.tick(T0 + HOST_SILENT_MS + MIN, false);
        assert.equal(sent.length, 1);
        assert.match(sent[0], /молчит/);

        // A queue that refuses the canary is said at once, and only once.
        const down = { ...env, FEEDBACK_STORAGE_QUEUE: { send: async () => { throw new Error('down'); } } };
        const store2 = new Map();
        const w2 = new Watch({ storage: { get: async (k) => store2.get(k), put: async (k, v) => { store2.set(k, v); } } }, down);
        sent.length = 0;
        await w2.beat(T0);
        await w2.tick(T0, true);
        await w2.beat(T0 + 40 * MIN);
        await w2.tick(T0 + 40 * MIN, false);
        assert.equal(sent.length, 1);
        assert.match(sent[0], /не смог положить контрольное сообщение/);
    } finally {
        globalThis.fetch = realFetch;
    }
}

// /watch/ needs the secret; a wrong or missing one is a plain 404.
{
    const calls = [];
    const env = {
        WATCH_SECRET: 'sekret-123',
        WATCH: {
            idFromName: (n) => n,
            get: () => ({ fetch: async (url, init) => { calls.push([new URL(url).pathname, JSON.parse(init.body)]); return new Response('ok'); } })
        }
    };
    const post = (path, auth, body) => new Request(`https://w.example${path}`, {
        method: 'POST', headers: auth ? { Authorization: `Bearer ${auth}` } : {}, body: body ? JSON.stringify(body) : undefined
    });
    assert.equal((await worker.fetch(post('/watch/beat', null), env)).status, 404);
    assert.equal((await worker.fetch(post('/watch/beat', 'sekret-124'), env)).status, 404);
    assert.equal(calls.length, 0);
    assert.equal((await worker.fetch(post('/watch/beat', 'sekret-123'), env)).status, 200);
    assert.equal(calls[0][0], '/beat');
    assert.equal((await worker.fetch(post('/watch/canary', 'sekret-123', { id: 'x' }), env)).status, 400);
    assert.equal((await worker.fetch(post('/watch/canary', 'sekret-123', { id: 'canary-1' }), env)).status, 200);
    assert.deepEqual(calls[1][1].id, 'canary-1');
    assert.equal((await worker.fetch(post('/watch/other', 'sekret-123'), env)).status, 404);
}

console.log('watch tests: ok');
