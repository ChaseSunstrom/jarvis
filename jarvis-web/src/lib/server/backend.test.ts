import { describe, it, expect } from 'vitest';
import {
	resolveBackend,
	backendProblem,
	toWsUrl,
	normalizeKind,
	mediaProxyTarget,
	modelProxyTarget
} from './backend';

describe('resolveBackend', () => {
	it('defaults to the core backend', () => {
		const cfg = resolveBackend({ JARVIS_URL: 'http://core:8123', JARVIS_TOKEN: 'k' });
		expect(cfg.kind).toBe('core');
		expect(cfg.url).toBe('http://core:8123');
		expect(cfg.token).toBe('k');
		expect(cfg.configured).toBe(true);
	});

	it('prefers JARVIS_* over HA_* when backend=core', () => {
		const cfg = resolveBackend({
			JARVIS_BACKEND: 'core',
			JARVIS_URL: 'http://core:8123',
			JARVIS_TOKEN: 'core-token',
			HA_URL: 'http://ha:8123',
			HA_TOKEN: 'ha-token'
		});
		expect(cfg.url).toBe('http://core:8123');
		expect(cfg.token).toBe('core-token');
		expect(cfg.source).toEqual({ url: 'JARVIS_URL', token: 'JARVIS_TOKEN' });
	});

	it('prefers HA_* over JARVIS_* when backend=ha', () => {
		const cfg = resolveBackend({
			JARVIS_BACKEND: 'ha',
			JARVIS_URL: 'http://core:8123',
			JARVIS_TOKEN: 'core-token',
			HA_URL: 'http://ha:8123',
			HA_TOKEN: 'ha-token'
		});
		expect(cfg.kind).toBe('ha');
		expect(cfg.url).toBe('http://ha:8123');
		expect(cfg.token).toBe('ha-token');
	});

	it('falls back to HA_* when backend=core but only HA vars are set', () => {
		const cfg = resolveBackend({ HA_URL: 'http://ha:8123/', HA_TOKEN: 'ha-token' });
		expect(cfg.kind).toBe('core');
		expect(cfg.url).toBe('http://ha:8123');
		expect(cfg.token).toBe('ha-token');
		expect(cfg.source).toEqual({ url: 'HA_URL', token: 'HA_TOKEN' });
	});

	it('strips trailing slashes and builds the websocket url', () => {
		expect(resolveBackend({ JARVIS_URL: 'http://x:1///' }).url).toBe('http://x:1');
		expect(toWsUrl('http://x:1')).toBe('ws://x:1/api/websocket');
		expect(toWsUrl('https://x')).toBe('wss://x/api/websocket');
		expect(toWsUrl('')).toBe('');
	});

	it('treats blank env values as unset', () => {
		const cfg = resolveBackend({ JARVIS_URL: '   ', HA_URL: 'http://ha:8123', HA_TOKEN: 't' });
		expect(cfg.url).toBe('http://ha:8123');
		expect(cfg.configured).toBe(true);
	});

	it('reports what is missing', () => {
		expect(backendProblem(resolveBackend({}))).toBe('server missing JARVIS_URL/JARVIS_TOKEN');
		expect(backendProblem(resolveBackend({ JARVIS_URL: 'http://x' }))).toBe(
			'server missing JARVIS_TOKEN'
		);
		expect(backendProblem(resolveBackend({ JARVIS_TOKEN: 't' }))).toBe('server missing JARVIS_URL');
		expect(backendProblem(resolveBackend({ JARVIS_BACKEND: 'ha' }))).toBe(
			'server missing HA_URL/HA_TOKEN'
		);
		expect(
			backendProblem(resolveBackend({ JARVIS_URL: 'http://x', JARVIS_TOKEN: 't' }))
		).toBeNull();
	});

	it('normalizes the backend kind', () => {
		expect(normalizeKind('HA')).toBe('ha');
		expect(normalizeKind(' ha ')).toBe('ha');
		expect(normalizeKind('core')).toBe('core');
		expect(normalizeKind('nonsense')).toBe('core');
		expect(normalizeKind(undefined)).toBe('core');
	});
});

describe('mediaProxyTarget', () => {
	const BASE = 'http://backend:8123';

	it('passes the media paths through', () => {
		expect(mediaProxyTarget('/api/tts_proxy/abc.wav', BASE)).toBe(
			'http://backend:8123/api/tts_proxy/abc.wav'
		);
		expect(mediaProxyTarget('/api/tts/xyz.mp3', BASE)).toBe('http://backend:8123/api/tts/xyz.mp3');
		expect(mediaProxyTarget('/api/tts_proxy/a.wav?x=1', BASE)).toBe(
			'http://backend:8123/api/tts_proxy/a.wav?x=1'
		);
	});

	// The reason this helper exists. `path.includes('..')` is not a traversal
	// check: the WHATWG URL parser also collapses the percent-encoded dot
	// segments below, so each of these used to resolve off the allow-list and be
	// fetched with the server's admin token attached.
	it('blocks percent-encoded dot-segment traversal', () => {
		for (const path of [
			'/api/tts_proxy/%2e%2e/%2e%2e/api/states',
			'/api/tts_proxy/%2E%2E/%2E%2E/api/config',
			'/api/tts_proxy/.%2e/.%2e/api/states',
			'/api/tts_proxy/%2e./%2e./api/services/lock/unlock',
			'/api/tts_proxy/x/%2e%2e/%2e%2e/%2e%2e/'
		]) {
			expect(mediaProxyTarget(path, BASE), path).toBeNull();
		}
	});

	it('blocks plain traversal, other paths and scheme tricks', () => {
		for (const path of [
			'/api/tts_proxy/../../api/states',
			'/api/states',
			'/',
			'',
			'api/tts_proxy/a.wav',
			'//evil.example/api/tts_proxy/a.wav',
			'http://evil.example/api/tts_proxy/a.wav',
			'/api/tts_proxy/a\\..\\b',
			'/api/tts_proxy/a\nHost: evil'
		]) {
			expect(mediaProxyTarget(path, BASE), JSON.stringify(path)).toBeNull();
		}
	});

	it('keeps a sub-path backend prefix and still enforces the allow-list', () => {
		expect(mediaProxyTarget('/api/tts_proxy/a.wav', 'http://backend:8123/jarvis')).toBe(
			'http://backend:8123/jarvis/api/tts_proxy/a.wav'
		);
		expect(
			mediaProxyTarget('/api/tts_proxy/%2e%2e/%2e%2e/api/states', 'http://backend:8123/jarvis')
		).toBeNull();
	});

	it('rejects an unusable base url', () => {
		expect(mediaProxyTarget('/api/tts_proxy/a.wav', '')).toBeNull();
		expect(mediaProxyTarget('/api/tts_proxy/a.wav', 'not a url')).toBeNull();
	});

	it('resolves against the configured backend, whichever it is', () => {
		const cfg = resolveBackend({ JARVIS_BACKEND: 'ha', HA_URL: 'https://ha.example/', HA_TOKEN: 't' });
		expect(mediaProxyTarget('/api/tts_proxy/a.wav', cfg.url)).toBe(
			'https://ha.example/api/tts_proxy/a.wav'
		);
	});
});

describe('modelProxyTarget', () => {
	const BASE = 'http://backend:8123';

	// The bug: the phone asks its own Jarvis for wake-word weights at
	// /api/models/<name>, and the URL people configure is the console's. The
	// console had no such route, so the download 404'd and the on-device wake
	// word could not be set up at all.
	it('resolves a model name against the backend', () => {
		expect(modelProxyTarget('melspectrogram.onnx', BASE)).toBe(
			'http://backend:8123/api/models/melspectrogram.onnx'
		);
		expect(modelProxyTarget('hey_jarvis_v0.1.onnx', BASE)).toBe(
			'http://backend:8123/api/models/hey_jarvis_v0.1.onnx'
		);
		// The catalogue listing rides the same route.
		expect(modelProxyTarget('list', BASE)).toBe('http://backend:8123/api/models/list');
	});

	it('keeps a sub-path backend prefix', () => {
		expect(modelProxyTarget('embedding_model.onnx', 'http://backend:8123/jarvis/')).toBe(
			'http://backend:8123/jarvis/api/models/embedding_model.onnx'
		);
	});

	it('refuses anything that is not one plain file name', () => {
		for (const name of [
			'',
			'..',
			'../../api/states',
			'a/b',
			'%2e%2e',
			'.hidden',
			'a b.onnx',
			'a?x=1',
			'a#b',
			'x'.repeat(65)
		]) {
			expect(modelProxyTarget(name, BASE), JSON.stringify(name)).toBeNull();
		}
	});

	it('rejects an unusable base url, and one carrying credentials', () => {
		expect(modelProxyTarget('a.onnx', '')).toBeNull();
		expect(modelProxyTarget('a.onnx', 'not a url')).toBeNull();
		expect(modelProxyTarget('a.onnx', 'http://user:pass@backend:8123')).toBeNull();
	});
});
