/*
 * The watcher outside the host: is the host alive, and does a VK message still reach it?
 *
 * - Pulse: the host posts /watch/beat every 5 minutes (src/ops/heartbeat.py). No beat
 *   for HOST_SILENT_MS → "🔴 the host is silent" to the owner's ops bot; a reminder
 *   every REMIND_MS while it lasts; "🟢" when the beats come back.
 * - Canary: once an hour the Worker puts a service message on the storage queue, the
 *   same queue VK feedback takes. The host's consumer recognizes it, drops it (no
 *   model, nothing written to the database) and posts /watch/canary with its id. Not
 *   seen within CHAIN_SILENT_MS while the host is beating → "🔴 the VK → host chain is
 *   broken". This does not cover VK → Worker itself (VK not calling back); the last
 *   real callback time is kept for the alert text.
 *
 * State lives in one Durable Object (SQLite-backed, free plan). Without a first beat
 * nothing is checked, so a Worker deployed before the host never alerts.
 */

export const HOST_SILENT_MS = 15 * 60 * 1000;
export const CHAIN_SILENT_MS = 30 * 60 * 1000;
export const REMIND_MS = 6 * 60 * 60 * 1000;
export const CANARY_VERSION = 'canary-v1';

const WATCH_NAME = 'watch';

/* The Durable Object's fetch interface: plain JSON, no RPC (nothing to import, so the
 * tests run under node and bun). */
async function call(env, action, body) {
    const stub = env.WATCH.get(env.WATCH.idFromName(WATCH_NAME));
    const response = await stub.fetch(`https://watch/${action}`, {
        method: 'POST',
        body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(`watch ${action}: ${response.status}`);
}

function authorized(request, env) {
    const expected = env.WATCH_SECRET;
    const given = (request.headers.get('Authorization') || '').replace(/^Bearer /, '');
    if (!expected || given.length !== expected.length) return false;
    let diff = 0;
    for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
    return diff === 0;
}

/* /watch/beat and /watch/canary from the host. Anything else under /watch/ is 404. */
export async function handleWatch(request, env) {
    const path = new URL(request.url).pathname;
    if (request.method !== 'POST' || !authorized(request, env)) {
        return new Response('not found', { status: 404 });
    }
    const now = Date.now();
    if (path === '/watch/beat') {
        await call(env, 'beat', { now });
        return new Response('ok');
    }
    if (path === '/watch/canary') {
        let body;
        try { body = await request.json(); } catch { body = null; }
        const id = typeof body?.id === 'string' ? body.id : '';
        if (!/^canary-[0-9]{1,16}$/.test(id)) return new Response('bad id', { status: 400 });
        await call(env, 'seen', { id, now });
        return new Response('ok');
    }
    return new Response('not found', { status: 404 });
}

/* A real VK callback arrived: only its time is kept, for the alert text. */
export async function noteCallback(env) {
    try { await call(env, 'callback', { now: Date.now() }); } catch { /* never blocks feedback */ }
}

/* Every 5 minutes (wrangler cron): checks, and the canary at the top of the hour. */
export async function scheduled(event, env) {
    const now = event.scheduledTime || Date.now();
    await call(env, 'tick', { now, hourly: new Date(now).getUTCMinutes() < 5 });
}

export function ago(ms) {
    const minutes = Math.round(ms / 60000);
    if (minutes < 60) return `${minutes} мин`;
    const hours = Math.round(minutes / 6) / 10;
    return `${hours} ч`.replace('.', ',');
}

/*
 * The decisions, pure: state in, [new state, messages] out. Kept apart from the
 * Durable Object so the tests run without Cloudflare.
 */
export function decide(state, now, sendCanary) {
    const s = { ...state };
    const out = [];
    if (!s.beat) return [s, out, false];  // not configured yet: no checks, no canary

    const silent = now - s.beat;
    if (silent > HOST_SILENT_MS) {
        if (!s.hostDown) {
            s.hostDown = true;
            s.hostRemind = now;
            out.push(`🔴 Сервер «Игры дня» молчит ${ago(silent)}: нет отметок пульса.`);
        } else if (now - s.hostRemind >= REMIND_MS) {
            s.hostRemind = now;
            out.push(`🔴 Сервер всё ещё молчит: ${ago(silent)}.`);
        }
    }
    // Recovery is reported by beat(), the moment it happens.

    if (s.canaryId && s.seenId !== s.canaryId && !s.hostDown && now - s.canarySent > CHAIN_SILENT_MS) {
        if (!s.chainDown) {
            s.chainDown = true;
            s.chainRemind = now;
            const last = s.callback ? `последнее сообщение из VK было ${ago(now - s.callback)} назад` : 'сообщений из VK с запуска не было';
            out.push('🔴 Цепочка VK → сервер не работает: контрольное сообщение не дошло за '
                + `${ago(now - s.canarySent)}. Сервер на связи, значит дело в очереди Cloudflare `
                + `или в обработчике отзывов. Отзывы из VK сейчас могут не доходить (${last}).`);
        } else if (now - s.chainRemind >= REMIND_MS) {
            s.chainRemind = now;
            out.push('🔴 Цепочка VK → сервер всё ещё не работает.');
        }
    }

    // A new canary only once the previous one was seen or given up on (alerted), so a
    // slow consumer is not judged by a canary it had no time for.
    const canary = Boolean(sendCanary && (!s.canaryId || s.seenId === s.canaryId || s.chainDown));
    if (canary) {
        s.canaryId = `canary-${now}`;
        s.canarySent = now;
    }
    return [s, out, canary];
}

export function onBeat(state, now) {
    const s = { ...state, beat: now };
    const out = [];
    if (state.hostDown) {
        s.hostDown = false;
        out.push(`🟢 Сервер снова на связи (молчал ${ago(now - state.beat)}).`);
    }
    return [s, out];
}

export function onSeen(state, id, now) {
    const s = { ...state, seenId: id, seenAt: now };
    const out = [];
    if (state.chainDown && id === state.canaryId) {
        s.chainDown = false;
        out.push('🟢 Цепочка VK → сервер снова работает: контрольное сообщение дошло.');
    }
    return [s, out];
}

export async function notify(env, texts) {
    for (const text of texts) {
        try {
            await fetch(`https://api.telegram.org/bot${env.OPS_BOT_TOKEN}/sendMessage`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_id: env.OPS_CHAT_ID, text })
            });
        } catch (error) {
            console.log('Watch alert failed:', error?.name || 'error');
        }
    }
}

export class Watch {
    constructor(ctx, env) {
        this.ctx = ctx;
        this.env = env;
    }

    async fetch(request) {
        const action = new URL(request.url).pathname.slice(1);
        const body = await request.json();
        if (action === 'beat') await this.beat(body.now);
        else if (action === 'seen') await this.seen(body.id, body.now);
        else if (action === 'callback') await this.callback(body.now);
        else if (action === 'tick') await this.tick(body.now, body.hourly);
        else return new Response('unknown', { status: 404 });
        return new Response('ok');
    }

    async load() {
        return (await this.ctx.storage.get('state')) || {};
    }

    async save(state) {
        await this.ctx.storage.put('state', state);
    }

    async beat(now) {
        const [state, out] = onBeat(await this.load(), now);
        await this.save(state);
        await notify(this.env, out);
    }

    async seen(id, now) {
        const [state, out] = onSeen(await this.load(), id, now);
        await this.save(state);
        await notify(this.env, out);
    }

    async callback(now) {
        const state = await this.load();
        await this.save({ ...state, callback: now });
    }

    async tick(now, hourly) {
        const [state, out, canary] = decide(await this.load(), now, hourly);
        if (canary) {
            try {
                await this.env.FEEDBACK_STORAGE_QUEUE.send({ version: CANARY_VERSION, id: state.canaryId });
            } catch (error) {
                out.push('🔴 Worker не смог положить контрольное сообщение в очередь Cloudflare: '
                    + 'отзывы из VK, скорее всего, тоже не принимаются.');
                state.chainDown = true;  // already said; recovery comes with a canary seen
                state.chainRemind = now;
                console.log('Canary send failed:', error?.name || 'error');
            }
        }
        await this.save(state);
        await notify(this.env, out);
    }
}
