// The console's own link to the backend.
//
// Every management page already opens its own socket for its own data. This is
// the *chrome's* socket: it backs the connection indicator in the header and
// the command palette's index of entities, areas and automations, both of which
// have to survive a page change and a backend restart.
//
// Which means it is the one connection in the app that reconnects. The pages'
// sockets do not, deliberately — a page that lost its socket also lost its
// subscriptions, and silently reattaching would leave the user staring at stale
// rows they believe are live.

import { openConnection, type Connection } from './connection';
import type {
	AreaEntry,
	BusEvent,
	DeviceRegistryEntry,
	EntityRegistryEntry,
	EntityState,
	Subscription
} from './jarvisClient';
import { applyStateChanged } from './jarvisClient';

export type LinkStatus = 'connecting' | 'connected' | 'reconnecting' | 'offline';

/** Consecutive failed attempts before the indicator gives up and says "offline". */
export const OFFLINE_AFTER_ATTEMPTS = 3;

/**
 * Exponential backoff, capped. Deterministic — the jitter is a caller-supplied
 * 0..1 number, so a test can pass 0 and get exact values.
 */
export function backoffDelay(attempt: number, jitter = 0, base = 600, max = 8000): number {
	const n = Math.max(0, Math.floor(attempt));
	const raw = Math.min(base * Math.pow(2, n), max);
	// Up to +25% spread, so several tabs do not stampede the relay together.
	return Math.round(raw * (1 + 0.25 * Math.min(Math.max(jitter, 0), 1)));
}

/** The label the header shows for a status. */
export function statusLabel(status: LinkStatus): string {
	switch (status) {
		case 'connected':
			return 'LINK OK';
		case 'connecting':
			return 'LINKING';
		case 'reconnecting':
			return 'RECONNECTING';
		default:
			return 'OFFLINE';
	}
}

export interface LinkSnapshot {
	status: LinkStatus;
	states: EntityState[];
	areas: AreaEntry[];
	entries: EntityRegistryEntry[];
	devices: DeviceRegistryEntry[];
}

export type LinkListener = (snapshot: LinkSnapshot) => void;

const EMPTY: LinkSnapshot = {
	status: 'connecting',
	states: [],
	areas: [],
	entries: [],
	devices: []
};

export interface ConsoleLinkOptions {
	/** Injected in tests so retries do not really wait. */
	setTimeoutFn?: typeof setTimeout;
	clearTimeoutFn?: typeof clearTimeout;
	/** Injected in tests to make the backoff deterministic. */
	random?: () => number;
	connect?: typeof openConnection;
}

export class ConsoleLink {
	private snapshot: LinkSnapshot = EMPTY;
	private listeners = new Set<LinkListener>();
	private conn: Connection | null = null;
	private sub: Subscription | null = null;
	private stateMap = new Map<string, EntityState>();
	private retryTimer: ReturnType<typeof setTimeout> | null = null;
	private attempts = 0;
	private running = false;
	private everConnected = false;

	private readonly setTimeoutFn: typeof setTimeout;
	private readonly clearTimeoutFn: typeof clearTimeout;
	private readonly random: () => number;
	private readonly connect: typeof openConnection;

	/**
	 * The live connection, or null while down.
	 *
	 * Exposed so layout-level surfaces — the approvals banner — can subscribe to
	 * bus events on the socket this class is already keeping up, instead of
	 * opening a second one per tab and doubling the relay's connections.
	 * Deliberately read-only: reconnection stays this class's job, and a caller
	 * that held on to a stale Connection across a reconnect would silently stop
	 * receiving events.
	 */
	get connection(): Connection | null {
		return this.conn;
	}

	constructor(opts: ConsoleLinkOptions = {}) {
		this.setTimeoutFn = opts.setTimeoutFn ?? setTimeout;
		this.clearTimeoutFn = opts.clearTimeoutFn ?? clearTimeout;
		this.random = opts.random ?? Math.random;
		this.connect = opts.connect ?? openConnection;
	}

	get current(): LinkSnapshot {
		return this.snapshot;
	}

	get status(): LinkStatus {
		return this.snapshot.status;
	}

	subscribe(listener: LinkListener): () => void {
		this.listeners.add(listener);
		listener(this.snapshot);
		return () => this.listeners.delete(listener);
	}

	start(): void {
		if (this.running) return;
		this.running = true;
		this.attempts = 0;
		void this.dial();
	}

	stop(): void {
		this.running = false;
		if (this.retryTimer !== null) this.clearTimeoutFn(this.retryTimer);
		this.retryTimer = null;
		void this.sub?.unsubscribe();
		this.sub = null;
		this.conn?.close();
		this.conn = null;
		this.stateMap.clear();
		// A deliberate teardown is not a drop: the next start() is a fresh dial
		// and should say LINKING, not RECONNECTING at someone.
		this.attempts = 0;
		this.everConnected = false;
		this.emit({ status: 'connecting' });
	}

	/** Issue a service call on the console socket. Rejects when there is no link. */
	async callService(
		domain: string,
		service: string,
		data: Record<string, any> = {}
	): Promise<unknown> {
		if (!this.conn) throw new Error('no link to the backend');
		return this.conn.client.callService(domain, service, data);
	}

	private emit(patch: Partial<LinkSnapshot>): void {
		this.snapshot = { ...this.snapshot, ...patch };
		for (const listener of this.listeners) listener(this.snapshot);
	}

	private async dial(): Promise<void> {
		if (!this.running) return;
		this.emit({
			status: this.everConnected || this.attempts > 0 ? 'reconnecting' : 'connecting'
		});
		let connection: Connection;
		try {
			connection = await this.connect({
				onStatus: (s) => {
					if (s === 'closed' || s === 'error') this.onLost();
				}
			});
		} catch {
			this.onLost();
			return;
		}
		if (!this.running) {
			connection.close();
			return;
		}
		this.conn = connection;
		this.attempts = 0;
		this.everConnected = true;
		this.emit({ status: 'connected' });
		await this.load(connection);
	}

	private async load(connection: Connection): Promise<void> {
		const client = connection.client;
		const optional = async <T>(fn: () => Promise<T>, fallback: T): Promise<T> => {
			try {
				return (await fn()) ?? fallback;
			} catch {
				return fallback;
			}
		};

		const states = await optional(() => client.getStates(), [] as EntityState[]);
		if (this.conn !== connection) return;
		this.stateMap = new Map(states.map((s) => [s.entity_id, s]));
		this.emit({ states: [...this.stateMap.values()] });

		const areas = await optional(() => client.listAreas(), [] as AreaEntry[]);
		const entries = await optional(() => client.listEntities(), [] as EntityRegistryEntry[]);
		const devices = await optional(() => client.listDevices(), [] as DeviceRegistryEntry[]);
		if (this.conn !== connection) return;
		this.emit({ areas, entries, devices });

		try {
			this.sub = await client.subscribeEvents((event: BusEvent) => {
				if (this.conn !== connection) return;
				if (applyStateChanged(this.stateMap, event)) {
					this.emit({ states: [...this.stateMap.values()] });
				}
			}, 'state_changed');
		} catch {
			// A backend without event subscriptions still gives a usable palette,
			// just one that does not update itself.
		}
	}

	private onLost(): void {
		void this.sub?.unsubscribe();
		this.sub = null;
		this.conn = null;
		if (!this.running) return;
		if (this.retryTimer !== null) return;

		const attempt = this.attempts;
		this.attempts += 1;
		this.emit({
			status: this.attempts >= OFFLINE_AFTER_ATTEMPTS ? 'offline' : 'reconnecting'
		});
		const delay = backoffDelay(attempt, this.random());
		this.retryTimer = this.setTimeoutFn(() => {
			this.retryTimer = null;
			void this.dial();
		}, delay);
		(this.retryTimer as any)?.unref?.();
	}
}
