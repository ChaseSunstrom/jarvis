<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import {
		friendlyName,
		type EntityRegistryEntry,
		type EntityState,
		type ToolDescription,
		type ToolRow
	} from '$lib/jarvisClient';
	import {
		METHODS,
		blankToolForm,
		parseToolForm,
		toolFormFromRow,
		type ToolForm
	} from '$lib/toolDraft';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let busy = $state(false);
	let loading = $state(true);

	let tools = $state<ToolDescription[]>([]);
	let source = $state<'tools' | 'services' | ''>('');
	let entries = $state<EntityRegistryEntry[]>([]);
	let states = $state<EntityState[]>([]);
	let toolFilter = $state('');
	let entityFilter = $state('');

	/**
	 * The manageable view: which tools the console created, and their service
	 * blocks. Kept beside `tools` rather than replacing it, because the
	 * catalogue above is what the *model* sees and that includes built-ins the
	 * console can list but not touch.
	 */
	let rows = $state<ToolRow[]>([]);
	let rowMap = $derived(new Map(rows.map((row) => [row.name, row])));
	let manageable = $state(true);

	let editing = $state('');
	let form = $state<ToolForm>(blankToolForm());
	let formError = $state('');
	let saving = $state(false);
	let removing = $state('');
	let confirming = $state('');

	let selected = $state('');
	let args = $state('{}');
	let result = $state('');

	async function refreshTools(): Promise<void> {
		if (!conn) return;
		try {
			rows = (await conn.client.listToolRows()) ?? [];
		} catch (e) {
			// An older jarvis-core has no tool management. The catalogue and the
			// test runner still work, so the page degrades rather than erroring.
			rows = [];
			manageable = false;
			console.warn('tool management unavailable', e);
		}
		try {
			tools = await conn.client.listTools();
		} catch {
			/* the catalogue load below already reported this */
		}
	}

	function openNewTool(): void {
		editing = editing === 'new' ? '' : 'new';
		form = blankToolForm();
		formError = '';
		confirming = '';
	}

	function openEditTool(row: ToolRow): void {
		if (editing === row.name) {
			editing = '';
			return;
		}
		editing = row.name;
		form = toolFormFromRow(row);
		formError = '';
		confirming = '';
	}

	async function saveTool(): Promise<void> {
		if (!conn || !editing) return;
		const parsed = parseToolForm(form);
		if (!parsed.ok) {
			formError = parsed.error;
			document.getElementById(`tool-field-${parsed.field}`)?.focus();
			return;
		}
		saving = true;
		formError = '';
		try {
			if (editing === 'new') {
				await conn.client.createTool(parsed.draft);
				toasts.success(`Created ${parsed.draft.name}`);
			} else {
				await conn.client.updateTool(editing, parsed.draft);
				toasts.success(`Saved ${parsed.draft.name}`);
			}
			editing = '';
			await refreshTools();
		} catch (e) {
			formError = describeError(e);
		} finally {
			saving = false;
		}
	}

	async function removeTool(row: ToolRow): Promise<void> {
		if (!conn) return;
		if (confirming !== row.name) {
			confirming = row.name;
			setTimeout(() => {
				if (confirming === row.name) confirming = '';
			}, 4000);
			return;
		}
		confirming = '';
		removing = row.name;
		try {
			await conn.client.deleteTool(row.name);
			toasts.success(`Deleted ${row.name}`);
			if (editing === row.name) editing = '';
			await refreshTools();
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not delete ${row.name}`, describeError(e));
		} finally {
			removing = '';
		}
	}

	let stateMap = $derived(new Map(states.map((s) => [s.entity_id, s])));
	/**
	 * The catalogue: what the model can call, plus anything the console
	 * manages that is not already in it.
	 *
	 * A union rather than a choice between the two lists. `jarvis/tools/list`
	 * (or its service-catalogue fallback) is what the *model* sees, and
	 * `config/tool/list` is what this page can *edit* — a backend can answer
	 * one and not the other, and taking either alone loses rows. Reading only
	 * the first would hide every console-created tool; reading only the second
	 * would throw away the fallback this page degrades to.
	 */
	let catalogue = $derived.by<ToolDescription[]>(() => {
		const seen = new Set(tools.map((t) => t.name));
		return [
			...tools,
			...rows
				.filter((row) => !seen.has(row.name))
				.map((row) => ({
					name: row.name,
					description: row.description,
					domain: row.domain,
					parameters: row.parameters ?? undefined
				}))
		];
	});
	let visibleTools = $derived(
		catalogue.filter((t) => t.name.toLowerCase().includes(toolFilter.trim().toLowerCase()))
	);
	let visibleEntities = $derived(
		entries
			.filter((e) => {
				const needle = entityFilter.trim().toLowerCase();
				if (!needle) return true;
				return (
					e.entity_id.toLowerCase().includes(needle) ||
					friendlyName(stateMap.get(e.entity_id), e).toLowerCase().includes(needle)
				);
			})
			.sort((a, b) => a.entity_id.localeCompare(b.entity_id))
	);
	let exposedCount = $derived(entries.filter((e) => e.exposed !== false).length);
	let selectedTool = $derived(tools.find((t) => t.name === selected));

	async function toggleExposure(entry: EntityRegistryEntry): Promise<void> {
		if (!conn) return;
		err = '';
		const next = entry.exposed === false;
		try {
			await conn.client.updateEntity(entry.entity_id, { exposed: next });
			entries = entries.map((e) =>
				e.entity_id === entry.entity_id ? { ...e, exposed: next } : e
			);
			toasts.success(`${next ? 'Exposed' : 'Hidden from the LLM'} · ${entry.entity_id}`);
		} catch (e) {
			err = describeError(e);
			toasts.error(`Exposure change failed · ${entry.entity_id}`, describeError(e));
		}
	}

	async function testRun(): Promise<void> {
		if (!conn || !selected) return;
		busy = true;
		err = '';
		result = '';
		let parsed: Record<string, any>;
		try {
			parsed = args.trim() ? JSON.parse(args) : {};
		} catch (e) {
			busy = false;
			err = `arguments are not valid JSON: ${(e as Error).message}`;
			toasts.error('Arguments are not valid JSON', (e as Error).message);
			return;
		}
		try {
			const outcome = await conn.client.callTool(selected, parsed);
			result = JSON.stringify(outcome ?? null, null, 2);
			toasts.success(`Ran ${selected}`);
		} catch (e) {
			err = describeError(e);
			toasts.error(`${selected} failed`, describeError(e));
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		let disposed = false;
		(async () => {
			try {
				const connection = await openConnection({ onStatus: (s) => (status = s) });
				if (disposed) {
					connection.close();
					return;
				}
				conn = connection;
				tools = await connection.client.listTools();
				// From the client's own record of which command answered — reading
				// tools[0].source mislabels an empty native list as a fallback.
				source = connection.client.supportsNativeTools ? 'tools' : 'services';
				if (source === 'services') {
					hint =
						'This backend has no jarvis/tools/list command, so the service catalogue is shown instead — the same calls the LLM tool layer makes.';
				}
				if (tools.length && !selected) selected = tools[0].name;
				try {
					rows = (await connection.client.listToolRows()) ?? [];
				} catch (e) {
					rows = [];
					manageable = false;
					console.warn('tool management unavailable', e);
				}
				try {
					entries = (await connection.client.listEntities()) ?? [];
					states = (await connection.client.getStates()) ?? [];
				} catch (e) {
					hint = describeError(e);
				}
			} catch (e) {
				err = describeError(e);
			} finally {
				if (!disposed) loading = false;
			}
		})();
		return () => {
			disposed = true;
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · Tools</title></svelte:head>

{#snippet toolEditor()}
	{#if formError}
		<p class="err" data-testid="tool-form-error" role="alert">{formError}</p>
	{/if}

	<div class="field">
		<label for="tool-field-name">Name</label>
		<input
			id="tool-field-name"
			type="text"
			data-testid="tool-field-name"
			placeholder="paperless_search"
			disabled={editing !== 'new'}
			bind:value={form.name}
		/>
		<span class="hint">
			{editing === 'new'
				? 'What the model says to call it. Lowercase letters, digits and underscores.'
				: 'A tool cannot be renamed — the model calls it by this word.'}
		</span>
	</div>

	<div class="field">
		<label for="tool-field-description">Description</label>
		<input
			id="tool-field-description"
			type="text"
			data-testid="tool-field-description"
			placeholder="Search Paperless-ngx documents by query text"
			bind:value={form.description}
		/>
		<span class="hint">This is all the model has to decide when to use it. Be specific.</span>
	</div>

	<div class="field">
		<label for="tool-field-tier">Tier</label>
		<select id="tool-field-tier" data-testid="tool-field-tier" bind:value={form.tier}>
			<option value="1">1 — run it</option>
			<option value="2">2 — run it and tell me</option>
			<option value="3">3 — ask me first</option>
		</select>
	</div>

	<div class="field">
		<label for="tool-field-method">Method</label>
		<select id="tool-field-method" data-testid="tool-field-method" bind:value={form.method}>
			{#each METHODS as method (method)}<option value={method}>{method}</option>{/each}
		</select>
	</div>

	<div class="field">
		<label for="tool-field-url">URL</label>
		<input
			id="tool-field-url"
			type="text"
			data-testid="tool-field-url"
			placeholder="http://paperless.lan/api/documents/?query=&#123;&#123; query &#125;&#125;"
			bind:value={form.url}
		/>
		<span class="hint">Field values are percent-encoded into the URL.</span>
	</div>

	<div class="field">
		<label for="tool-field-fields">Fields</label>
		<textarea id="tool-field-fields" rows="5" spellcheck="false" data-testid="tool-field-fields" bind:value={form.fields}
		></textarea>
		<span class="hint">JSON. What the model may fill in, and which are required.</span>
	</div>

	<div class="field">
		<label for="tool-field-headers">Headers</label>
		<textarea id="tool-field-headers" rows="3" spellcheck="false" data-testid="tool-field-headers" bind:value={form.headers}
		></textarea>
		<span class="hint">JSON. Auth tokens go here; they stay on the server.</span>
	</div>

	<div class="field">
		<label for="tool-field-payload">Body</label>
		<textarea id="tool-field-payload" rows="3" spellcheck="false" data-testid="tool-field-payload" bind:value={form.payload}
		></textarea>
		<span class="hint">JSON, for POST/PUT/PATCH. Blank sends none.</span>
	</div>

	<div class="actions">
		<button type="button" class="btn" data-testid="tool-save" disabled={saving} onclick={saveTool}>
			{saving ? 'SAVING…' : editing === 'new' ? 'CREATE' : 'SAVE'}
		</button>
		<button type="button" class="btn ghost" data-testid="tool-cancel" onclick={() => (editing = '')}>
			CANCEL
		</button>
	</div>
{/snippet}

<h1>TOOLS</h1>
<p class="lede">
	{tools.length} callable{tools.length === 1 ? '' : 's'} · {exposedCount} exposed entit{exposedCount ===
	1
		? 'y'
		: 'ies'} · link {status}
</p>

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<section class="panel">
	<div class="panel-head">
		<span>Test run</span>
		<span class="muted">{source === 'tools' ? 'jarvis/tools/call' : 'call_service'}</span>
	</div>
	<div class="row">
		<select data-testid="tool-select" aria-label="Tool to run" bind:value={selected}>
			{#each visibleTools as tool (tool.name)}
				<option value={tool.name}>{tool.name}</option>
			{/each}
		</select>
		<input
			type="text"
			style="flex:1 1 16rem"
			placeholder={'{"entity_id": "light.lab_lights"}'}
			aria-label="Tool arguments as JSON"
			data-testid="tool-args"
			bind:value={args}
		/>
		<button
			type="button"
			class="btn"
			data-testid="tool-run"
			disabled={busy || !selected}
			onclick={testRun}
		>
			{busy ? 'RUNNING…' : 'RUN'}
		</button>
	</div>
	{#if selectedTool?.description}
		<p class="muted">{selectedTool.description}</p>
	{/if}
	{#if result}
		<pre data-testid="tool-result" aria-live="polite">{result}</pre>
	{/if}
</section>

{#if manageable}
	<div class="toolbar">
		<button
			type="button"
			class="btn"
			data-testid="tool-new"
			aria-expanded={editing === 'new'}
			onclick={openNewTool}
		>
			{editing === 'new' ? 'CANCEL' : '+ NEW TOOL'}
		</button>
		<span class="muted">
			A tool is an HTTP call the assistant may make. Built-ins and *.tool.yaml manifests are listed
			but cannot be changed here.
		</span>
	</div>

	{#if editing === 'new'}
		<section class="panel">
			<div class="panel-head"><span>New tool</span></div>
			<div class="editor" data-testid="tool-editor-new">
				{@render toolEditor()}
			</div>
		</section>
	{/if}
{/if}

<section class="panel">
	<div class="panel-head">
		<span>Catalogue</span>
		<input
			type="text"
			placeholder="filter  ( / )"
			aria-label="Filter the tool catalogue"
			data-testid="tool-filter"
			data-jv-filter
			bind:value={toolFilter}
		/>
	</div>
	{#if loading && !tools.length}
		<Skeleton rows={5} label="Loading the tool catalogue" />
	{:else}
		{#each visibleTools as tool, i (tool.name)}
			{@const row = rowMap.get(tool.name)}
			<div class="row jv-stagger" style={staggerStyle(i)} data-testid="tool-{tool.name}">
				<span class="name">
					<b>{tool.name}</b>
					<span class="eid">{tool.description || 'no description'}</span>
				</span>
				<button
					type="button"
					class="btn ghost"
					aria-label="Load {tool.name} into the test runner"
					onclick={() => (selected = tool.name)}>USE</button
				>
				{#if row?.editable}
					<button
						type="button"
						class="btn ghost"
						data-testid="tool-edit-{tool.name}"
						aria-expanded={editing === tool.name}
						aria-label="Edit {tool.name}"
						onclick={() => openEditTool(row)}
					>
						{editing === tool.name ? 'CLOSE' : 'EDIT'}
					</button>
					<button
						type="button"
						class="btn ghost danger"
						data-testid="tool-delete-{tool.name}"
						disabled={removing === tool.name}
						aria-label="Delete {tool.name}"
						onclick={() => removeTool(row)}
					>
						{removing === tool.name
							? 'DELETING…'
							: confirming === tool.name
								? 'CONFIRM?'
								: 'DELETE'}
					</button>
				{:else if manageable}
					<span class="pill" data-testid="tool-builtin-{tool.name}">BUILT IN</span>
				{/if}
			</div>

			{#if row?.editable && editing === tool.name}
				<div class="editor" data-testid="tool-editor-{tool.name}">
					{@render toolEditor()}
				</div>
			{/if}
		{:else}
			<div class="jv-empty" data-testid="empty">
				<span class="jv-empty-mark" aria-hidden="true">[ ∅ ]</span>
				<p class="jv-empty-title">{status === 'open' ? 'No tools matched' : 'No link to the backend'}</p>
				<p class="jv-empty-body">
					{status === 'open'
						? 'Nothing in the catalogue matches that filter.'
						: `The websocket relay is ${status}.`}
				</p>
			</div>
		{/each}
	{/if}
</section>

<section class="panel">
	<div class="panel-head">
		<span>Entity exposure</span>
		<input
			type="text"
			placeholder="filter"
			aria-label="Filter entities"
			data-testid="entity-filter"
			bind:value={entityFilter}
		/>
	</div>
	<p class="muted">
		Unexposed entities stay out of the LLM's prompt and cannot be targeted by voice.
	</p>
	{#each visibleEntities as entry, i (entry.entity_id)}
		{@const exposed = entry.exposed !== false}
		<div class="row jv-stagger" style={staggerStyle(i)} data-testid="expose-row-{entry.entity_id}">
			<span class="name">
				<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
				<span class="eid">{entry.entity_id}</span>
			</span>
			<button
				type="button"
				class="btn"
				class:on={exposed}
				data-testid="expose-{entry.entity_id}"
				aria-pressed={exposed}
				aria-label="{exposed ? 'Hide' : 'Expose'} {entry.entity_id} to the assistant"
				onclick={() => toggleExposure(entry)}
			>
				{exposed ? 'EXPOSED' : 'HIDDEN'}
			</button>
		</div>
	{:else}
		<p class="muted">No entity registry entries on this backend.</p>
	{/each}
</section>
