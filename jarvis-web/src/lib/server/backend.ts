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

/**
 * A model file name the mirror is allowed to be asked for.
 *
 * One path segment, and a conservative alphabet. `..` cannot appear, so a
 * traversal cannot be spelled — and nor can `%2e%2e`, because a percent sign is
 * not in the alphabet either and SvelteKit has already decoded the segment by
 * the time it gets here. The real authority is jarvis-core, which serves only
 * the names in its own catalogue; this is the door, not the lock.
 */
const MODEL_NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

/**
 * Where a model download goes, or null when the name is not one.
 *
 * The phone asks its own Jarvis for wake-word weights at `/api/models/<name>`.
 * Pointed at jarvis-core that is a real route; pointed at the console — which
 * is the URL a person types, because it is the one with a web page on it — it
 * was a 404, and the reported symptom was exactly that: "I can't download the
 * models, I get a 404 error on my phone".
 *
 * So the console serves the same path. Unlike [mediaProxyTarget] this one does
 * NOT get the server-held admin token attached: the caller presents its own
 * bearer token and jarvis-core validates it, the same arrangement the `/ws`
 * relay uses for a client that brings its own. See `api/models/[name]`.
 */
export function modelProxyTarget(name: string, baseUrl: string): string | null {
	if (!MODEL_NAME.test(name)) return null;
	let base: URL;
	try {
		base = new URL(baseUrl);
	} catch {
		return null;
	}
	if (base.username || base.password) return null;
	return `${base.href.replace(/\/+$/, '')}/api/models/${name}`;
}

/**
 * Origin control for the `/ws` relay.
 *
 * The relay attaches the server-held backend token to whatever connects, so a
 * socket on it is an authenticated admin session: read every state and every
 * event, and dispatch any service — `lock.unlock` included.
 *
 * WebSocket upgrades are **not** subject to the same-origin policy. There is no
 * preflight, and `Origin` is advisory unless the server checks it. Without this
 * check any page the user happens to open — an ad frame, a blog, anything that
 * can reach the HUD on the LAN or over WireGuard — can open
 * `ws://jarvis.local:8199/ws` and drive the house. That is untrusted content
 * reaching a dispatcher with no human approval, which the tiering model exists
 * to prevent; the tiers cannot help here because they gate the *model's* path,
 * and this is the "a human pressed a button" path being forged.
 *
 * Same-origin is the default. `JARVIS_ALLOWED_ORIGINS` (comma-separated) adds
 * extras for a reverse proxy that terminates on a different name.
 */

/** Strip a port that is the default for the scheme, so the two spellings match. */
function normalizeHost(host: string, protocol: string): string {
	const lower = host.trim().toLowerCase();
	const dflt = protocol === 'https:' || protocol === 'wss:' ? ':443' : ':80';
	return lower.endsWith(dflt) ? lower.slice(0, -dflt.length) : lower;
}

/** Parse `JARVIS_ALLOWED_ORIGINS` into normalised `protocol//host` strings. */
export function parseAllowedOrigins(raw: string | undefined): string[] {
	return String(raw ?? '')
		.split(',')
		.map((s) => s.trim())
		.filter(Boolean)
		.map((s) => {
			try {
				const u = new URL(s);
				return `${u.protocol}//${normalizeHost(u.host, u.protocol)}`;
			} catch {
				return '';
			}
		})
		.filter(Boolean);
}

/**
 * Whether a `/ws` upgrade carrying this `Origin` may proceed.
 *
 * - **No Origin header** → allowed. Every browser sends one on a WebSocket
 *   handshake, so its absence means a non-browser client (the Android app, a
 *   script, curl) — something a hostile web page cannot cause to happen.
 * - **`Origin: null`** → refused. That is a real browser origin (sandboxed
 *   iframe, `data:` document); it is simply not ours.
 * - Otherwise the origin's host must equal the request's `Host`, or appear in
 *   the allow-list.
 */
export function isOriginAllowed(
	origin: string | undefined | null,
	host: string | undefined | null,
	allowed: string[] = []
): boolean {
	if (origin === undefined || origin === null || origin === '') return true;

	let originUrl: URL;
	try {
		originUrl = new URL(String(origin));
	} catch {
		return false; // unparseable, and "null" lands here too
	}
	if (!originUrl.host) return false;

	const originKey = `${originUrl.protocol}//${normalizeHost(originUrl.host, originUrl.protocol)}`;
	if (allowed.includes(originKey)) return true;

	if (host) {
		// Compare host:port only. The scheme is not comparable: behind a TLS
		// terminator the page is https while the hop to this server is http.
		if (normalizeHost(originUrl.host, originUrl.protocol) === normalizeHost(String(host), originUrl.protocol)) {
			return true;
		}
	}
	return false;
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
