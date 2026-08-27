// One section's socket, as a thing a section owns rather than re-types.
//
// Every management section dials the relay on mount, loads what it shows,
// and offers RECONNECT when the link drops — and every one of them carried
// the same forty lines to do it, including the two details that are easy to
// get wrong: a `dial` generation, because the socket being replaced reports
// its close asynchronously and a late 'closed' would overwrite the new
// socket's 'open'; and a `disposed` flag, because a connection that resolves
// after the page has gone must be closed rather than kept. Five settings
// sections would have been five more copies. This is the one.
//
// See `$lib/ui` OfflineState for why a page's socket does not reattach by
// itself: a reconnected socket has none of the page's subscriptions, so
// `connect()` is what RECONNECT runs — re-dial, re-load, re-subscribe.

import { openConnection, describeError, type Connection, type ConnectionStatus } from './connection';

export class SectionLink {
	conn = $state<Connection | null>(null);
	status = $state<ConnectionStatus>('connecting');
	/** The page's own failure, in words. Empty when there is none. */
	err = $state('');
	/** Mid-reconnect: the offline state's button says so and stops taking clicks. */
	redialling = $state(false);

	private disposed = false;
	private dial = 0;

	/**
	 * `onOpen` runs once the socket is up, with the connection: load, subscribe.
	 * `onClose` runs before a redial and on dispose: unsubscribe what `onOpen` made.
	 */
	constructor(
		private readonly onOpen: (conn: Connection) => Promise<void>,
		private readonly onClose?: () => void
	) {}

	/** The link's contribution to a screen's status: offline beats error beats ready. */
	get screen(): 'ready' | 'error' | 'offline' {
		return this.status === 'closed' || this.status === 'error'
			? 'offline'
			: this.err
				? 'error'
				: 'ready';
	}

	async connect(): Promise<void> {
		if (this.redialling) return;
		this.redialling = true;
		const mine = ++this.dial;
		this.onClose?.();
		this.conn?.close();
		this.conn = null;
		this.err = '';
		try {
			const connection = await openConnection({
				onStatus: (s) => {
					if (mine === this.dial) this.status = s;
				}
			});
			if (this.disposed || mine !== this.dial) {
				connection.close();
				return;
			}
			this.conn = connection;
			await this.onOpen(connection);
		} catch (e) {
			this.err = describeError(e);
		} finally {
			this.redialling = false;
		}
	}

	/** Mount: dial. Returns the teardown for `onMount` to hand back. */
	mount(): () => void {
		this.disposed = false;
		void this.connect();
		return () => this.dispose();
	}

	dispose(): void {
		this.disposed = true;
		this.onClose?.();
		this.conn?.close();
		this.conn = null;
	}
}
