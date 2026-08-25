<!--
@component
Everything extensible, and what it may reach.

Skills, MCP servers and tool plugins in one list, because they are one question:
what has been added to Jarvis, and what is it allowed to do. Three separate
panels was the design this replaces — an operator asking "what can write to my
memory" had to know which of the three a thing was before they could look.

It lives on `/tools` rather than in a tab of its own. That page is already
"what Jarvis can call and what it is allowed to call", and the console has ten
top-level destinations too many.

The row is the whole design: name, what it is, what it holds, whether it works,
when it last ran, and one switch. Everything rarer than that — the tool list,
the permission scope, where it came from — is behind the row's own expander,
which is the difference between a page you can read and a page you have to
study.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Button,
		Dialog,
		EmptyState,
		Field,
		Input,
		Panel,
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

	interface Props {
		/** The page's connection. Null while it is dialling. */
		conn: Connection | null;
	}
	let { conn }: Props = $props();

	let extensions = $state<Extension[]>([]);
	let errors = $state<{ kind: string; id: string; location: string; error: string }[]>([]);
	let permissions = $state<string[]>([]);
	let loading = $state(true);
	let err = $state('');
	let busy = $state('');
	let opened = $state<string | null>(null);
	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let newTools = $state('');
	let createError = $state('');

	const KINDS: Record<string, string> = {
		skill: 'SKILL',
		mcp: 'MCP SERVER',
		plugin: 'PLUGIN'
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

<Panel title="Extensions" meta={`${extensions.length} installed`} testid="extensions-panel">
	{#if loading}
		<SkeletonRows rows={4} />
	{:else if err}
		<p class="notice" role="alert" data-testid="extensions-error">{err}</p>
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
			<p class="muted">
				What has been added to Jarvis, and what each one is allowed to reach. Turning one off
				takes its tools off the model, not just off this page.
			</p>
			<Button variant="primary" testid="extensions-new" onclick={() => (creating = true)}>
				NEW SKILL
			</Button>
		</div>

		{#each grouped as group (group.kind)}
			{#if group.rows.length}
				<h3 class="group">{KINDS[group.kind]}S</h3>
				{#each group.rows as row (row.key)}
					<div class="row" class:off={!row.enabled} data-testid={`ext-${row.key}`}>
						<div class="line">
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
										<span class="muted">It declared none.</span>
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
											<p class="muted small">
												A skill is a document: this narrows what the model is told it may use.
												What stops it acting is each tool's own tier.
											</p>
										{:else}
											<p class="muted small">
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
		<h3 class="group">NOT LOADED</h3>
		{#each errors as problem (problem.id)}
			<div class="row bad" data-testid={`ext-rejected-${problem.id}`}>
				<div class="line">
					<span class="name">
						<span class="id">{problem.id}</span>
						<span class="what">{problem.error}</span>
					</span>
					<Pill tone="danger">REJECTED</Pill>
				</div>
			</div>
		{/each}
		<p class="muted small">
			A manifest that does not validate is not loaded at all — not loaded with the bad parts
			dropped, which would be a narrower allowlist than its author wrote.
		</p>
	{/if}
</Panel>

<Dialog open={creating} title="New skill" onclose={() => (creating = false)}>
	<Field label="Name" hint="Lowercase, hyphens. It becomes the folder.">
		<Input bind:value={newName} testid="new-skill-name" placeholder="bin-day" />
	</Field>
	<Field label="What it is for" hint="The one line the model sees before it reads anything.">
		<Input
			bind:value={newDescription}
			testid="new-skill-description"
			placeholder="Which bin goes out, and on which night."
		/>
	</Field>
	<Field label="Tools it may name" hint="Optional, space separated. The permissions follow.">
		<Input bind:value={newTools} testid="new-skill-tools" placeholder="get_state web_search" />
	</Field>
	{#if createError}<p class="notice" role="alert" data-testid="new-skill-error">{createError}</p>{/if}
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
		margin-bottom: var(--jv-space-3);
	}
	.head p {
		margin: 0;
		max-width: 60ch;
	}
	.group {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
		margin: var(--jv-space-4) 0 var(--jv-space-2);
	}
	.row {
		border-top: 1px solid var(--jv-line-soft);
		padding: var(--jv-space-3) 0;
	}
	.row.off .id,
	.row.off .what {
		color: var(--jv-text-dim);
	}
	.line {
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
	.id {
		font-family: var(--jv-font-chrome);
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
		font-size: var(--jv-fs-2xs);
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-dim);
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
		font-family: var(--jv-font-chrome);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
	}
	dd {
		margin: 0;
		word-break: break-word;
	}
	.scope-label {
		display: block;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-dim);
		margin-bottom: var(--jv-space-2);
	}
	.grants {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2) var(--jv-space-4);
	}
	.small {
		font-size: var(--jv-fs-2xs);
		margin: var(--jv-space-2) 0 0;
	}
</style>
