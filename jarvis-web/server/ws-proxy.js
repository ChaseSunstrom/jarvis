// HA WebSocket proxy.
//
// The browser never sees the Home Assistant token. It connects to
// ws(s)://<origin>/ws; this module opens a server-side connection to
// ${HA_URL}/api/websocket, performs the HA auth handshake with HA_TOKEN,
// swallows the auth_* messages, then relays frames bidirectionally
// (JSON text frames and binary audio frames).
//
// Used both by the production server (build/index.js, see scripts/postbuild.mjs)
// and by the vite dev server (vite.config.ts plugin).

import { WebSocketServer, WebSocket } from 'ws';

/**
 * @param {import('node:http').Server} httpServer
 * @param {{ haUrl?: string, haToken?: string, path?: string }} [opts]
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
		wss.handleUpgrade(req, socket, head, (client) => {
			proxyToHA(client, {
				haUrl: opts.haUrl ?? process.env.HA_URL,
				haToken: opts.haToken ?? process.env.HA_TOKEN
			});
		});
	});

	return wss;
}

/**
 * @param {import('ws').WebSocket} client browser-side socket
 * @param {{ haUrl?: string, haToken?: string }} cfg
 */
function proxyToHA(client, { haUrl, haToken }) {
	if (!haUrl || !haToken) {
		client.close(1011, 'server missing HA_URL/HA_TOKEN');
		return;
	}
	const target = haUrl.replace(/\/+$/, '').replace(/^http/, 'ws') + '/api/websocket';
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
				ha.send(JSON.stringify({ type: 'auth', access_token: haToken }));
			} else if (msg.type === 'auth_ok') {
				authed = true;
				for (const [d, b] of pendingFromClient) ha.send(d, { binary: b });
				pendingFromClient.length = 0;
			} else if (msg.type === 'auth_invalid') {
				client.close(1011, 'home assistant auth failed');
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
