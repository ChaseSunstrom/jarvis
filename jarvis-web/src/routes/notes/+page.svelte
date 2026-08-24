<script lang="ts">
	/**
	 * Notes: documents Jarvis and you both write, in one folder of markdown.
	 *
	 * The editor is deliberately plain — a textarea and a preview — because the
	 * notes are files: whatever you type here is what somebody opening
	 * `config/notes/<slug>.md` in any editor will see, and a rich editor that
	 * wrote its own markup would break that promise on the first bold word.
	 *
	 * What this page adds over an editor is the two things a folder cannot do
	 * on its own: full-text search across every note, and the link graph —
	 * `[[wiki links]]` resolved, and the back-links that answer "what points at
	 * this?".
	 */
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import { ScreenState, type Status } from '$lib/ui';

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

	const status = $derived<Status>(
		loading ? 'loading' : err ? 'error' : notes.length === 0 ? 'empty' : 'ready'
	);
	const dirty = $derived(Boolean(open) && draft !== (open?.body ?? ''));

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

	async function show(row: NoteRow): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ note: NoteRow }>({
				type: 'jarvis/notes/get',
				// `note_id`, not `id`: every frame already has an `id`, and it is
				// the correlation number the server replies against.
				note_id: row.id
			});
			open = answer.note;
			draft = answer.note.body ?? '';
		} catch (e) {
			err = describeError(e);
		}
	}

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
			await show(open);
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
			await show(answer.note);
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
			if (open?.id === row.id) open = null;
			await load();
			toasts.success('Deleted', row.title);
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}
</script>

<svelte:head><title>Notes · Jarvis</title></svelte:head>

<h1 data-testid="notes-screen">NOTES</h1>
<p class="lede" data-testid="notes-lede">
	Markdown files in <code>config/notes/</code>. Jarvis writes here — research reports, things you
	asked it to note — and so can you, from any editor. <code>[[links]]</code> are resolved both ways.
</p>

<div class="toolbar">
	<input
		type="search"
		placeholder="search every note  ( / )"
		aria-label="Search notes"
		data-testid="notes-search"
		bind:value={query}
		onchange={load}
	/>
	{#if creating}
		<input
			type="text"
			placeholder="title"
			aria-label="New note title"
			data-testid="notes-new-title"
			bind:value={newTitle}
		/>
		<button class="btn" data-testid="notes-create" disabled={!newTitle.trim()} onclick={create}>
			CREATE
		</button>
	{:else}
		<button class="btn ghost" data-testid="notes-new" onclick={() => (creating = true)}>
			+ NEW NOTE
		</button>
	{/if}
</div>

{#if !supported}
	<p class="lede" data-testid="notes-unsupported">This server has no notes integration.</p>
{:else}
	<div class="split">
		<ScreenState
			{status}
			emptyTitle="No notes yet"
			emptyBody="Say “note that…” to Jarvis, or write one here. They are markdown files in your config directory."
			errorTitle="Couldn't read the notes"
			errorDetail={err}
			onretry={load}
			onreconnect={load}
			emptyTestid="notes-empty"
			errorTestid="notes-error"
		>
			{#snippet children()}
				<ul class="list" data-testid="notes-list">
					{#each notes as note (note.id)}
						<li data-testid="note-row-{note.id}" class:open={open?.id === note.id}>
							<button class="row" onclick={() => show(note)}>
								<span class="title">{note.title}</span>
								{#if note.excerpt}<span class="excerpt">{note.excerpt}</span>{/if}
								<span class="tags">
									{#each note.tags as tag (tag)}<span class="tag">{tag}</span>{/each}
								</span>
							</button>
							<button
								class="btn ghost danger"
								data-testid="note-delete-{note.id}"
								disabled={!!busy}
								onclick={() => remove(note)}
							>
								DELETE
							</button>
						</li>
					{/each}
				</ul>
			{/snippet}
		</ScreenState>

		{#if open}
			<section class="editor" data-testid="note-editor">
				<header>
					<h2 data-testid="note-title">{open.title}</h2>
					<button
						class="btn"
						data-testid="note-save"
						disabled={!dirty || busy === 'save'}
						onclick={save}
					>
						{busy === 'save' ? 'SAVING…' : dirty ? 'SAVE' : 'SAVED'}
					</button>
				</header>
				<textarea
					data-testid="note-body"
					aria-label="The note, in markdown"
					bind:value={draft}
					spellcheck="true"
				></textarea>
				{#if open.links.length || open.backlinks.length}
					<p class="links" data-testid="note-links">
						{#if open.links.length}<span>points at: {open.links.join(', ')}</span>{/if}
						{#if open.backlinks.length}<span>pointed at by: {open.backlinks.join(', ')}</span>{/if}
					</p>
				{/if}
			</section>
		{/if}
	</div>
{/if}

<style>
	.lede {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		margin: 0 0 var(--jv-space-3);
	}
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-3);
	}
	.split {
		display: grid;
		grid-template-columns: minmax(16rem, 1fr) 2fr;
		gap: var(--jv-space-4);
		align-items: start;
	}
	@media (max-width: 60rem) {
		.split {
			grid-template-columns: 1fr;
		}
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
	}
	.list li {
		display: grid;
		grid-template-columns: 1fr max-content;
		gap: var(--jv-space-2);
		align-items: center;
	}
	.list li.open .title {
		color: var(--jv-accent);
	}
	.row {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
		text-align: left;
		background: none;
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-2);
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	.row:hover,
	.row:focus-visible {
		border-color: var(--jv-line);
		background: var(--jv-surface-2);
	}
	.title {
		color: var(--jv-text);
	}
	.excerpt,
	.tags,
	.links {
		color: var(--jv-text-faint);
		font-size: var(--jv-fs-xs);
	}
	.tag {
		margin-right: var(--jv-space-2);
		text-transform: uppercase;
		letter-spacing: var(--jv-track-chrome);
	}
	.editor {
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-3);
		background: var(--jv-surface-1);
	}
	.editor header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
	}
	.editor h2 {
		margin: 0;
		font-size: var(--jv-fs-md);
	}
	textarea {
		width: 100%;
		min-height: var(--jv-measure-log);
		margin-top: var(--jv-space-2);
		background: var(--jv-surface-2);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-sm);
		color: var(--jv-text);
		font-family: var(--jv-font-mono);
		font-size: var(--jv-fs-sm);
		padding: var(--jv-space-2);
		resize: vertical;
	}
	.links {
		display: flex;
		gap: var(--jv-space-3);
		margin: var(--jv-space-2) 0 0;
	}
</style>
