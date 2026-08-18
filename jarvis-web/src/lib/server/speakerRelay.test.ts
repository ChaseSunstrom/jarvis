import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { relaySpeakerWrite } from './speakerRelay';

/**
 * The rule enrolment is built on, tested as behaviour rather than as a grep.
 *
 * **Enrolment is authorised by something the caller HOLDS, never by the fact
 * that they could reach this port.** Teaching Jarvis a new owner on the
 * strength of reachability is the opposite of what the feature is for.
 *
 * There are exactly two things a caller can hold: the phone's own Jarvis token,
 * or the console password. `routes.test.ts` used to enforce this by asserting
 * the string `backend.token` never appears in the relay — a good proxy while
 * the phone was the only client, and one that cannot tell "reaches for the
 * admin token" from "reaches for it only after the console password was
 * proved". These tests assert the rule itself, so the proxy is not needed.
 */

const ADMIN = 'admin-token-not-for-browsers';
let calls: { url: string; auth: string | null }[] = [];

function fetcher(): typeof globalThis.fetch {
	return (async (input: string | URL | Request, init?: RequestInit) => {
		const headers = new Headers(init?.headers);
		calls.push({ url: String(input), auth: headers.get('authorization') });
		return new Response(JSON.stringify({ enrolled: true, samples: 1 }), {
			status: 200,
			headers: { 'Content-Type': 'application/json' }
		});
	}) as unknown as typeof globalThis.fetch;
}

function post(auth?: string, query = ''): Request {
	return new Request(`http://console.local/api/voice/speaker/enrol${query}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/octet-stream',
			...(auth ? { Authorization: auth } : {})
		},
		body: new Uint8Array([1, 2, 3, 4])
	});
}

beforeEach(() => {
	calls = [];
	process.env.JARVIS_BACKEND = 'core';
	process.env.JARVIS_URL = 'http://core:8080';
	process.env.JARVIS_TOKEN = ADMIN;
});

afterEach(() => {
	delete process.env.JARVIS_BACKEND;
	delete process.env.JARVIS_URL;
	delete process.env.JARVIS_TOKEN;
});

async function statusOf(promise: Promise<Response>): Promise<number> {
	try {
		return (await promise).status;
	} catch (err) {
		// SvelteKit's `error()` throws an HttpError rather than returning.
		return (err as { status?: number }).status ?? 500;
	}
}

describe('relaying a voice-identity write', () => {
	it('refuses a caller who holds neither credential', async () => {
		const status = await statusOf(relaySpeakerWrite(post(), fetcher(), 'enrol', false));
		expect(status).toBe(401);
		expect(calls, 'the request reached jarvis-core without any credential').toEqual([]);
	});

	it('never sends the admin token for a caller who proved nothing', async () => {
		await statusOf(relaySpeakerWrite(post(), fetcher(), 'enrol', false));
		expect(calls.map((c) => c.auth)).not.toContain(`Bearer ${ADMIN}`);
	});

	it('forwards the phone’s own token, unchanged', async () => {
		await relaySpeakerWrite(post('Bearer phone-token'), fetcher(), 'enrol', false);
		expect(calls).toHaveLength(1);
		expect(calls[0].auth).toBe('Bearer phone-token');
		expect(calls[0].auth).not.toBe(`Bearer ${ADMIN}`);
	});

	it('lets an unlocked console through on the console password', async () => {
		const res = await relaySpeakerWrite(post(), fetcher(), 'enrol', true);
		expect(res.status).toBe(200);
		expect(calls).toHaveLength(1);
		expect(calls[0].auth).toBe(`Bearer ${ADMIN}`);
	});

	it('prefers the caller’s own token when they have both', async () => {
		// Downgrading a specific credential to a blanket one would make the
		// phone's identity invisible to jarvis-core, which is the single
		// authority on tokens.
		await relaySpeakerWrite(post('Bearer phone-token'), fetcher(), 'enrol', true);
		expect(calls[0].auth).toBe('Bearer phone-token');
	});

	it('treats a malformed Authorization header as no credential at all', async () => {
		const status = await statusOf(
			relaySpeakerWrite(post('Bearer'), fetcher(), 'enrol', false)
		);
		expect(status).toBe(401);
		expect(calls).toEqual([]);
	});

	it('refuses an empty body before it reaches jarvis-core', async () => {
		const empty = new Request('http://console.local/api/voice/speaker/enrol', {
			method: 'POST',
			headers: { Authorization: 'Bearer phone-token' },
			body: null
		});
		expect(await statusOf(relaySpeakerWrite(empty, fetcher(), 'enrol', false))).toBe(400);
		expect(calls).toEqual([]);
	});

	it('carries the rate and width through to jarvis-core', async () => {
		// jarvis-core reads these from the query string. Dropping them silently
		// gave every caller the 16 kHz/16-bit defaults, which is what both
		// clients happen to send — a trap for the first one that does not.
		await relaySpeakerWrite(
			post('Bearer phone-token', '?rate=48000&width=2'),
			fetcher(),
			'enrol',
			false
		);
		expect(calls[0].url).toContain('rate=48000');
		expect(calls[0].url).toContain('width=2');
	});

	it('sends the write to the endpoint it was asked for', async () => {
		await relaySpeakerWrite(post('Bearer t'), fetcher(), 'verify', false);
		expect(calls[0].url).toContain('/api/voice/speaker/verify');
	});
});
