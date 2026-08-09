// The browser transport had no coverage: it is the piece that decides what
// happens to a command when the relay socket is not open, and getting that wrong
// hangs every page that awaits one.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { openConnection, relayUrl, describeError } from './connection';
import { JarvisCommandError, UnsupportedCommandError } from './jarvisClient';

const CONNECTING = 0;
const OPEN = 1;
const CLOSING = 2;
const CLOSED = 3;

class FakeWebSocket {
	static CONNECTING = CONNECTING;
	static OPEN = OPEN;
	static CLOSING = CLOSING;
	static CLOSED = CLOSED;
	static last: FakeWebSocket | null = null;

	url: string;
	readyState = CONNECTING;
	binaryType = 'blob';
	sent: string[] = [];
	onopen: (() => void) | null = null;
	onmessage: ((e: { data: unknown }) => void) | null = null;
	onerror: (() => void) | null = null;
	onclose: ((e: { reason: string }) => void) | null = null;

	constructor(url: string) {
		this.url = url;
		FakeWebSocket.last = this;
	}
	send(data: string) {
		this.sent.push(data);
	}
	close() {
		this.readyState = CLOSED;
	}
	// --- test drivers ---
	open() {
		this.readyState = OPEN;
		this.onopen?.();
	}
	deliver(msg: unknown) {
		this.onmessage?.({ data: JSON.stringify(msg) });
	}
	drop(reason = 'backend went away') {
		this.readyState = CLOSED;
		this.onclose?.({ reason });
	}
}

beforeEach(() => {
	(globalThis as any).WebSocket = FakeWebSocket;
	(globalThis as any).location = { protocol: 'http:', host: 'jarvis.local:3000' };
	FakeWebSocket.last = null;
});

afterEach(() => {
	delete (globalThis as any).WebSocket;
	delete (globalThis as any).location;
	vi.useRealTimers();
});

async function connect() {
	const statuses: string[] = [];
	const promise = openConnection({ onStatus: (s) => statuses.push(s) });
	const socket = FakeWebSocket.last!;
	socket.open();
	return { conn: await promise, socket, statuses };
}

describe('relayUrl', () => {
	it('follows the page protocol', () => {
		expect(relayUrl()).toBe('ws://jarvis.local:3000/ws');
		(globalThis as any).location = { protocol: 'https:', host: 'jarvis.local' };
		expect(relayUrl()).toBe('wss://jarvis.local/ws');
	});
});

describe('openConnection', () => {
	it('resolves on open and round-trips a command', async () => {
		const { conn, socket, statuses } = await connect();
		expect(statuses).toEqual(['connecting', 'open']);
		expect(socket.binaryType).toBe('arraybuffer');

		const promise = conn.client.getStates();
		expect(JSON.parse(socket.sent[0])).toEqual({ id: 1, type: 'get_states' });
		socket.deliver({ id: 1, type: 'result', success: true, result: [] });
		await expect(promise).resolves.toEqual([]);
	});

	// Regression: the transport used to `if (readyState === OPEN) socket.send(...)`
	// and otherwise return silently. JarvisClient.command() had already created a
	// pending entry by then, so the caller awaited a promise that could never
	// settle — the areas/tools pages latch `busy = true` and disable every button.
	it('rejects a command issued after the socket closed instead of hanging', async () => {
		const { conn, socket } = await connect();
		socket.drop('backend went away');

		const settled = await Promise.race([
			conn.client.getStates().then(
				() => 'resolved',
				(e) => e
			),
			new Promise((r) => setTimeout(() => r('HUNG'), 50))
		]);
		expect(settled).toBeInstanceOf(Error);
		expect((settled as Error).message).toMatch(/not open/);
		expect(conn.client.pendingIds).toEqual([]);
	});

	it('rejects a command issued after close() by the page', async () => {
		const { conn } = await connect();
		conn.close();
		await expect(conn.client.getStates()).rejects.toThrow(/not open/);
	});

	it('fails everything in flight when the relay drops', async () => {
		const { conn, socket, statuses } = await connect();
		const inflight = conn.client.getStates();
		socket.drop('backend went away');
		await expect(inflight).rejects.toThrow('backend went away');
		expect(statuses).toContain('closed');
	});

	it('rejects when the socket closes before it ever opened', async () => {
		const promise = openConnection();
		FakeWebSocket.last!.drop('relay refused');
		await expect(promise).rejects.toThrow('relay refused');
	});

	it('rejects once on error and does not also reject on the following close', async () => {
		const promise = openConnection();
		const socket = FakeWebSocket.last!;
		socket.onerror?.();
		await expect(promise).rejects.toThrow(/cannot reach/);
		expect(() => socket.drop()).not.toThrow();
	});

	it('ignores binary frames on the management socket', async () => {
		const { conn, socket } = await connect();
		const onUnhandled = vi.fn();
		(conn.client as any).opts.onUnhandled = onUnhandled;
		socket.onmessage?.({ data: new ArrayBuffer(4) });
		expect(onUnhandled).not.toHaveBeenCalled();
	});
});

describe('describeError', () => {
	it('explains an unknown command', () => {
		expect(describeError(new UnsupportedCommandError('jarvis/tools/list', 'nope'))).toContain(
			'does not implement'
		);
	});
	it('passes other errors through', () => {
		expect(describeError(new JarvisCommandError('timeout', 'too slow'))).toBe('too slow');
		expect(describeError('plain')).toBe('plain');
	});
});
