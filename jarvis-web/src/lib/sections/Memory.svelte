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
	 *
	 * Every entry is a point on the destination's graph; `?entry=<id>` in the
	 * URL — set by selecting that point — is the one this list scrolls to and
	 * marks.
	 */
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import {
		openConnection,
		describeError,
		type Connection,
		type ConnectionStatus
	} from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import { touchKnowledge } from '$lib/knowledge/store.svelte';
	import { toasts } from '$lib/toast';
	import { Button, Pill, ScreenState, type Status } from '$lib/ui';

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
	let link = $state<ConnectionStatus>('connecting');

	const extracted = $derived(entries.filter((e) => e.source === 'extracted').length);
	/** The entry the URL points at — the node picked on the graph. */
	const picked = $derived(page.url.pathname.endsWith('/memory') ? page.url.searchParams.get('entry') ?? '' : '');
	/*
	 * Offline is first, and it is a state this page did not have.
	 *
	 * Everything below the fold here is a list read once over a socket. When
	 * that socket died the page kept showing the list it had, which is the
	 * worst of the four states to get wrong on THIS screen: what Jarvis
	 * remembers is exactly the thing you would act on, and stale is
	 * indistinguishable from current unless the page says so.
	 */
	const status = $derived<Status>(
		link === 'closed' || link === 'error'
			? 'offline'
			: loading
				? 'loading'
				: err
					? 'error'
					: entries.length === 0
						? 'empty'
						: 'ready'
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

	/** Reconnect dials again: `load()` would reuse the socket that just died. */
	async function reconnect(): Promise<void> {
		conn?.close();
		conn = null;
		link = 'connecting';
		err = '';
		loading = true;
		try {
			conn = await openConnection({ onStatus: (s) => (link = s) });
			await load();
		} catch (e) {
			err = describeError(e);
			loading = false;
		}
	}

	onMount(() => {
		let live: Connection | null = null;
		void (async () => {
			try {
				live = await openConnection({ onStatus: (s) => (link = s) });
				conn = live;
				await load();
			} catch (e) {
				err = describeError(e);
				loading = false;
			}
		})();
		return () => live?.close();
	});

	// The picked entry is brought into view once the list has it. Not on every
	// render: scrolling the page under somebody who is reading is the thing to
	// avoid, so this follows the URL and nothing else.
	$effect(() => {
		const id = picked;
		if (!id || loading) return;
		void tick().then(() => {
			document
				.querySelector(`[data-testid="memory-entry-${CSS.escape(id)}"]`)
				?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		});
	});

	async function forget(entry: Entry): Promise<void> {
		if (!conn || busy) return;
		busy = entry.id;
		try {
			await conn.client.command({ type: 'jarvis/memory/forget', entry_id: entry.id });
			entries = entries.filter((e) => e.id !== entry.id);
			total = Math.max(0, total - 1);
			toasts.success('Forgotten', entry.text.slice(0, 60));
			touchKnowledge();
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
			touchKnowledge();
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

<p class="lede" data-testid="memory-lede">
	What Jarvis remembers between conversations. {total} note{total === 1 ? '' : 's'}, {extracted} of
	them worked out rather than told. It is all on this machine, in one file you can read.
</p>

{#if !supported}
	<p class="lede" data-testid="memory-unsupported">
		This server has no memory integration configured.
	</p>
{:else}
	<form
		class="bar"
		onsubmit={(e) => {
			e.preventDefault();
			void load();
		}}
	>
		<label class="sr" for="memory-search">Search what Jarvis remembers</label>
		<input
			id="memory-search"
			type="search"
			class="search"
			placeholder="Search  ( / )"
			data-testid="memory-search"
			data-jv-filter
			bind:value={query}
		/>
		<span class="quiet">
			<Button testid="memory-export-json" onclick={() => exportAll('json')}>Export JSON</Button>
			<Button testid="memory-export-md" onclick={() => exportAll('markdown')}>Export markdown</Button>
		</span>
		<!--
		  The one irreversible control in the console, and the user's alone —
		  the model has no tool that can reach it. One click arms it; the red
		  button is the second one.
		-->
		{#if confirmingWipe}
			<Button variant="danger" testid="memory-wipe-confirm" disabled={busy === 'wipe'} onclick={wipe}>
				{busy === 'wipe' ? '…' : 'Yes — forget everything'}
			</Button>
			<Button testid="memory-wipe-cancel" onclick={() => (confirmingWipe = false)}>Cancel</Button>
		{:else}
			<Button variant="danger" testid="memory-wipe" onclick={() => (confirmingWipe = true)}>
				Forget everything
			</Button>
		{/if}
	</form>

	<ScreenState
		{status}
		emptyTitle="Nothing remembered yet"
		emptyBody="Tell Jarvis something worth keeping — “remember that the spare key is in the blue tin” — or let it work things out as you talk."
		errorTitle="Couldn't read what Jarvis remembers"
		errorDetail={err}
		onretry={load}
		onreconnect={reconnect}
		testid="memory-state"
		emptyTestid="memory-empty"
		errorTestid="memory-error"
	>
		{#snippet children()}
			<ul class="notes" data-testid="memory-list">
				{#each entries as entry (entry.id)}
					<li
						data-testid="memory-entry-{entry.id}"
						class:pinned={entry.pinned}
						class:picked={picked === entry.id}
					>
						<p class="text">{entry.text}</p>
						<div class="meta">
							<span class="source" data-testid="memory-source-{entry.id}">{entry.source}</span>
							<span class="dot" aria-hidden="true">·</span>
							<span>{when(entry.created)}</span>
							{#each entry.tags as tag (tag)}<Pill>{tag}</Pill>{/each}
							{#if entry.pinned}<Pill tone="live">pinned</Pill>{/if}
							{#if entry.redacted?.length}
								<Pill tone="warn">redacted: {entry.redacted.join(', ')}</Pill>
							{/if}
						</div>
						<span class="acts">
							<Button testid="memory-pin-{entry.id}" disabled={!!busy} onclick={() => pin(entry)}>
								{entry.pinned ? 'Unpin' : 'Pin'}
							</Button>
							<Button variant="danger" testid="memory-forget-{entry.id}" disabled={!!busy} onclick={() => forget(entry)}>
								Forget
							</Button>
						</span>
					</li>
				{/each}
			</ul>
		{/snippet}
	</ScreenState>
{/if}

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
		max-width: 70ch;
	}
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		clip-path: inset(50%);
		white-space: nowrap;
		border: 0;
	}
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-4);
	}
	.search {
		flex: 1 1 12rem;
		min-width: 0;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.search::placeholder {
		color: var(--jv-text-faint);
	}
	.search:hover {
		border-color: var(--jv-line);
	}
	.quiet {
		display: inline-flex;
		gap: var(--jv-space-2);
	}
	.notes {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		background: var(--jv-panel);
		overflow: hidden;
	}
	.notes li {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-areas: 'text acts' 'meta acts';
		gap: var(--jv-space-1) var(--jv-space-3);
		align-items: center;
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		transition: background var(--jv-dur-base) var(--jv-ease-out);
	}
	.notes li:last-child {
		border-bottom: 0;
	}
	.notes li:hover {
		background: var(--jv-wash);
	}
	/* The picked entry is the one lit on the graph: the same wash and rule. */
	.notes li.picked {
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.text {
		grid-area: text;
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		overflow-wrap: anywhere;
	}
	li.picked .text {
		color: var(--jv-text-bright);
	}
	.meta {
		grid-area: meta;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.source {
		text-transform: uppercase;
	}
	.dot {
		opacity: 0.6;
	}
	/* Pin and forget are there for the keyboard and on hover; a row is a fact, not a control panel. */
	.acts {
		grid-area: acts;
		display: flex;
		gap: var(--jv-space-1);
		opacity: 0;
		transition: opacity var(--jv-dur-fast) var(--jv-ease-out);
	}
	.notes li:hover .acts,
	.notes li.picked .acts,
	.acts:focus-within {
		opacity: 1;
	}
	@media (max-width: 900px) {
		.acts {
			opacity: 1;
		}
	}
</style>
