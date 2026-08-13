// The console link is the one socket in the app that reconnects, so the parts
// worth testing are the ones a reconnect gets wrong: the backoff schedule, and
// the status the header shows on the way down and back up.
import { describe, it, expect, vi } from 'vitest';
import { ConsoleLink, OFFLINE_AFTER_ATTEMPTS, backoffDelay, statusLabel } from './consoleLink';
import type { Connection } from './connection';
import { JarvisClient } from './jarvisClient';

describe('backoffDelay', () => {
	it('doubles from the base', () => {
		expect(backoffDelay(0, 0)).toBe(600);
		expect(backoffDelay(1, 0)).toBe(1200);
		expect(backoffDelay(2, 0)).toBe(2400);
	});

	it('caps, so a backend that stays down does not push retries into next week', () => {
		expect(backoffDelay(20, 0)).toBe(8000);
		expect(backoffDelay(200, 0)).toBe(8000);
	});

	it('adds up to a quarter of jitter, so tabs do not stampede together', () => {
		expect(backoffDelay(0, 1)).toBe(750);
		expect(backoffDelay(0, 0.5)).toBe(675);
		// Out-of-range jitter is clamped rather than trusted.
		expect(backoffDelay(0, 5)).toBe(750);
		expect(backoffDelay(0, -5)).toBe(600);
	});

	it('treats a negative attempt as the first one', () => {
		expect(backoffDelay(-3, 0)).toBe(600);
	});
});

describe('statusLabel', () => {
	it('gives every status a distinct label', () => {
		const labels = (['connecting', 'connected', 'reconnecting', 'offline'] as const).map(
			statusLabel
		);
		expect(new Set(labels).size).toBe(4);
		expect(statusLabel('connected')).toBe('LINK OK');
		expect(statusLabel('offline')).toBe('OFFLINE');
	});
});

/** A fake `openConnection` whose sockets can be dropped on demand. */
function fakeConnector() {
	const opened: {
		conn: Connection;
		drop: () => void;
		sent: string[];
		reply: (id: number, result: unknown) => void;
	}[] = [];
	let fail = false;

	const connect = (handlers: { onStatus?: (s: string) => void } = {}) => {
		if (fail) return Promise.reject(new Error('refused'));
		const sent: string[] = [];
		const client = new JarvisClient((data) => sent.push(data), { timeoutMs: 0 });
		const conn: Connection = { client, socket: {} as WebSocket, close: () => {} };
		const entry = {
			conn,
			sent,
			drop: () => handlers.onStatus?.('closed'),
			reply: (id: number, result: unknown) =>
				client.handleMessage({ id, type: 'result', success: true, result })
		};
		opened.push(entry);
		handlers.onStatus?.('open');
		return Promise.resolve(conn);
	};

	return {
		opened,
		connect: connect as any,
		setFail: (v: boolean) => (fail = v)
	};
}

/** Settle the microtask queue so the link's async load steps run. */
const flush = async () => {
	for (let i = 0; i < 12; i += 1) await Promise.resolve();
};

describe('ConsoleLink', () => {
	it('starts connecting and reports connected once the socket is open', async () => {
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		const seen: string[] = [];
		link.subscribe((s) => seen.push(s.status));
		link.start();
		await flush();
		expect(seen[0]).toBe('connecting');
		expect(link.status).toBe('connected');
		link.stop();
	});

	it('publishes the states, registries and areas the palette indexes', async () => {
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		link.start();
		await flush();
		const socket = f.opened[0];
		// The link asks one at a time — states, then areas, entities, devices —
		// so each answer has to land before the next command is even sent.
		socket.reply(1, [{ entity_id: 'light.a', state: 'on', attributes: {} }]);
		await flush();
		socket.reply(2, [{ id: 'lab', name: 'Lab' }]);
		await flush();
		socket.reply(3, [{ entity_id: 'light.a', area_id: 'lab' }]);
		await flush();
		socket.reply(4, [{ id: 'dev', name: 'Dev' }]);
		await flush();
		expect(link.current.states).toHaveLength(1);
		expect(link.current.areas[0].name).toBe('Lab');
		expect(link.current.entries).toHaveLength(1);
		expect(link.current.devices).toHaveLength(1);
		link.stop();
	});

	it('goes to reconnecting when the socket drops, and retries on the backoff', async () => {
		vi.useFakeTimers();
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		link.start();
		await flush();
		expect(link.status).toBe('connected');

		f.opened[0].drop();
		expect(link.status).toBe('reconnecting');
		expect(f.opened).toHaveLength(1);

		vi.advanceTimersByTime(backoffDelay(0, 0));
		await flush();
		expect(f.opened).toHaveLength(2);
		expect(link.status).toBe('connected');
		link.stop();
		vi.useRealTimers();
	});

	it('gives up on the label after a few failures, but keeps retrying', async () => {
		vi.useFakeTimers();
		const f = fakeConnector();
		f.setFail(true);
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		link.start();
		await flush();
		expect(link.status).toBe('reconnecting');

		for (let attempt = 1; attempt < OFFLINE_AFTER_ATTEMPTS; attempt += 1) {
			vi.advanceTimersByTime(backoffDelay(attempt, 0));
			await flush();
		}
		expect(link.status).toBe('offline');

		// The backend comes back: the next scheduled retry still lands.
		f.setFail(false);
		vi.advanceTimersByTime(10_000);
		await flush();
		expect(link.status).toBe('connected');
		link.stop();
		vi.useRealTimers();
	});

	it('stop() cancels a pending retry', async () => {
		vi.useFakeTimers();
		const f = fakeConnector();
		f.setFail(true);
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		link.start();
		await flush();
		link.stop();
		f.setFail(false);
		vi.advanceTimersByTime(60_000);
		await flush();
		expect(f.opened).toHaveLength(0);
		vi.useRealTimers();
	});

	it('start() twice does not open two sockets', async () => {
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		link.start();
		link.start();
		await flush();
		expect(f.opened).toHaveLength(1);
		link.stop();
	});

	it('connects without indexing when the surface has no palette', async () => {
		// The HUD needs the socket — approvals ride on it — and has no use for a
		// whole house of states to feed a search box it cannot open.
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0, indexed: false });
		link.start();
		await flush();
		expect(link.status).toBe('connected');
		expect(f.opened[0].sent).toHaveLength(0);
		link.stop();
	});

	it('loads the index the moment indexing is turned on, without waiting for a reconnect', async () => {
		// Walking from the HUD to /devices must not leave the palette empty until
		// the next time the backend happens to drop.
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0, indexed: false });
		link.start();
		await flush();
		link.setIndexed(true);
		await flush();
		const socket = f.opened[0];
		expect(JSON.parse(socket.sent[0]).type).toBe('get_states');
		socket.reply(1, [{ entity_id: 'light.a', state: 'on', attributes: {} }]);
		await flush();
		expect(link.current.states).toHaveLength(1);

		// Turning it on again is not a second load, and the socket is not re-dialled.
		const before = socket.sent.length;
		link.setIndexed(true);
		await flush();
		expect(socket.sent).toHaveLength(before);
		expect(f.opened).toHaveLength(1);
		link.stop();
	});

	it('callService refuses rather than hanging when there is no link', async () => {
		const link = new ConsoleLink({ connect: fakeConnector().connect });
		await expect(link.callService('light', 'turn_on', {})).rejects.toThrow(/no link/);
	});

	it('callService goes out on the live socket', async () => {
		const f = fakeConnector();
		const link = new ConsoleLink({ connect: f.connect, random: () => 0 });
		link.start();
		await flush();
		void link.callService('light', 'turn_on', { entity_id: 'light.a' });
		const frames = f.opened[0].sent.map((s) => JSON.parse(s));
		expect(frames.some((m) => m.type === 'call_service' && m.domain === 'light')).toBe(true);
		link.stop();
	});
});
