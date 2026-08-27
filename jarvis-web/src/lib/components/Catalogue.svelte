<!--
@component
Something to browse: the catalogue, first on the tools page.

"I can't browse the tools from the settings" (M65). The browse control was a
button inside a fold, and what it opened was empty — no source was configured
by default, so there was nothing behind it. Both halves are fixed here: the
server now offers its own shipped skills as a source (`bundled`), and this
draws them ABOVE the folds, filtered by the page's one search, with each
entry's state on its row — INSTALLED, or one INSTALL that goes through the
existing plan-then-approve flow, unchanged.

The row shows what a person needs to decide: the id, what it is, where it is
from, and what it asks for. Installing is still a second decision, in a
dialog that shows the exact ref, the hash, every file, and every program in
the payload — that dialog moved here from `Extensions.svelte` with the flow,
so there is one way to the catalogue, not two.

MCP is one line, not rows: a catalogue cannot offer a stdio server (that is a
program on this host, and only the file a person edits may name one), the
repository hosts no http server to list, so the way in is add-by-URL in the
MCP fold — and the line's one control opens that form.

It draws its own panel, unlike the fold bodies: it is not a fold, and the
point is that nothing has to be opened to see it.
-->
<script lang="ts">
	import { Button, Dialog, EmptyState, ErrorState, Pill, SkeletonRows } from '$lib/ui';
	import { staggerStyle } from '$lib/motion';
	import { describeError, type Connection } from '$lib/connection';

	interface CatalogEntry {
		id: string;
		kind: string;
		source: string;
		url: string;
		version: string;
		description: string;
		author: string;
		permissions: string[];
		ref: string;
		sha256: string;
		/** Something of this kind and id is in the registry now. */
		installed: boolean;
	}

	interface InstallPlan {
		/** MCP only (`kind === 'mcp'`: a server is added; nothing is downloaded): the URL and the tier its tools will run at. */
		url?: string;
		tier?: number;
		/** MCP only: the server's one-sentence plan, in words. */
		note?: string;
		id: string;
		kind: string;
		source: string;
		ref: string;
		sha256: string;
		permissions: string[];
		files: string[];
		hooks: string[];
		warning: string;
		description?: string;
	}

	interface SourceError {
		source: string;
		error: string;
	}

	interface Props {
		/** The page's connection. Null while it is dialling. */
		conn: Connection | null;
		/** The page's one search (M55): an entry matches when any of its words do. */
		query?: string;
		/** The link is down: what is on screen is the last read. */
		offline?: boolean;
		/** Open the MCP fold's add-by-URL form. */
		onaddmcp?: () => void;
		/** Something landed, so the lists below are stale. */
		oninstalled?: (id: string) => void;
	}
	let { conn, query = '', offline = false, onaddmcp, oninstalled }: Props = $props();

	let entries = $state<CatalogEntry[]>([]);
	let sources = $state<string[]>([]);
	let sourceErrors = $state<SourceError[]>([]);
	let loading = $state(true);
	let loaded = $state(false);
	let err = $state('');
	/** The command itself failed, as opposed to a source it reported on. */
	let failed = $state(false);
	let busy = $state('');
	let planError = $state('');
	let proposal = $state<InstallPlan | null>(null);
	/** Servers a registry lists that this house cannot install (M108). */
	let skipped = $state(0);
	/** How many the catalogue offers with no query, for "N of M match". */
	let total = $state(0);

	// The registries search on their side (the MCP registry lists thousands;
	// a page of a hundred is what comes back), so a changed query is a new
	// browse — after a pause, not on every keystroke.
	let queryTimer: ReturnType<typeof setTimeout> | null = null;
	let lastQuery = '';
	$effect(() => {
		const q = query.trim();
		if (!loaded || q === lastQuery) return;
		if (queryTimer) clearTimeout(queryTimer);
		queryTimer = setTimeout(() => {
			lastQuery = q;
			void load();
		}, 400);
	});

	async function load(): Promise<void> {
		if (!conn) return;
		// The skeleton is for the first paint only. A re-read after an install
		// keeps the rows on screen: a list that blanks and comes back is a list
		// that looks like it lost something.
		loading = !loaded;
		try {
			const answer = await conn.client.command<{
				entries?: CatalogEntry[];
				sources?: string[];
				errors?: SourceError[];
				error?: string;
				skipped?: number;
			}>({ type: 'jarvis/extensions/browse', query: query.trim() });
			entries = answer.entries ?? [];
			// The server filters by the query (the registries search on their
			// side), so "N of M match" needs the whole from an unfiltered read —
			// made here when a query got in before one (CI typed before the
			// first browse had answered: "1 of 1 match").
			if (!query.trim()) total = entries.length;
			else if (total === 0) {
				const whole = await conn.client.command<{ entries?: CatalogEntry[] }>({ type: 'jarvis/extensions/browse' });
				total = whole.entries?.length ?? entries.length;
			}
			sources = answer.sources ?? [];
			sourceErrors = answer.errors ?? [];
			skipped = answer.skipped ?? 0;
			err = answer.error ?? '';
			failed = false;
		} catch (e) {
			err = describeError(e);
			failed = true;
		} finally {
			loading = false;
			loaded = true;
		}
	}

	/**
	 * Which of the four states this is. "No source" is the EMPTY state even
	 * though the server puts it in `error` — that is what would be here and
	 * how it gets there, not a fault — while a source that could not be read,
	 * or a command that did not answer, is the error state with the reason.
	 */
	const view = $derived<'loading' | 'error' | 'empty' | 'rows'>(
		loading
			? 'loading'
			: failed
				? 'error'
				: sources.length === 0
					? 'empty'
					: err && entries.length === 0
						? 'error'
						: 'rows'
	);

	$effect(() => {
		if (conn) void load();
	});

	function matchesQuery(entry: CatalogEntry, q: string): boolean {
		const needle = q.trim().toLowerCase();
		if (!needle) return true;
		return [entry.id, entry.description, entry.source, entry.author, entry.ref, ...entry.permissions]
			.join(' ')
			.toLowerCase()
			.includes(needle);
	}

	const shown = $derived(entries.filter((entry) => matchesQuery(entry, query)));
	const installedCount = $derived(entries.filter((entry) => entry.installed).length);
	const meta = $derived(
		query.trim()
			? `${shown.length} of ${Math.max(total, entries.length)} match`
			: entries.length
				? `${entries.length} available · ${installedCount} installed`
				: sources.length
					? sources.join(', ')
					: 'no source'
	);

	/** Ask what installing would do. Fetches and hashes; installs nothing. */
	async function propose(entry: CatalogEntry): Promise<void> {
		if (!conn) return;
		planError = '';
		busy = entry.id;
		try {
			const answer = await conn.client.command<{ plan?: InstallPlan; error?: string }>({
				type: 'jarvis/extensions/plan',
				source: entry.source,
				// `entry`, not `id`: `id` is the websocket envelope's message id.
				entry: entry.id
			});
			if (answer.error) planError = answer.error;
			else proposal = answer.plan ?? null;
		} catch (e) {
			planError = describeError(e);
		} finally {
			busy = '';
		}
	}

	/** Install exactly what is on screen: the plan goes back as approved. */
	async function confirmInstall(): Promise<void> {
		if (!conn || !proposal) return;
		const id = proposal.id;
		busy = id;
		try {
			await conn.client.command({
				type: 'jarvis/extensions/install',
				source: proposal.source,
				entry: id,
				approved: proposal
			});
			proposal = null;
			await load();
			oninstalled?.(id);
		} catch (e) {
			planError = describeError(e);
		} finally {
			busy = '';
		}
	}
</script>

<section class="catalogue" data-testid="catalogue-section" aria-labelledby="catalogue-title">
	<div class="head">
		<h2 id="catalogue-title">Catalogue</h2>
		<span class="meta" data-testid="catalogue-meta">{meta}</span>
	</div>
	<div class="body">
		{#if view === 'loading'}
			<SkeletonRows rows={4} label="Loading the catalogue" />
		{:else if view === 'error'}
			<ErrorState
				title="Couldn't read the catalogue"
				detail={err}
				onretry={load}
				testid="catalogue-error"
			/>
		{:else if view === 'empty'}
			<EmptyState
				title="No catalogue sources"
				body="Nothing is offered until a source is named. Add one under extensions: catalog: sources: in configuration.yaml — a folder on this machine, or an https list."
				testid="catalogue-empty"
			/>
		{:else}
			{#if offline}
				<p class="note offline" data-testid="catalogue-offline">
					The link is down — this is the catalogue as it was last read. Reconnect from the notice
					above.
				</p>
			{/if}
			{#if planError}
				<p class="bad" role="alert" data-testid="catalogue-plan-error">{planError}</p>
			{/if}
			{#each sourceErrors as problem (problem.source)}
				<p class="warn" role="alert" data-testid="catalogue-source-error-{problem.source}">
					{problem.source}: {problem.error}
				</p>
			{/each}
			<ul class="rows">
				{#each shown as entry, i (entry.source + entry.id)}
					<li
						class="entry jv-stagger"
						style={staggerStyle(i)}
						data-testid="catalog-{entry.id}"
						data-installed={entry.installed}
						data-jv-row
					>
						<div class="what">
							<span class="id">
								{entry.id}
								<Pill tone={entry.kind === 'mcp' ? 'live' : 'neutral'} testid="catalog-kind-{entry.id}">
									{entry.kind === 'mcp' ? 'MCP' : 'SKILL'}
								</Pill>
							</span>
							<span class="desc">{entry.description}</span>
							<span class="asks" data-testid="catalog-perms-{entry.id}">
								{entry.source}{entry.ref ? ` · ${entry.ref}` : ''} · asks for {entry.permissions.length
									? entry.permissions.join(', ')
									: 'nothing'}
							</span>
						</div>
						<div class="acts">
							{#if entry.installed}
								<Pill tone="ok" testid="catalog-installed-{entry.id}">INSTALLED</Pill>
							{:else}
								<!-- Ghost, not primary: the shipped entries are installed on
								     a fresh box, so this is the rarer press, and the page's
								     one lit control stays NEW SKILL (see Tools.svelte). -->
								<Button
									testid="catalog-install-{entry.id}"
									disabled={busy === entry.id}
									title={busy === entry.id
										? 'Fetching and hashing'
										: 'See exactly what installing it would do, then decide'}
									onclick={() => propose(entry)}
								>
									{busy === entry.id ? 'READING…' : 'INSTALL'}
								</Button>
							{/if}
						</div>
					</li>
				{:else}
					<li class="empty-row">
						<EmptyState
							testid="catalogue-no-match"
							title="Nothing in the catalogue matches"
							body="Clear the search, or try another word."
						/>
					</li>
				{/each}
			</ul>
		{/if}

		<!-- One line for MCP, whatever the state above: a registry offers its
		     http servers as rows (INSTALL adds one at the default tier and
		     downloads nothing); a server that runs on this machine is never
		     offered, and any server can still be added by URL. -->
		<div class="mcp" data-testid="catalogue-mcp">
			<p class="note">
				MCP servers from a registry install as http servers at the default tier — every tool they
				offer is held for a person until approved, and nothing is downloaded.
				{#if skipped}
					<span data-testid="catalogue-skipped">
						{skipped} {skipped === 1 ? 'server' : 'servers'} in the registry would start a program on
						this machine and {skipped === 1 ? 'is' : 'are'} not offered.
					</span>
				{/if}
				A server not in any registry is added by URL in the MCP servers fold below; a program this
				machine starts (stdio) is configured in configuration.yaml with <code>allow_stdio</code>.
			</p>
			<Button testid="catalogue-add-mcp" title="Open the MCP servers fold on its add form" onclick={onaddmcp}>
				ADD BY URL
			</Button>
		</div>
	</div>
</section>

<!--
  Installing is a second decision, and this dialog shows what the row cannot:
  the exact ref, the hash, every file, and every program in the payload.
-->
<Dialog open={Boolean(proposal)} title={`Install ${proposal?.id ?? ''}?`} onclose={() => (proposal = null)}>
	{#if proposal}
		{#if proposal.kind === 'mcp'}
			<dl data-testid="install-plan" data-kind="mcp">
				<dt>From</dt>
				<dd>{proposal.source}{proposal.ref ? ` · ${proposal.ref}` : ''}</dd>
				<dt>Server</dt>
				<dd class="hash" data-testid="install-url">{proposal.url}</dd>
				<dt>Tier</dt>
				<dd data-testid="install-tier">{proposal.tier}</dd>
			</dl>
			<p class="note" data-testid="install-note">{proposal.note}</p>
		{:else}
			<dl data-testid="install-plan" data-kind="skill">
				<dt>From</dt>
				<dd>{proposal.source} at {proposal.ref || 'no ref'}</dd>
				<dt>Checksum</dt>
				<dd class="hash">{proposal.sha256}</dd>
				<dt>Asks for</dt>
				<dd data-testid="install-permissions">
					{proposal.permissions.length ? proposal.permissions.join(', ') : 'nothing'}
				</dd>
				<dt>Files</dt>
				<dd>{proposal.files.join(', ')}</dd>
			</dl>
			{#if proposal.hooks.length}
				<p class="warn" role="alert" data-testid="install-hooks">{proposal.warning}</p>
			{/if}
			<p class="note">
				Nothing in this payload is run — a skill folder is read, never executed. What it can do is
				tell the model things, and every action it suggests still goes through that action's own
				approval.
			</p>
		{/if}
	{/if}
	{#snippet actions()}
		<Button onclick={() => (proposal = null)}>CANCEL</Button>
		<Button
			variant="primary"
			testid="install-confirm"
			disabled={busy === proposal?.id}
			title={busy === proposal?.id ? 'Installing' : 'Install exactly what is on screen'}
			onclick={confirmInstall}
		>
			INSTALL
		</Button>
	{/snippet}
</Dialog>

<style>
	/* The folds' panel, without the fold: a flat hairline surface whose head
	   is a title rather than a disclosure. */
	.catalogue {
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.head h2 {
		margin: 0;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		/* One step brighter than a closed fold's title: this one is open. */
		color: var(--jv-text);
	}
	.meta {
		margin-left: auto;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.body {
		padding: var(--jv-space-4);
	}
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.entry {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.entry:first-child {
		padding-top: 0;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	/* An entry's id is what the manifest and the model call it: data. */
	.id {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-bright);
	}
	.desc {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.asks {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	.acts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		justify-content: flex-end;
	}
	.empty-row {
		padding: 0;
	}
	.mcp {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-4);
		flex-wrap: wrap;
		padding-top: var(--jv-space-3);
	}
	.note {
		margin: 0;
		max-width: 70ch;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		flex: 1 1 24rem;
	}
	.note.offline {
		margin-bottom: var(--jv-space-3);
		color: var(--jv-warn);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	.bad {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.warn {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-warn);
	}
	dl {
		display: grid;
		grid-template-columns: minmax(6rem, max-content) 1fr;
		gap: var(--jv-space-1) var(--jv-space-4);
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
	}
	dt {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	dd {
		margin: 0;
		color: var(--jv-text);
		word-break: break-word;
	}
	.hash {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		word-break: break-all;
	}
	@media (max-width: 640px) {
		.entry {
			grid-template-columns: minmax(0, 1fr);
		}
		.acts {
			justify-content: flex-start;
		}
	}
</style>
