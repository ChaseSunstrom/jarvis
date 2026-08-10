<script lang="ts">
	/**
	 * Tier-3 actions waiting on a human.
	 *
	 * Global rather than a page, and that is the whole design. An approval is
	 * raised by whatever the assistant was asked to do — it can arrive while you
	 * are on /devices, or mid-sentence on the HUD — and a request that expires
	 * unseen is indistinguishable from Jarvis ignoring you. So this lives in the
	 * layout and puts itself in front of whatever is on screen.
	 *
	 * The console had no way to approve anything at all before this: the gate
	 * fired, the model was told to wait, and the only surface that could say yes
	 * was the phone.
	 *
	 * What it deliberately does NOT do is decide anything. The request was pinned
	 * server-side when it was raised — fuzzy targets already resolved to concrete
	 * entity ids — so what runs is what is shown here, and a second click cannot
	 * run it twice because jarvis-core pops the request before executing.
	 */
	import { onMount } from 'svelte';
	import { describeError, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import type { BusEvent, PendingApproval, Subscription } from '$lib/jarvisClient';

	let { conn }: { conn: Connection | null } = $props();

	let pending = $state<PendingApproval[]>([]);
	let busy = $state('');
	let err = $state('');
	/** Ticks once a second so the countdown is honest. */
	let now = $state(Date.now());

	const idOf = (req: PendingApproval): string => req.request_id ?? req.id ?? '';

	function upsert(req: PendingApproval): void {
		const id = idOf(req);
		if (!id) return;
		pending = [...pending.filter((p) => idOf(p) !== id), req];
	}

	function drop(id: string): void {
		pending = pending.filter((p) => idOf(p) !== id);
	}

	/** Seconds left, or null when the server gave no expiry. */
	function secondsLeft(req: PendingApproval): number | null {
		if (!req.expires_at) return null;
		// `expires_at` is epoch SECONDS from Python's time.time().
		return Math.max(0, Math.round(req.expires_at - now / 1000));
	}

	/** The arguments, rendered compactly — this is what the human is agreeing to. */
	function summarise(req: PendingApproval): string {
		const args = req.arguments ?? {};
		const parts = Object.entries(args)
			.filter(([, v]) => v !== null && v !== undefined && v !== '')
			.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : JSON.stringify(v)}`);
		return parts.join(' · ') || 'no arguments';
	}

	async function resolve(req: PendingApproval, approved: boolean): Promise<void> {
		const id = idOf(req);
		if (!conn || !id) return;
		busy = id;
		err = '';
		try {
			const result = await conn.client.resolveApproval(id, approved);
			drop(id);
			if (result?.status === 'error') {
				// Expired or already used. Saying so is better than a silent
				// disappearance, because the action did NOT happen.
				toasts.error(`${req.tool} was not run`, result.error ?? 'the request had expired');
			} else {
				toasts.success(approved ? `Approved ${req.tool}` : `Denied ${req.tool}`);
			}
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not answer for ${req.tool}`, describeError(e));
		} finally {
			busy = '';
		}
	}

	onMount(() => {
		const ticker = setInterval(() => {
			now = Date.now();
			// Drop what the server has already expired, so the card cannot sit at
			// "0s" offering a button that can no longer do anything.
			pending = pending.filter((p) => (secondsLeft(p) ?? 1) > 0);
		}, 1000);
		return () => clearInterval(ticker);
	});

	// Re-subscribes whenever the connection is replaced (a reconnect gives a new
	// client), and catches up on anything raised while the socket was down.
	$effect(() => {
		const connection = conn;
		if (!connection) return;
		let disposed = false;
		const subs: Subscription[] = [];

		(async () => {
			try {
				const current = await connection.client.pendingApprovals();
				if (!disposed) for (const req of current) upsert(req);
			} catch {
				// An older backend has no such service. The live events below
				// still work, so this is not worth an error banner.
			}
			try {
				subs.push(
					await connection.client.subscribeEvents((event: BusEvent) => {
						upsert(event.data as PendingApproval);
					}, 'jarvis_approval_required')
				);
				subs.push(
					await connection.client.subscribeEvents((event: BusEvent) => {
						// Answered somewhere else — the phone, a script, another tab.
						drop(String(event.data?.request_id ?? event.data?.id ?? ''));
					}, 'jarvis_approval_resolved')
				);
			} catch (e) {
				if (!disposed) err = describeError(e);
			}
		})();

		return () => {
			disposed = true;
			for (const sub of subs) void sub.unsubscribe();
		};
	});
</script>

{#if pending.length}
	<section class="approvals" data-testid="approvals" aria-live="assertive">
		<div class="head">
			<span class="mark" aria-hidden="true">[ ! ]</span>
			<span>{pending.length} action{pending.length === 1 ? '' : 's'} waiting on you</span>
		</div>

		{#if err}<p class="err" data-testid="approval-error" role="alert">{err}</p>{/if}

		{#each pending as req (idOf(req))}
			{@const left = secondsLeft(req)}
			<div class="req" data-testid="approval-{req.tool}">
				<div class="what">
					<b>{req.tool}</b>
					{#if req.description}<span class="desc">{req.description}</span>{/if}
					<span class="args" data-testid="approval-args-{req.tool}">{summarise(req)}</span>
				</div>
				{#if left !== null}
					<span class="left" data-testid="approval-expiry-{req.tool}">{left}s</span>
				{/if}
				<button
					type="button"
					class="btn approve"
					data-testid="approve-{req.tool}"
					disabled={busy === idOf(req)}
					onclick={() => resolve(req, true)}
				>
					APPROVE
				</button>
				<button
					type="button"
					class="btn ghost danger"
					data-testid="deny-{req.tool}"
					disabled={busy === idOf(req)}
					onclick={() => resolve(req, false)}
				>
					DENY
				</button>
			</div>
		{/each}
	</section>
{/if}

<style>
	.approvals {
		position: sticky;
		top: 0;
		z-index: 40;
		border: 1px solid var(--jv-warn, #ffb347);
		border-left: 3px solid var(--jv-warn, #ffb347);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-panel);
		box-shadow: var(--jv-elev-panel);
		padding: var(--jv-space-3);
		margin-bottom: var(--jv-space-3);
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-warn, #ffb347);
		margin-bottom: var(--jv-space-2);
	}
	.req {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		padding: var(--jv-space-2) 0;
		border-top: 1px dashed var(--jv-line-hair);
	}
	.what {
		flex: 1 1 14rem;
		min-width: 0;
	}
	.what b {
		color: var(--jv-text-bright);
		font-weight: 500;
	}
	.desc,
	.args {
		display: block;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.left {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-warn, #ffb347);
	}
	.approve {
		border-color: var(--jv-ok, #35d08a);
		color: var(--jv-ok, #35d08a);
	}
</style>
