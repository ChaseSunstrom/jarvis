import { describe, it, expect, afterAll } from 'vitest';
import http from 'node:http';
import { WebSocket } from 'ws';
import {
	resolveBackend as resolveTs,
	isOriginAllowed as isOriginAllowedTs,
	parseAllowedOrigins as parseAllowedOriginsTs
} from './backend';
// server/ws-proxy.js is copied verbatim into build/ by scripts/postbuild.mjs and
// therefore carries its own copy of resolveBackend. This test is the guard that
// the two copies stay in agreement.
import {
	resolveBackend as resolveJs,
	isOriginAllowed as isOriginAllowedJs,
	parseAllowedOrigins as parseAllowedOriginsJs,
	attachWsProxy
} from '../../../server/ws-proxy.js';
// @ts-expect-error - plain .mjs test double, no types
import { startMockHA, MOCK_TOKEN } from '../../../../tests/web/mock-ha.mjs';

const CASES: Record<string, string | undefined>[] = [
	{},
	{ JARVIS_URL: 'http://core:8123', JARVIS_TOKEN: 'k' },
	{ HA_URL: 'http://ha:8123/', HA_TOKEN: 'h' },
	{
		JARVIS_BACKEND: 'ha',
		JARVIS_URL: 'http://core:8123',
		JARVIS_TOKEN: 'k',
		HA_URL: 'https://ha.example',
		HA_TOKEN: 'h'
	},
	{
		JARVIS_BACKEND: 'CORE',
		JARVIS_URL: 'http://core:8123',
		HA_URL: 'http://ha:8123',
		HA_TOKEN: 'h'
	},
	{ JARVIS_BACKEND: 'nonsense', JARVIS_URL: '  ', HA_URL: 'http://ha:8123', HA_TOKEN: 'h' }
];

describe('ws-proxy resolveBackend', () => {
	it('matches the TypeScript resolver for every env shape', () => {
		for (const env of CASES) {
			expect(resolveJs(env), JSON.stringify(env)).toEqual(resolveTs(env));
		}
	});

	it('builds the websocket url the relay dials', () => {
		expect(resolveJs({ JARVIS_URL: 'http://core:8123' }).wsUrl).toBe(
			'ws://core:8123/api/websocket'
		);
		expect(resolveJs({ JARVIS_BACKEND: 'ha', HA_URL: 'https://ha.example' }).wsUrl).toBe(
			'wss://ha.example/api/websocket'
		);
	});
});

// --- the origin guard -------------------------------------------------------
//
// A socket on /ws is an authenticated admin session: the relay attaches the
// server-held backend token to it. WebSocket upgrades bypass the same-origin
// policy, so without an Origin check any page the user opens can drive the
// house from across the internet.

const ORIGIN_CASES: [string | undefined, string | undefined, string[]][] = [
	// same-origin, the normal case
	['http://jarvis.local:8199', 'jarvis.local:8199', []],
	['https://jarvis.local:8199', 'jarvis.local:8199', []],
	['http://127.0.0.1:8199', '127.0.0.1:8199', []],
	// default ports spelled both ways
	['http://jarvis.local', 'jarvis.local:80', []],
	['https://jarvis.local', 'jarvis.local:443', []],
	['http://jarvis.local:80', 'jarvis.local', []],
	// foreign origins
	['https://evil.example', 'jarvis.local:8199', []],
	['http://jarvis.local:9999', 'jarvis.local:8199', []],
	['http://jarvis.local.evil.example', 'jarvis.local', []],
	['null', 'jarvis.local:8199', []],
	['', 'jarvis.local:8199', []],
	[undefined, 'jarvis.local:8199', []],
	['not a url', 'jarvis.local:8199', []],
	// allow-list
	['https://hud.example', 'jarvis.local:8199', ['https://hud.example']],
	['https://hud.example', 'jarvis.local:8199', ['https://other.example']],
	['https://hud.example:443', 'jarvis.local:8199', ['https://hud.example']],
	// no Host header to compare against
	['https://evil.example', undefined, []]
];

describe('ws-proxy origin guard', () => {
	it('agrees between the TypeScript original and the hand-copy', () => {
		for (const [origin, host, allowed] of ORIGIN_CASES) {
			expect(isOriginAllowedJs(origin, host, allowed), JSON.stringify([origin, host, allowed])).toBe(
				isOriginAllowedTs(origin, host, allowed)
			);
		}
		for (const raw of ['', undefined, 'https://a.example', 'https://a.example, http://b:8080', 'junk,https://c.example']) {
			expect(parseAllowedOriginsJs(raw)).toEqual(parseAllowedOriginsTs(raw));
		}
	});

	it('allows a same-origin upgrade, including either spelling of a default port', () => {
		expect(isOriginAllowedTs('http://jarvis.local:8199', 'jarvis.local:8199')).toBe(true);
		expect(isOriginAllowedTs('http://jarvis.local', 'jarvis.local:80')).toBe(true);
		expect(isOriginAllowedTs('https://jarvis.local', 'jarvis.local:443')).toBe(true);
		// Behind a TLS terminator the page is https while this hop is http.
		expect(isOriginAllowedTs('https://jarvis.local:8199', 'jarvis.local:8199')).toBe(true);
	});

	it('refuses a foreign origin, a look-alike host and a sandboxed frame', () => {
		expect(isOriginAllowedTs('https://evil.example', 'jarvis.local:8199')).toBe(false);
		expect(isOriginAllowedTs('http://jarvis.local.evil.example', 'jarvis.local')).toBe(false);
		expect(isOriginAllowedTs('http://jarvis.local:9999', 'jarvis.local:8199')).toBe(false);
		// `Origin: null` is a real browser origin (sandboxed iframe, data: doc).
		expect(isOriginAllowedTs('null', 'jarvis.local:8199')).toBe(false);
		expect(isOriginAllowedTs('not a url', 'jarvis.local:8199')).toBe(false);
	});

	it('allows a non-browser client, which cannot send an Origin at all', () => {
		// Every browser sends Origin on a WS handshake, so its absence means a
		// script/native client — not something a hostile page can arrange.
		expect(isOriginAllowedTs(undefined, 'jarvis.local:8199')).toBe(true);
		expect(isOriginAllowedTs('', 'jarvis.local:8199')).toBe(true);
	});

	it('honours JARVIS_ALLOWED_ORIGINS, and ignores unparseable entries', () => {
		const allowed = parseAllowedOriginsTs('https://hud.example , junk, http://box:8080');
		expect(allowed).toEqual(['https://hud.example', 'http://box:8080']);
		expect(isOriginAllowedTs('https://hud.example', 'jarvis.local:8199', allowed)).toBe(true);
		expect(isOriginAllowedTs('http://box:8080', 'jarvis.local:8199', allowed)).toBe(true);
		expect(isOriginAllowedTs('https://nope.example', 'jarvis.local:8199', allowed)).toBe(false);
	});
});

// --- the guard on the real upgrade path -------------------------------------

const cleanups: (() => Promise<void> | void)[] = [];
afterAll(async () => {
	for (const fn of cleanups.reverse()) await fn();
});

/** A relay in front of the real mock backend, on a real port. */
async function startRelay(allowedOrigins?: string[]) {
	const mock = await startMockHA({});
	const server = http.createServer((_req, res) => res.end('ok'));
	attachWsProxy(server, {
		env: { JARVIS_URL: mock.url, JARVIS_TOKEN: MOCK_TOKEN },
		allowedOrigins
	});
	await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
	const port = (server.address() as any).port;
	cleanups.push(async () => {
		server.close();
		await mock.close();
	});
	return { port, url: `ws://127.0.0.1:${port}/ws` };
}

/** Resolves to 'open' or the failure text, whichever happens first. */
function tryConnect(url: string, origin?: string): Promise<string> {
	return new Promise((resolve) => {
		const ws = new WebSocket(url, origin ? { headers: { Origin: origin } } : {});
		ws.on('open', () => {
			ws.close();
			resolve('open');
		});
		ws.on('error', (e: Error) => resolve(e.message));
	});
}

describe('ws-proxy upgrade', () => {
	it('403s a cross-origin upgrade before any socket exists', async () => {
		const { url } = await startRelay();
		// This is the whole attack: a page on some other origin opening a socket
		// to the HUD. No token, no cookie, no preflight.
		expect(await tryConnect(url, 'https://evil.example')).toContain('403');
	});

	it('still relays a same-origin upgrade, authenticated, end to end', async () => {
		const { port, url } = await startRelay();
		const ws = new WebSocket(url, { headers: { Origin: `http://127.0.0.1:${port}` } });
		const states = await new Promise<any[]>((resolve, reject) => {
			ws.on('open', () => ws.send(JSON.stringify({ id: 1, type: 'get_states' })));
			ws.on('message', (raw: any) => {
				const msg = JSON.parse(raw.toString());
				if (msg.id === 1) resolve(msg.result ?? []);
			});
			ws.on('error', reject);
		});
		// The relay did the auth handshake for us and swallowed the auth_* frames.
		expect(states.length).toBeGreaterThan(0);
		ws.close();
	});

	it('lets a non-browser client with no Origin through', async () => {
		const { url } = await startRelay();
		expect(await tryConnect(url)).toBe('open');
	});

	it('lets an allow-listed origin through', async () => {
		const { url } = await startRelay(['https://hud.example']);
		expect(await tryConnect(url, 'https://hud.example')).toBe('open');
		expect(await tryConnect(url, 'https://evil.example')).toContain('403');
	});
});
