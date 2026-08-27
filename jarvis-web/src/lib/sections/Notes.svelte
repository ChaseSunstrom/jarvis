<script lang="ts">
	/**
	 * Notes: documents Jarvis and you both write, in one folder of markdown.
	 *
	 * The editor is deliberately plain — a textarea, no preview widgets —
	 * because the notes are files: whatever you type here is what somebody
	 * opening `config/notes/<slug>.md` in any editor will see, and a rich editor
	 * that wrote its own markup would break that promise on the first bold word.
	 *
	 * What this page adds over a folder is the two things a folder cannot do:
	 * full-text search across every note, and the link graph — `[[wiki links]]`
	 * resolved, and the back-links that answer "what points at this?". The
	 * graph itself is the destination's hero (see `routes/knowledge/+layout`);
	 * the note that is open here is the node lit there, and it is the URL
	 * (`?open=<id>`) that says which, so a link to a note is a link to its
	 * point on the map.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
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
	import { Button, EmptyState, Field, Input, Panel, Pill, ScreenState, type Status } from '$lib/ui';
	import Markdown from '$lib/components/Markdown.svelte';

	interface NoteRow {
		id: string;
		title: string;
		tags: string[];
		updated: string;
		links: string[];
		backlinks: string[];
		excerpt?: string;
		body?: string;
	}

	let conn = $state<Connection | null>(null);
	let notes = $state<NoteRow[]>([]);
	let open = $state<NoteRow | null>(null);
	let draft = $state('');
	let query = $state('');
	let loading = $state(true);
	let err = $state('');
	let supported = $state(true);
	let busy = $state('');
	let creating = $state(false);
	let newTitle = $state('');

	let link = $state<ConnectionStatus>('connecting');
	/*
	 * Offline first, and it is a state this page did not have — the same gap
	 * Memory had (M44). The socket died and the list stayed on screen looking
	 * current; a note somebody is about to act on is exactly the wrong thing
	 * to show a stale copy of without saying so.
	 */
	const status = $derived<Status>(
		link === 'closed' || link === 'error'
			? 'offline'
			: loading
				? 'loading'
				: err
					? 'error'
					: notes.length === 0
						? 'empty'
						: 'ready'
	);
	const dirty = $derived(Boolean(open) && draft !== (open?.body ?? ''));
	/** READ draws the markdown; EDIT is the textarea (M106). A fresh open reads. */
	let mode = $state<'read' | 'edit'>('read');
	/** The note the URL asks for, which the graph sets by selecting a node. */
	const wanted = $derived(page.url.pathname.endsWith('/notes') ? page.url.searchParams.get('open') ?? '' : '');

	async function load(): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ notes: NoteRow[] }>({
				type: query ? 'jarvis/notes/search' : 'jarvis/notes/list',
				query
			});
			notes = answer.notes ?? [];
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

	async function show(id: string): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ note: NoteRow }>({
				type: 'jarvis/notes/get',
				// `note_id`, not `id`: every frame already has an `id`, and it is
				// the correlation number the server replies against.
				note_id: id
			});
			open = answer.note;
			draft = answer.note.body ?? '';
			mode = 'read';
		} catch (e) {
			err = describeError(e);
		}
	}

	/** Open a note: the URL first, so the graph lights the same node. */
	function pick(id: string): void {
		void goto(`/knowledge/notes?open=${encodeURIComponent(id)}`, { noScroll: true, keepFocus: true });
		void show(id);
	}

	// The URL is the selection. A node clicked on the graph, a back button, a
	// link from a note — all arrive here, and the editor follows.
	$effect(() => {
		const id = wanted;
		if (!conn || !id || open?.id === id) return;
		void show(id);
	});

	async function save(): Promise<void> {
		if (!conn || !open || busy) return;
		busy = 'save';
		try {
			await conn.client.command({
				type: 'jarvis/notes/update',
				note_id: open.id,
				body: draft
			});
			toasts.success('Saved', open.title);
			await load();
			await show(open.id);
			touchKnowledge();
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function create(): Promise<void> {
		if (!conn || !newTitle.trim() || busy) return;
		busy = 'create';
		try {
			const answer = await conn.client.command<{ note: NoteRow }>({
				type: 'jarvis/notes/create',
				title: newTitle.trim(),
				body: ''
			});
			newTitle = '';
			creating = false;
			await load();
			pick(answer.note.id);
			touchKnowledge();
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function remove(row: NoteRow): Promise<void> {
		if (!conn || busy) return;
		busy = row.id;
		try {
			await conn.client.command({ type: 'jarvis/notes/delete', note_id: row.id });
			if (open?.id === row.id) {
				open = null;
				void goto('/knowledge/notes', { noScroll: true, keepFocus: true });
			}
			await load();
			toasts.success('Deleted', row.title);
			touchKnowledge();
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	function when(iso: string): string {
		const d = new Date(iso);
		return Number.isNaN(d.getTime())
			? ''
			: d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
	}
</script>

<p class="lede" data-testid="notes-lede">
	Markdown files in <code>config/notes/</code>. Jarvis writes here — research reports, things you
	asked it to note — and so can you, from any editor. <code>[[links]]</code> are resolved both ways.
</p>

{#if !supported}
	<p class="lede" data-testid="notes-unsupported">This server has no notes integration.</p>
{:else}
	<div class="split">
		<div class="list-side">
			<form
				class="find"
				onsubmit={(e) => {
					e.preventDefault();
					void load();
				}}
			>
				<label class="sr" for="notes-search">Search notes</label>
				<input
					id="notes-search"
					type="search"
					class="search"
					placeholder="Search every note  ( / )"
					data-testid="notes-search"
					data-jv-filter
					bind:value={query}
				/>
				{#if creating}
					<Input bind:value={newTitle} placeholder="Title" testid="notes-new-title" />
					<Button testid="notes-create" disabled={!newTitle.trim() || busy === 'create'} onclick={create}>
						Create
					</Button>
				{:else}
					<Button testid="notes-new" onclick={() => (creating = true)}>+ New note</Button>
				{/if}
			</form>

			<ScreenState
				{status}
				emptyTitle="No notes yet"
				emptyBody="Say “note that…” to Jarvis, or write one here. They are markdown files in your config directory."
				errorTitle="Couldn't read the notes"
				errorDetail={err}
				onretry={load}
				onreconnect={reconnect}
				emptyTestid="notes-empty"
				errorTestid="notes-error"
			>
				{#snippet children()}
					<ul class="list" data-testid="notes-list">
						{#each notes as note (note.id)}
							<li data-testid="note-row-{note.id}" class:open={open?.id === note.id} data-jv-row>
								<button class="row" type="button" onclick={() => pick(note.id)}>
									<span class="title">{note.title}</span>
									{#if note.excerpt}<span class="excerpt">{note.excerpt}</span>{/if}
									<span class="meta">
										{#if note.updated}<span class="when">{when(note.updated)}</span>{/if}
										{#each note.tags as tag (tag)}<Pill>{tag}</Pill>{/each}
									</span>
								</button>
								<span class="act">
									<Button variant="danger" testid="note-delete-{note.id}" disabled={!!busy} onclick={() => remove(note)}>
										Delete
									</Button>
								</span>
							</li>
						{/each}
					</ul>
				{/snippet}
			</ScreenState>
		</div>

		{#if open}
			<!-- Captured: a snippet is its own function, and TypeScript cannot carry
			     the `{#if open}` narrowing into it. -->
			{@const note = open}
			<div class="editor-side">
				<Panel title={note.title} meta={dirty ? 'edited' : busy === 'save' ? 'saving' : 'saved'} live={dirty} testid="note-editor">
					{#snippet children()}
						<h2 class="sr" data-testid="note-title">{note.title}</h2>
						<!-- Read first, edit on request (M106): a note is prose Jarvis or
						     a person wrote in markdown, and it used to open straight into
						     a monospace textarea — a research report as raw characters.
						     An edited note stays in the editor until it is saved or the
						     next note is opened; a fresh open reads. -->
						<div class="mode" role="tablist" aria-label="Read or edit">
							<Button testid="note-mode-read" variant={mode === 'read' ? 'primary' : undefined} onclick={() => (mode = 'read')} disabled={dirty}
								title={dirty ? 'Save or discard the edit to read it rendered' : 'Read the note as it was written'}>READ</Button>
							<Button testid="note-mode-edit" variant={mode === 'edit' ? 'primary' : undefined} onclick={() => (mode = 'edit')}>EDIT</Button>
						</div>
						{#if mode === 'read'}
							<div class="read" data-testid="note-read">
								<Markdown text={draft} />
							</div>
						{:else}
							<Field label="The note, in markdown">
								<Input bind:value={draft} rows={16} mono testid="note-body" />
							</Field>
						{/if}
						<div class="foot">
							<div class="links" data-testid="note-links">
								{#if note.links.length}
									<span class="k">points at</span>
									{#each note.links as target (target)}
										<Button testid="note-link-{target}" onclick={() => pick(target)}>{target}</Button>
									{/each}
								{/if}
								{#if note.backlinks.length}
									<span class="k">pointed at by</span>
									{#each note.backlinks as source (source)}
										<Button testid="note-backlink-{source}" onclick={() => pick(source)}>{source}</Button>
									{/each}
								{/if}
								{#if !note.links.length && !note.backlinks.length}
									<span class="k">No links yet — write <code>[[a title]]</code> to make one.</span>
								{/if}
							</div>
							<Button variant="primary" testid="note-save" disabled={!dirty || busy === 'save'} onclick={save}>
								{busy === 'save' ? 'SAVING…' : dirty ? 'SAVE' : 'SAVED'}
							</Button>
						</div>
					{/snippet}
				</Panel>
			</div>
		{:else}
			<!--
			  An empty right-hand pane said nothing about itself: half the screen
			  blank, on a page whose whole point is reading. It says what it is
			  for now, which is the difference between "loading" and "pick one".
			-->
			<div class="editor-side placeholder" data-testid="note-none-open">
				<EmptyState
					title="Nothing open"
					body="Pick a note on the left, or a point on the graph, to read or edit it. Jarvis writes here too — research reports land as notes."
				/>
			</div>
		{/if}
	</div>
{/if}

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
		max-width: 70ch;
	}
	.lede code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
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
	.split {
		display: grid;
		grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
		gap: var(--jv-space-5);
		align-items: start;
	}
	.find {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-3);
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
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		background: var(--jv-panel);
		overflow: hidden;
	}
	.list li {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--jv-space-2);
		border-bottom: 1px solid var(--jv-line-hair);
		transition: background var(--jv-dur-fast) var(--jv-ease-out);
	}
	.list li:last-child {
		border-bottom: 0;
	}
	.list li:hover {
		background: var(--jv-wash);
	}
	.list li.open {
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.row {
		display: grid;
		gap: var(--jv-space-1);
		text-align: left;
		background: none;
		border: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		color: inherit;
		font: inherit;
		cursor: pointer;
		min-width: 0;
	}
	.row:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: calc(-1 * var(--jv-focus-offset));
	}
	.title {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	li.open .title {
		color: var(--jv-text-bright);
	}
	.excerpt {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.when {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	/* The delete is there for the keyboard and on hover; not a red button beside every note. */
	.act {
		padding-right: var(--jv-space-3);
		opacity: 0;
		transition: opacity var(--jv-dur-fast) var(--jv-ease-out);
	}
	.list li:hover .act,
	.act:focus-within {
		opacity: 1;
	}
	.mode {
		display: flex;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-2);
	}
	.read {
		min-height: calc(var(--jv-space-7) * 4);
		padding: var(--jv-space-2) var(--jv-space-3);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-sm);
		line-height: 1.5;
		overflow-wrap: anywhere;
	}
	.editor-side {
		min-width: 0;
	}
	/* The markdown is data, set in mono on the sunken ground. */
	.editor-side :global(textarea.in) {
		background: var(--jv-surface-sunken);
		font-size: var(--jv-fs-xs);
		line-height: 1.7;
		min-height: var(--jv-measure-log);
	}
	.foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		margin-top: var(--jv-space-3);
	}
	.links {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
		min-width: 0;
	}
	.links .k {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.links code {
		font-family: var(--jv-font-chrome);
	}
	.placeholder {
		display: flex;
		align-items: stretch;
	}
	.placeholder :global(.empty) {
		flex: 1;
	}
	@media (max-width: 900px) {
		.split {
			grid-template-columns: minmax(0, 1fr);
		}
		.act {
			opacity: 1;
		}
	}
</style>
