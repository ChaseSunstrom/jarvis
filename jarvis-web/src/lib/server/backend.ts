// Which backend this server talks to.
//
// jarvis-core and Home Assistant expose the same websocket contract at
// /api/websocket and the same TTS media paths under /api/tts_proxy/, so one
// pair of (url, token) is all either needs. JARVIS_BACKEND picks which pair of
// env vars wins; the other pair stays as a fallback so an existing HA-only
// deployment keeps working after an upgrade without touching its env file.
//
// NOTE: server/ws-proxy.js carries a hand-copy of resolveBackend() because it
// is copied verbatim into build/ by scripts/postbuild.mjs and cannot import
// from src/. Keep the two in sync.

export type BackendKind = 'core' | 'ha';

export interface BackendConfig {
	/** Which backend was selected. */
	kind: BackendKind;
	/** Base HTTP url, trailing slashes stripped. Empty when unconfigured. */
	url: string;
	/** Access token. Server-side only — never sent to the browser. */
	token: string;
	/** Full websocket url, e.g. ws://host:8123/api/websocket. Empty when unconfigured. */
	wsUrl: string;
	/** Both url and token are present. */
	configured: boolean;
	/** Names of the env vars that were consulted, most specific first. */
	source: { url: string; token: string };
}

export const WS_PATH = '/api/websocket';

export function normalizeKind(value: unknown): BackendKind {
	return String(value ?? '').trim().toLowerCase() === 'ha' ? 'ha' : 'core';
}

/** ws:// for http://, wss:// for https://. */
export function toWsUrl(httpUrl: string): string {
	if (!httpUrl) return '';
	return httpUrl.replace(/\/+$/, '').replace(/^http/, 'ws') + WS_PATH;
}

export function resolveBackend(env: Record<string, string | undefined> = {}): BackendConfig {
	const kind = normalizeKind(env.JARVIS_BACKEND);
	// The selected backend's vars win; the other pair is the fallback.
	const order =
		kind === 'core'
			? { url: ['JARVIS_URL', 'HA_URL'], token: ['JARVIS_TOKEN', 'HA_TOKEN'] }
			: { url: ['HA_URL', 'JARVIS_URL'], token: ['HA_TOKEN', 'JARVIS_TOKEN'] };

	const pick = (names: string[]): [string, string] => {
		for (const name of names) {
			const value = (env[name] ?? '').trim();
			if (value) return [value, name];
		}
		return ['', names[0]];
	};

	const [rawUrl, urlSource] = pick(order.url);
	const [token, tokenSource] = pick(order.token);
	const url = rawUrl.replace(/\/+$/, '');

	return {
		kind,
		url,
		token,
		wsUrl: toWsUrl(url),
		configured: Boolean(url && token),
		source: { url: urlSource, token: tokenSource }
	};
}

/**
 * Media paths `/api/tts` is allowed to proxy. Everything else on the backend —
 * `/api/states`, `/api/config`, `/api/services/...` — is off limits, because the
 * proxy attaches the server-held admin token to whatever it fetches.
 */
export const MEDIA_PREFIXES = ['/api/tts_proxy/', '/api/tts/'] as const;

/**
 * Resolve a client-supplied media `path` against the backend, or null when it
 * escapes the allow-list.
 *
 * A substring test for `..` is **not** enough. The WHATWG URL parser treats the
 * percent-encoded forms of a dot segment (`%2e%2e`, `.%2e`, `%2E%2E`) as dot
 * segments too, so `/api/tts_proxy/%2e%2e/%2e%2e/api/states` contains no literal
 * `..` yet resolves to `/api/states` — reachable, with the admin token attached,
 * by anyone who can load a page from this server. The only reliable check is to
 * let the parser normalise the URL first and re-test the result.
 */
export function mediaProxyTarget(path: string, baseUrl: string): string | null {
	if (!path.startsWith('/') || path.startsWith('//')) return null;
	if (/[\\\r\n\t]/.test(path)) return null;
	if (!MEDIA_PREFIXES.some((p) => path.startsWith(p))) return null;

	let base: URL;
	let target: URL;
	try {
		base = new URL(baseUrl);
		// Concatenate (not `new URL(path, base)`) so a backend served under a
		// sub-path keeps its prefix, exactly as the unguarded version did.
		target = new URL(base.href.replace(/\/+$/, '') + path);
	} catch {
		return null;
	}
	if (target.origin !== base.origin || target.username || target.password) return null;

	const basePath = base.pathname.replace(/\/+$/, '');
	// Re-test the *normalised* pathname: dot segments are already collapsed here.
	if (!MEDIA_PREFIXES.some((p) => target.pathname.startsWith(basePath + p))) return null;
	return target.href;
}

/** Human-readable reason a backend is unusable, or null when it is fine. */
export function backendProblem(cfg: BackendConfig): string | null {
	if (!cfg.url && !cfg.token) {
		return `server missing ${cfg.source.url}/${cfg.source.token}`;
	}
	if (!cfg.url) return `server missing ${cfg.source.url}`;
	if (!cfg.token) return `server missing ${cfg.source.token}`;
	return null;
}
