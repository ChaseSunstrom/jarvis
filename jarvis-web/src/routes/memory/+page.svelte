<script lang="ts">
	/**
	 * What Jarvis remembers about you, and the two buttons that matter.
	 *
	 * The memory integration's promise is that this is the user's data: local,
	 * readable, deletable and portable. A promise like that is only true if
	 * there is a screen where you can read every note, see where each one came
	 * from — you said it, or Jarvis worked it out — delete one, and leave with
	 * the lot.
	 *
	 * So the two operations that are *not* offered to the model are the two
	 * given the most room here: EXPORT and FORGET EVERYTHING. The model may
	 * write a note and forget one; handing over the whole store or deleting all
	 * of it is the user's, and it is theirs through this page.
	 */
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import { ScreenState, type Status } from '$lib/ui';

	interface Entry {
		id: string;
		text: string;
		tags: string[];
		created: number;
		source: string;
		pinned?: boolean;
		redacted?: string[];
		conversation_id?: string;
	}

	let conn = $state<Connection | null>(null);
	let entries = $state<Entry[]>([]);
	let total = $state(0);
	let query = $state('');
	let loading = $state(true);
	let err = $state('');
	let supported = $state(true);
	let busy = $state('');
	let confirmingWipe = $state(false);

	const extracted = $derived(entries.filter((e) => e.source === 'extracted').length);
	const status = $derived<Status>(
		loading ? 'loading' : err ? 'error' : entries.length === 0 ? 'empty' : 'ready'
	);

	async function load(): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ entries: Entry[]; total: number }>({
				type: 'jarvis/memory/list',
				query
			});
			entries = answer.entries ?? [];
			total = answer.total ?? entries.length;
			supported = true;
		} catch (e) {
			if (isUnsupported(e)) supported = false;
			else err = describeError(e);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		let live: Connection | null = null;
		void (async () => {
			try {
				live = await openConnection();
				conn = live;
				await load();
			} catch (e) {
				err = describeError(e);
				loading = false;
			}
		})();
		return () => live?.close();
	});

	async function forget(entry: Entry): Promise<void> {
		if (!conn || busy) return;
		busy = entry.id;
		try {
			await conn.client.command({ type: 'jarvis/memory/forget', entry_id: entry.id });
			entries = entries.filter((e) => e.id !== entry.id);
			total = Math.max(0, total - 1);
			toasts.success('Forgotten', entry.text.slice(0, 60));
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function pin(entry: Entry): Promise<void> {
		if (!conn || busy) return;
		busy = entry.id;
		try {
			const answer = await conn.client.command<{ entry: Entry }>({
				type: 'jarvis/memory/pin',
				entry_id: entry.id,
				pinned: !entry.pinned
			});
			entries = entries.map((e) => (e.id === entry.id ? answer.entry : e));
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	/**
	 * Everything, as a file. Fetched over REST rather than the socket: this is a
	 * download, and a download is what "you can leave with your data" means in
	 * practice.
	 */
	function exportAll(format: 'json' | 'markdown'): void {
		window.open(`/api/memory/export?format=${format}`, '_blank');
	}

	async function wipe(): Promise<void> {
		if (!conn || busy) return;
		busy = 'wipe';
		try {
			const answer = await conn.client.command<{ wiped: number }>({
				type: 'jarvis/memory/forget',
				all: true
			});
			entries = [];
			total = 0;
			confirmingWipe = false;
			toasts.success(`Forgot ${answer.wiped} note(s)`, 'including the semantic index');
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	function when(seconds: number): string {
		return new Date(seconds * 1000).toLocaleDateString(undefined, {
			day: 'numeric',
			month: 'short',
			year: 'numeric'
		});
	}
</script>

<svelte:head><title>Memory · Jarvis</title></svelte:head>

<h1 data-testid="memory-screen">MEMORY</h1>
<p class="lede" data-testid="memory-lede">
	What Jarvis remembers between conversations. {total} note{total === 1 ? '' : 's'}, {extracted} of
	them worked out rather than told. It is all on this machine, in one file you can read.
</p>

<div class="toolbar">
	<input
		type="search"
		placeholder="search  ( / )"
		aria-label="Search what Jarvis remembers"
		data-testid="memory-search"
		bind:value={query}
		onchange={load}
	/>
	<button class="btn ghost" data-testid="memory-export-json" onclick={() => exportAll('json')}>
		EXPORT JSON
	</button>
	<button class="btn ghost" data-testid="memory-export-md" onclick={() => exportAll('markdown')}>
		EXPORT MARKDOWN
	</button>
	{#if confirmingWipe}
		<button
			class="btn danger"
			data-testid="memory-wipe-confirm"
			disabled={busy === 'wipe'}
			onclick={wipe}
		>
			{busy === 'wipe' ? '…' : 'YES — FORGET EVERYTHING'}
		</button>
		<button class="btn ghost" data-testid="memory-wipe-cancel" onclick={() => (confirmingWipe = false)}>
			CANCEL
		</button>
	{:else}
		<button class="btn ghost danger" data-testid="memory-wipe" onclick={() => (confirmingWipe = true)}>
			FORGET EVERYTHING
		</button>
	{/if}
</div>

{#if !supported}
	<p class="note" data-testid="memory-unsupported">
		This server has no memory integration configured.
	</p>
{:else}
	<ScreenState
		{status}
		emptyTitle="Nothing remembered yet"
		emptyBody="Tell Jarvis something worth keeping — “remember that the spare key is in the blue tin” — or let it work things out as you talk."
		errorTitle="Couldn't read what Jarvis remembers"
		errorDetail={err}
		onretry={load}
		onreconnect={load}
		testid="memory-state"
		emptyTestid="memory-empty"
		errorTestid="memory-error"
	>
		{#snippet children()}
		<ul class="notes" data-testid="memory-list">
			{#each entries as entry (entry.id)}
				<li data-testid="memory-entry-{entry.id}" class:pinned={entry.pinned}>
					<p class="text">{entry.text}</p>
					<p class="meta">
						<span class="source" data-testid="memory-source-{entry.id}">{entry.source}</span>
						<span>{when(entry.created)}</span>
						{#each entry.tags as tag (tag)}<span class="tag">{tag}</span>{/each}
						{#if entry.redacted?.length}
							<span class="tag" title="Something was scrubbed before this was stored">
								redacted: {entry.redacted.join(', ')}
							</span>
						{/if}
					</p>
					<span class="acts">
						<button
							class="btn ghost"
							data-testid="memory-pin-{entry.id}"
							disabled={!!busy}
							onclick={() => pin(entry)}
						>
							{entry.pinned ? 'UNPIN' : 'PIN'}
						</button>
						<button
							class="btn ghost danger"
							data-testid="memory-forget-{entry.id}"
							disabled={!!busy}
							onclick={() => forget(entry)}
						>
							FORGET
						</button>
					</span>
				</li>
			{/each}
		</ul>
		{/snippet}
	</ScreenState>
{/if}

<style>
	.lede,
	.note {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		margin: 0 0 var(--jv-space-3);
	}
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		align-items: center;
		margin: var(--jv-space-4) 0;
	}
	.notes {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.notes li {
		display: grid;
		grid-template-columns: 1fr max-content;
		gap: var(--jv-space-2);
		align-items: start;
		padding: var(--jv-space-3);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		background: var(--jv-surface-1);
	}
	.notes li.pinned {
		border-color: var(--jv-accent-deep);
	}
	.text {
		margin: 0;
		grid-column: 1;
		color: var(--jv-text);
	}
	.meta {
		grid-column: 1;
		margin: var(--jv-space-1) 0 0;
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		color: var(--jv-text-faint);
		font-size: var(--jv-fs-xs);
	}
	.source,
	.tag {
		text-transform: uppercase;
		letter-spacing: var(--jv-track-chrome);
	}
	.acts {
		grid-column: 2;
		grid-row: 1 / span 2;
		display: flex;
		gap: var(--jv-space-1);
	}
</style>
