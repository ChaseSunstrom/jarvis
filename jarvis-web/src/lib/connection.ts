// Browser-side transport for the management UI.
//
// Opens the same authenticated /ws relay the voice HUD uses (the server holds
// the backend token) and wires it to a JarvisClient. Pages open a connection on
// mount and close it on destroy; there is no shared singleton, so a page that
// crashes cannot leave a half-subscribed socket behind.

import { JarvisClient } from './jarvisClient';

export type ConnectionStatus = 'connecting' | 'open' | 'closed' | 'error';

export interface Connection {
	client: JarvisClient;
	socket: WebSocket;
	close(): void;
}

export interface ConnectionHandlers {
	onStatus?: (status: ConnectionStatus, detail?: string) => void;
}

export function relayUrl(): string {
	const proto = location.protocol === 'https:' ? 'wss' : 'ws';
	return `${proto}://${location.host}/ws`;
}

/**
 * Resolve once the relay socket is open. The proxy buffers frames until it has
 * finished the backend auth handshake, so commands may be sent immediately.
 */
export function openConnection(handlers: ConnectionHandlers = {}): Promise<Connection> {
	return new Promise((resolve, reject) => {
		let settled = false;
		const socket = new WebSocket(relayUrl());
		socket.binaryType = 'arraybuffer';
		const client = new JarvisClient((data) => {
			// Must throw rather than drop: JarvisClient.command() has already
			// registered a pending entry, and only a synchronous throw here makes
			// it reject. Silently swallowing the frame leaves the caller awaiting a
			// promise that can never settle — which is what freezes a page's
			// `busy` flag the first time the relay blips.
			if (socket.readyState !== WebSocket.OPEN) {
				throw new Error('websocket is not open');
			}
			socket.send(data);
		});

		handlers.onStatus?.('connecting');

		const close = () => {
			try {
				socket.close();
			} catch {
				/* already gone */
			}
		};

		socket.onopen = () => {
			settled = true;
			handlers.onStatus?.('open');
			resolve({ client, socket, close });
		};
		socket.onmessage = (e) => {
			if (typeof e.data === 'string') client.handleMessage(e.data);
		};
		socket.onerror = () => {
			handlers.onStatus?.('error', 'websocket error');
			if (!settled) {
				settled = true;
				reject(new Error('cannot reach the server websocket'));
			}
		};
		socket.onclose = (ev) => {
			client.handleClose(ev.reason || 'connection closed');
			handlers.onStatus?.('closed', ev.reason || undefined);
			if (!settled) {
				settled = true;
				reject(new Error(ev.reason || 'websocket closed before it opened'));
			}
		};
	});
}

/** Message for the "your backend does not do this" hint. */
export function describeError(err: unknown): string {
	if (err && typeof err === 'object' && 'code' in err && (err as any).code === 'unknown_command') {
		return `${(err as any).message} — the selected backend does not implement this command.`;
	}
	return err instanceof Error ? err.message : String(err);
}
