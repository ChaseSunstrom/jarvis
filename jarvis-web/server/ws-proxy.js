// Backend WebSocket proxy (jarvis-core or Home Assistant).
//
// The browser never sees the backend token. It connects to
// ws(s)://<origin>/ws; this module opens a server-side connection to
// ${url}/api/websocket, performs the auth handshake with the token, swallows
// the auth_* messages, then relays frames bidirectionally (JSON text frames
// and binary audio frames). jarvis-core speaks the same handshake and the same
// assist_pipeline framing, so the relay is backend-agnostic.
//
// Used both by the production server (build/index.js, see scripts/postbuild.mjs)
// and by the vite dev server (vite.config.ts plugin).

import { WebSocketServer, WebSocket } from 'ws';

// Hand-copy of src/lib/server/backend.ts — this file is copied verbatim into
// build/ by scripts/postbuild.mjs, so it cannot import from src/. Keep in sync.
/**
 * @param {Record<string, string|undefined>} env
 * @returns {{ kind: 'core'|'ha', url: string, token: string, wsUrl: string,
 *             configured: boolean, source: { url: string, token: string } }}
 */
export function resolveBackend(env = {}) {
	const kind = String(env.JARVIS_BACKEND ?? '').trim().toLowerCase() === 'ha' ? 'ha' : 'core';
	const order =
		kind === 'core'
			? { url: ['JARVIS_URL', 'HA_URL'], token: ['JARVIS_TOKEN', 'HA_TOKEN'] }
			: { url: ['HA_URL', 'JARVIS_URL'], token: ['HA_TOKEN', 'JARVIS_TOKEN'] };
	const pick = (names) => {
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
		wsUrl: url ? url.replace(/^http/, 'ws') + '/api/websocket' : '',
		configured: Boolean(url && token),
		source: { url: urlSource, token: tokenSource }
	};
}

/**
 * @param {import('node:http').Server} httpServer
 * @param {{ haUrl?: string, haToken?: string, url?: string, token?: string, path?: string,
 *           env?: Record<string, string|undefined> }} [opts]
 */
export function attachWsProxy(httpServer, opts = {}) {
	const path = opts.path ?? '/ws';
	const wss = new WebSocketServer({ noServer: true });

	httpServer.on('upgrade', (req, socket, head) => {
		let pathname = '/';
		try {
			pathname = new URL(req.url ?? '/', 'http://internal').pathname;
		} catch {
			socket.destroy();
			return;
		}
		if (pathname !== path) return; // let other handlers (e.g. vite HMR) deal with it
		// Resolved per connection so a restarted backend / changed env is picked up.
		const backend = resolveBackend(opts.env ?? process.env);
		wss.handleUpgrade(req, socket, head, (client) => {
			proxyToBackend(client, {
				// explicit opts win (used by tests); haUrl/haToken kept for compatibility
				url: opts.url ?? opts.haUrl ?? backend.url,
				token: opts.token ?? opts.haToken ?? backend.token,
				problem: `server missing ${backend.source.url}/${backend.source.token}`
			});
		});
	});

	return wss;
}

/**
 * @param {import('ws').WebSocket} client browser-side socket
 * @param {{ url?: string, token?: string, problem?: string }} cfg
 */
function proxyToBackend(client, { url, token, problem }) {
	if (!url || !token) {
		client.close(1011, problem ?? 'server missing backend url/token');
		return;
	}
	const target = url.replace(/\/+$/, '').replace(/^http/, 'ws') + '/api/websocket';
	const ha = new WebSocket(target);
	let authed = false;
	/** @type {Array<[import('ws').RawData, boolean]>} */
	const pendingFromClient = [];

	client.on('message', (data, isBinary) => {
		if (!authed) pendingFromClient.push([data, isBinary]);
		else if (ha.readyState === WebSocket.OPEN) ha.send(data, { binary: isBinary });
	});

	ha.on('message', (data, isBinary) => {
		if (!authed && !isBinary) {
			let msg;
			try {
				msg = JSON.parse(data.toString());
			} catch {
				return;
			}
			if (msg.type === 'auth_required') {
				ha.send(JSON.stringify({ type: 'auth', access_token: token }));
			} else if (msg.type === 'auth_ok') {
				authed = true;
				for (const [d, b] of pendingFromClient) ha.send(d, { binary: b });
				pendingFromClient.length = 0;
			} else if (msg.type === 'auth_invalid') {
				client.close(1011, 'backend auth failed');
				ha.close();
			}
			return; // swallow auth handshake messages
		}
		if (client.readyState === WebSocket.OPEN) client.send(data, { binary: isBinary });
	});

	const closeBoth = () => {
		if (client.readyState === WebSocket.OPEN || client.readyState === WebSocket.CONNECTING) {
			client.close();
		}
		if (ha.readyState === WebSocket.OPEN || ha.readyState === WebSocket.CONNECTING) {
			ha.close();
		}
	};
	client.on('close', closeBoth);
	ha.on('close', closeBoth);
	client.on('error', closeBoth);
	ha.on('error', closeBoth);
}
