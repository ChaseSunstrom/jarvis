import { describe, it, expect } from 'vitest';
import { resolveBackend as resolveTs } from './backend';
// server/ws-proxy.js is copied verbatim into build/ by scripts/postbuild.mjs and
// therefore carries its own copy of resolveBackend. This test is the guard that
// the two copies stay in agreement.
import { resolveBackend as resolveJs } from '../../../server/ws-proxy.js';

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
