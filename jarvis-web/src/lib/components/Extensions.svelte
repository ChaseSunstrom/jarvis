<!--
@component
Everything extensible, and what it may reach.

Skills, MCP servers and tool plugins in one list, because they are one question:
what has been added to Jarvis, and what is it allowed to do. Three separate
panels was the design this replaces — an operator asking "what can write to my
memory" had to know which of the three a thing was before they could look.

It lives on the tools page rather than in a tab of its own. That page is
already "what Jarvis can call and what it is allowed to call".

The row is the whole design: name, what it is, what it holds, whether it works,
when it last ran, and one switch. Everything rarer than that — the tool list,
the permission scope, where it came from — is behind the row's own expander,
which is the difference between a page you can read and a page you have to
study. It draws no panel of its own: the tools page puts it behind a
disclosure whose header carries the count this reports through `count`.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Button,
		Dialog,
		EmptyState,
		Field,
		Input,
		Pill,
		SkeletonRows,
		Toggle
	} from '$lib/ui';
	import { slide } from '$lib/motion';
	import type { Connection } from '$lib/connection';

	interface Extension {
		id: string;
		kind: 'skill' | 'mcp' | 'plugin';
		key: string;
		version: string;
		description: string;
		author: string;
		source_url: string;
		permissions: string[];
		granted: string[];
		revoked: string[];
		tools: string[];
		network: { needs: boolean; hosts: string[] };
		filesystem: { read: string[]; write: string[] };
		origin: string;
		enabled: boolean;
		location: string;
		health: { ok?: boolean; detail?: string };
		last_used: number | null;
	}

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
	}

	interface InstallPlan {
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

	interface Props {
		/** The page's connection. Null while it is dialling. */
		conn: Connection | null;
		/** How many are installed, for the disclosure header above. */
		count?: number;
	}
	let { conn, count = $bindable(0) }: Props = $props();

	let extensions = $state<Extension[]>([]);
	let errors = $state<{ kind: string; id: string; location: string; error: string }[]>([]);
	let permissions = $state<string[]>([]);
	let loading = $state(true);
	let err = $state('');
	let busy = $state('');
	let opened = $state<string | null>(null);
	let creating = $state(false);
	let browsing = $state(false);
	let catalogQuery = $state('');
	let catalogEntries = $state<CatalogEntry[]>([]);
	let catalogSources = $state<string[]>([]);
	let catalogError = $state('');
	let proposal = $state<InstallPlan | null>(null);
	let newName = $state('');
	let newDescription = $state('');
	let newTools = $state('');
	let createError = $state('');

	const KINDS: Record<string, string> = {
		skill: 'Skills',
		mcp: 'MCP servers',
		plugin: 'Plugins'
	};

	async function load(): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{
				extensions: Extension[];
				errors: typeof errors;
				permissions: string[];
			}>({ type: 'jarvis/extensions/list' });
			extensions = answer.extensions ?? [];
			errors = answer.errors ?? [];
			permissions = answer.permissions ?? [];
			count = extensions.length;
			err = '';
		} catch (e) {
			err = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		if (conn) void load();
	});

	async function set(row: Extension, patch: Record<string, unknown>): Promise<void> {
		if (!conn || busy) return;
		busy = row.key;
		try {
			await conn.client.command({ type: 'jarvis/extensions/set', key: row.key, ...patch });
			await load();
		} catch (e) {
			err = e instanceof Error ? e.message : String(e);
		} finally {
			busy = '';
		}
	}

	/** Revoking is narrowing, so the request carries what SURVIVES. */
	function toggle(row: Extension, permission: string): void {
		const held = new Set(row.granted);
		if (held.has(permission)) held.delete(permission);
		else held.add(permission);
		void set(row, { permissions: [...held] });
	}

	async function create(): Promise<void> {
		if (!conn) return;
		createError = '';
		busy = 'new';
		try {
			await conn.client.command({
				type: 'jarvis/extensions/scaffold',
				name: newName.trim(),
				description: newDescription.trim(),
				tools: newTools
					.split(/[\s,]+/)
					.map((t) => t.trim())
					.filter(Boolean)
			});
			creating = false;
			newName = '';
			newDescription = '';
			newTools = '';
			await load();
		} catch (e) {
			createError = e instanceof Error ? e.message : String(e);
		} finally {
			busy = '';
		}
	}

	async function browse(): Promise<void> {
		if (!conn) return;
		catalogError = '';
		try {
			const answer = await conn.client.command<{ entries: CatalogEntry[]; sources: string[]; error?: string }>(
				{ type: 'jarvis/extensions/browse', query: catalogQuery.trim() }
			);
			catalogEntries = answer.entries ?? [];
			catalogSources = answer.sources ?? [];
			if (answer.error) catalogError = answer.error;
		} catch (e) {
			catalogError = e instanceof Error ? e.message : String(e);
		}
	}

	/** Ask what would happen. Fetches and hashes; installs nothing. */
	async function propose(entry: CatalogEntry): Promise<void> {
		if (!conn) return;
		catalogError = '';
		busy = entry.id;
		try {
			const answer = await conn.client.command<{ plan?: InstallPlan; error?: string }>({
				type: 'jarvis/extensions/plan',
				source: entry.source,
				// `entry`, not `id`: `id` is the websocket envelope's message id.
				entry: entry.id
			});
			if (answer.error) catalogError = answer.error;
			else proposal = answer.plan ?? null;
		} catch (e) {
			catalogError = e instanceof Error ? e.message : String(e);
		} finally {
			busy = '';
		}
	}

	/** Install exactly what is on screen: the plan goes back as approved. */
	async function confirmInstall(): Promise<void> {
		if (!conn || !proposal) return;
		busy = proposal.id;
		try {
			await conn.client.command({
				type: 'jarvis/extensions/install',
				source: proposal.source,
				entry: proposal.id,
				approved: proposal
			});
			proposal = null;
			browsing = false;
			await load();
		} catch (e) {
			catalogError = e instanceof Error ? e.message : String(e);
		} finally {
			busy = '';
		}
	}

	function ago(at: number | null): string {
		if (!at) return 'never used';
		const seconds = Math.max(0, Math.floor(Date.now() / 1000 - at));
		if (seconds < 90) return 'used just now';
		const minutes = Math.floor(seconds / 60);
		if (minutes < 90) return `used ${minutes}m ago`;
		const hours = Math.floor(minutes / 60);
		if (hours < 36) return `used ${hours}h ago`;
		return `used ${Math.floor(hours / 24)}d ago`;
	}

	const grouped = $derived(
		(['skill', 'plugin', 'mcp'] as const).map((kind) => ({
			kind,
			rows: extensions.filter((e) => e.kind === kind)
		}))
	);
</script>

<div class="extensions" data-testid="extensions-panel">
	{#if loading}
		<SkeletonRows rows={4} label="Loading what is installed" />
	{:else if err}
		<p class="bad" role="alert" data-testid="extensions-error">{err}</p>
	{:else if extensions.length === 0}
		<EmptyState
			title="Nothing installed"
			body="Skills, MCP servers and plugins appear here. Write the first skill and it is live at once."
			testid="extensions-empty"
		>
			{#snippet action()}
				<Button variant="primary" testid="extensions-new" onclick={() => (creating = true)}>
					NEW SKILL
				</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<div class="head">
			<p class="note">
				What has been added to Jarvis, and what each one is allowed to reach. Turning one off
				takes its tools off the model, not just off this page.
			</p>
			<div class="actions">
				<Button
					testid="extensions-browse"
					title="Search the configured catalog sources"
					onclick={() => {
						browsing = true;
						void browse();
					}}
				>
					BROWSE CATALOG
				</Button>
				<!-- The one lit control on the tools page: writing a skill is the
				     thing this surface is for. -->
				<Button variant="primary" testid="extensions-new" onclick={() => (creating = true)}>
					NEW SKILL
				</Button>
			</div>
		</div>

		{#each grouped as group (group.kind)}
			{#if group.rows.length}
				<h3 class="group">{KINDS[group.kind]}</h3>
				{#each group.rows as row (row.key)}
					<div class="ext" class:off={!row.enabled} data-testid={`ext-${row.key}`}>
						<div class="ext-line">
							<!-- The whole name is the expander; it has no button chrome. -->
							<button
								type="button"
								class="name"
								aria-expanded={opened === row.key}
								data-testid={`ext-open-${row.key}`}
								onclick={() => (opened = opened === row.key ? null : row.key)}
							>
								<span class="id">{row.id}</span>
								<span class="what">{row.description}</span>
							</button>
							<div class="marks">
								{#if row.origin === 'bundled'}<Pill>SHIPPED</Pill>{/if}
								{#if row.health?.ok === false}
									<Pill tone="warn" testid={`ext-sick-${row.key}`}>
										{row.health?.detail || 'not working'}
									</Pill>
								{/if}
								{#if row.revoked?.length}
									<Pill tone="warn">NARROWED</Pill>
								{/if}
								<span class="used">{ago(row.last_used)}</span>
								<Toggle
									checked={row.enabled}
									label={row.enabled ? 'On' : 'Off'}
									disabled={busy === row.key}
									testid={`ext-toggle-${row.key}`}
									onchange={() => set(row, { enabled: !row.enabled })}
								/>
							</div>
						</div>

						{#if opened === row.key}
							<div class="detail" style={slide()} data-testid={`ext-detail-${row.key}`}>
								<dl>
									<dt>Where</dt>
									<dd>{row.location || '—'}</dd>
									<dt>Version</dt>
									<dd>{row.version || '—'}{row.author ? ` · ${row.author}` : ''}</dd>
									{#if row.source_url}
										<dt>Source</dt>
										<dd>{row.source_url}</dd>
									{/if}
									<dt>Tools</dt>
									<dd>{row.tools.length ? row.tools.join(', ') : 'none named'}</dd>
									{#if row.network?.needs}
										<dt>Network</dt>
										<dd>{row.network.hosts.length ? row.network.hosts.join(', ') : 'any host'}</dd>
									{/if}
									{#if row.filesystem?.read?.length || row.filesystem?.write?.length}
										<dt>Files</dt>
										<dd>
											{[
												row.filesystem.read.length ? `reads ${row.filesystem.read.join(', ')}` : '',
												row.filesystem.write.length ? `writes ${row.filesystem.write.join(', ')}` : ''
											]
												.filter(Boolean)
												.join(' · ')}
										</dd>
									{/if}
								</dl>

								<div class="scope">
									<span class="scope-label">Permission scope</span>
									{#if row.permissions.length === 0}
										<span class="note">It declared none.</span>
									{:else}
										<div class="grants">
											{#each row.permissions as permission (permission)}
												<Toggle
													checked={row.granted.includes(permission)}
													label={permission}
													disabled={busy === row.key}
													testid={`ext-perm-${row.key}-${permission}`}
													onchange={() => toggle(row, permission)}
												/>
											{/each}
										</div>
										{#if row.kind === 'skill'}
											<p class="note small">
												A skill is a document: this narrows what the model is told it may use.
												What stops it acting is each tool's own tier.
											</p>
										{:else}
											<p class="note small">
												Taking one away withdraws the tools that need it, on the next call.
											</p>
										{/if}
									{/if}
								</div>
							</div>
						{/if}
					</div>
				{/each}
			{/if}
		{/each}
	{/if}

	{#if errors.length}
		<h3 class="group">Not loaded</h3>
		{#each errors as problem (problem.id)}
			<div class="ext rejected" data-testid={`ext-rejected-${problem.id}`}>
				<div class="ext-line">
					<span class="name plain">
						<span class="id">{problem.id}</span>
						<span class="what">{problem.error}</span>
					</span>
					<Pill tone="danger">REJECTED</Pill>
				</div>
			</div>
		{/each}
		<p class="note small">
			A manifest that does not validate is not loaded at all — not loaded with the bad parts
			dropped, which would be a narrower allowlist than its author wrote.
		</p>
	{/if}
</div>

<!--
  The catalog. Two dialogs on purpose: browsing is reading, installing is a
  decision, and the second one shows what the first cannot — the exact ref, the
  hash, every file, and every program in the payload.
-->
<Dialog open={browsing} title="Catalog" onclose={() => (browsing = false)}>
	{#if catalogSources.length === 0 && !catalogError}
		<EmptyState
			title="No catalog source is configured"
			body="Nothing installs from an origin nobody named. Add one under `extensions: catalog: sources:` in configuration.yaml — https or a folder on this machine."
			testid="catalog-no-sources"
		/>
	{:else}
		<div class="search">
			<Input
				bind:value={catalogQuery}
				testid="catalog-query"
				placeholder="Search {catalogSources.join(', ')}"
			/>
			<Button testid="catalog-search" onclick={browse}>SEARCH</Button>
		</div>
		{#if catalogError}
			<p class="bad" role="alert" data-testid="catalog-error">{catalogError}</p>
		{/if}
		{#each catalogEntries as entry (entry.source + entry.id)}
			<div class="ext" data-testid={`catalog-${entry.id}`}>
				<div class="ext-line">
					<span class="name plain">
						<span class="id">{entry.id}</span>
						<span class="what">{entry.description}</span>
					</span>
					<div class="marks">
						<Pill>{entry.source}</Pill>
						{#if entry.ref}<Pill>{entry.ref}</Pill>{/if}
						<Button
							testid={`catalog-install-${entry.id}`}
							disabled={busy === entry.id}
							title={busy === entry.id ? 'Fetching and hashing' : 'See exactly what installing it would do'}
							onclick={() => propose(entry)}
						>
							REVIEW
						</Button>
					</div>
				</div>
				{#if entry.permissions.length}
					<p class="note small" data-testid={`catalog-perms-${entry.id}`}>
						Asks for: {entry.permissions.join(', ')}
					</p>
				{/if}
			</div>
		{/each}
		{#if catalogEntries.length === 0 && !catalogError}
			<EmptyState title="Nothing matched" body="Try a different word, or a different source." />
		{/if}
	{/if}
</Dialog>

<Dialog
	open={Boolean(proposal)}
	title={`Install ${proposal?.id ?? ''}?`}
	onclose={() => (proposal = null)}
>
	{#if proposal}
		<dl data-testid="install-plan">
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
		<p class="note small">
			Nothing in this payload is run — a skill folder is read, never executed. What it can do
			is tell the model things, and every action it suggests still goes through that action's
			own approval.
		</p>
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

<Dialog open={creating} title="New skill" onclose={() => (creating = false)}>
	<Field label="Name" hint="Lowercase, hyphens. It becomes the folder.">
		<Input bind:value={newName} testid="new-skill-name" placeholder="bin-day" mono />
	</Field>
	<Field label="What it is for" hint="The one line the model sees before it reads anything.">
		<Input
			bind:value={newDescription}
			testid="new-skill-description"
			placeholder="Which bin goes out, and on which night."
		/>
	</Field>
	<Field label="Tools it may name" hint="Optional, space separated. The permissions follow.">
		<Input bind:value={newTools} testid="new-skill-tools" placeholder="get_state web_search" mono />
	</Field>
	{#if createError}<p class="bad" role="alert" data-testid="new-skill-error">{createError}</p>{/if}
	{#snippet actions()}
		<Button onclick={() => (creating = false)}>CANCEL</Button>
		<Button
			variant="primary"
			disabled={!newName.trim() || !newDescription.trim() || busy === 'new'}
			title={!newName.trim() || !newDescription.trim()
				? 'A skill needs a name and a description.'
				: 'Write the SKILL.md and load it.'}
			testid="new-skill-create"
			onclick={create}
		>
			CREATE
		</Button>
	{/snippet}
</Dialog>

<style>
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--jv-space-4);
		margin-bottom: var(--jv-space-2);
		/*
		 * Wrap, or the sentence is crushed.
		 *
		 * At 390px the two buttons took their natural width and the paragraph
		 * collapsed to a two-character column — a readable line of prose
		 * rendered one letter per line, which the mobile screenshot caught and
		 * nothing else would have.
		 */
		flex-wrap: wrap;
	}
	.head .note {
		/* Its own line as soon as the buttons cannot sit beside it. */
		flex: 1 1 24rem;
	}
	.note {
		margin: 0;
		max-width: 70ch;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
	}
	.small {
		margin-top: var(--jv-space-2);
	}
	.bad {
		margin: 0;
		padding: var(--jv-space-2) 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.warn {
		margin: 0;
		padding: var(--jv-space-2) 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-warn);
	}
	.group {
		margin: var(--jv-space-4) 0 0;
		padding-bottom: var(--jv-space-2);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.ext {
		border-bottom: 1px solid var(--jv-line-hair);
		padding: var(--jv-space-3) 0;
	}
	.ext.off .id,
	.ext.off .what {
		color: var(--jv-text-faint);
	}
	.ext-line {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-4);
		flex-wrap: wrap;
	}
	.name {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
		flex: 1 1 20ch;
		min-width: 0;
		text-align: left;
		background: none;
		border: 0;
		padding: 0;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}
	.name.plain {
		cursor: default;
	}
	.name:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	/* An extension's id is what the model and the manifest call it: data. */
	.id {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-bright);
	}
	.what {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.marks {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.used {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.detail {
		margin-top: var(--jv-space-3);
		padding-left: var(--jv-space-3);
		border-left: 1px solid var(--jv-line-soft);
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
	.scope-label {
		display: block;
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		margin-bottom: var(--jv-space-2);
	}
	.grants {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2) var(--jv-space-4);
	}
	.actions {
		display: flex;
		gap: var(--jv-space-2);
		flex-shrink: 0;
	}
	.search {
		display: flex;
		gap: var(--jv-space-2);
		align-items: flex-end;
		margin-bottom: var(--jv-space-3);
	}
	.search :global(.in) {
		flex: 1 1 auto;
	}
	.hash {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		word-break: break-all;
	}
</style>
