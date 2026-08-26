<script lang="ts">
	/**
	 * The inbox: everything Jarvis said while you were not looking.
	 *
	 * Live over the same socket (`jarvis_notification` arrives as it happens)
	 * AND listed on open, because either alone is a gap: a surface that was
	 * closed would miss everything, and a surface that only polls would draw a
	 * finished task a minute after it finished.
	 *
	 * Deliberately not a toast. A toast is gone in four seconds and these
	 * arrive when nobody is watching; `Moment.svelte` is the shape, and this is
	 * where they accumulate.
	 */
	import { Button } from '$lib/ui';
	import { onMount } from 'svelte';
	import Moment from './Moment.svelte';
	import { describeError, type Connection } from '$lib/connection';
	import { isUnsupported, type Subscription } from '$lib/jarvisClient';

	interface Note {
		id: string;
		kind: string;
		title: string;
		body: string;
		at: number;
		read: boolean;
		source: string;
		link: string;
	}

	let { conn }: { conn: Connection | null } = $props();

	let notes = $state<Note[]>([]);
	let unread = $state(0);
	let supported = $state(true);
	let err = $state('');
	let open = $state(false);

	async function refresh(connection: Connection): Promise<void> {
		try {
			const answer = await connection.client.command<{ notifications: Note[]; unread: number }>(
				{ type: 'jarvis/notifications/list' }
			);
			notes = answer.notifications ?? [];
			unread = answer.unread ?? 0;
			supported = true;
		} catch (e) {
			if (isUnsupported(e)) supported = false;
			else err = describeError(e);
		}
	}

	$effect(() => {
		const connection = conn;
		if (!connection) return;
		void refresh(connection);
		let live: Subscription | null = null;
		void (async () => {
			try {
				live = await connection.client.subscribeEvents((event) => {
					const note = (event.data as { notification?: Note })?.notification;
					if (!note) return;
					notes = [note, ...notes.filter((n) => n.id !== note.id)];
					unread += note.read ? 0 : 1;
				}, 'jarvis_notification');
			} catch {
				// An older backend has no such event; the list still works.
			}
		})();
		return () => void live?.unsubscribe();
	});

	async function markRead(note: Note): Promise<void> {
		if (!conn) return;
		await conn.client.command({ type: 'jarvis/notifications/read', notification_id: note.id });
		notes = notes.map((n) => (n.id === note.id ? { ...n, read: true } : n));
		unread = Math.max(0, unread - 1);
	}

	async function dismiss(note: Note): Promise<void> {
		if (!conn) return;
		await conn.client.command({ type: 'jarvis/notifications/dismiss', notification_id: note.id });
		notes = notes.filter((n) => n.id !== note.id);
		if (!note.read) unread = Math.max(0, unread - 1);
	}

	async function readAll(): Promise<void> {
		if (!conn) return;
		await conn.client.command({ type: 'jarvis/notifications/read', all: true });
		notes = notes.map((n) => ({ ...n, read: true }));
		unread = 0;
	}
</script>

{#if supported}
	<section class="inbox" data-testid="notifications">
		<button
			class="head"
			onclick={() => (open = !open)}
			aria-expanded={open}
			data-testid="notifications-toggle"
		>
			<span class="disclose" aria-hidden="true">{open ? '▾' : '▸'}</span>
			<span class="word">Moments</span>
			<span class="count">{notes.length} · what Jarvis said while nobody was looking</span>
			{#if unread}
				<span class="badge" data-testid="notifications-unread" title="{unread} unread">{unread}</span>
			{/if}
		</button>

		{#if open}
			{#if err}
				<p class="err" role="alert" data-testid="notifications-error">{err}</p>
			{/if}
			{#if notes.length === 0}
				<p class="empty" data-testid="notifications-empty">
					Nothing yet. Finished jobs, reminders and briefings land here.
				</p>
			{:else}
				<div class="list" data-testid="notifications-list">
					{#each notes as note (note.id)}
						<Moment
							kind={note.kind}
							title={note.title}
							body={note.body}
							at={note.at}
							read={note.read}
							source={note.source}
							link={note.link}
							testid="moment-{note.id}"
							onread={() => markRead(note)}
							ondismiss={() => dismiss(note)}
						/>
					{/each}
				</div>
				{#if unread}
					<Button onclick={readAll} testid="notifications-read-all">
						MARK ALL READ
					</Button>
				{/if}
			{/if}
		{/if}
	</section>
{/if}

<style>
	/*
	 * The inbox is a flat panel whose head is the disclosure: collapsed by
	 * default with the unread count, because these arrive when you are not at
	 * the screen and a toast is the wrong shape for them.
	 */
	.inbox {
		display: flex;
		flex-direction: column;
		margin-bottom: var(--jv-space-4);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		width: 100%;
		background: none;
		border: none;
		padding: var(--jv-space-3) var(--jv-space-4);
		color: var(--jv-text-dim);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		text-align: left;
		cursor: pointer;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.head:hover,
	.head:focus-visible {
		color: var(--jv-text-bright);
	}
	.head[aria-expanded='true'] {
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.disclose {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-faint);
	}
	.count {
		flex: 1;
		min-width: 0;
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		letter-spacing: var(--jv-track-tight);
		text-transform: none;
		color: var(--jv-text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.badge {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		letter-spacing: var(--jv-track-tight);
		text-transform: none;
		color: var(--jv-accent);
		border: 1px solid color-mix(in srgb, var(--jv-accent) 40%, transparent);
		border-radius: var(--jv-radius-sm);
		padding: 0 var(--jv-space-2);
		white-space: nowrap;
	}
	.list {
		display: flex;
		flex-direction: column;
	}
	.empty,
	.err {
		margin: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		color: var(--jv-text-faint);
		font-size: var(--jv-fs-sm);
	}
	.err {
		color: var(--jv-danger-text);
	}
	.inbox :global(.btn) {
		margin: var(--jv-space-3) var(--jv-space-4);
		align-self: flex-start;
	}
	@media (max-width: 640px) {
		.count {
			display: none;
		}
	}
</style>
