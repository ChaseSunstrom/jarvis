<script lang="ts">
	import { onMount, tick, type Snippet } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import { prefersReducedMotion, staggerStyle } from '$lib/motion';
	import Catalogue from '$lib/components/Catalogue.svelte';
	import Extensions from '$lib/components/Extensions.svelte';
	import { DiscardGuard, formsDiffer } from '$lib/unsaved';
	import McpServers from '$lib/components/McpServers.svelte';
	import N8nConnection from '$lib/components/N8nConnection.svelte';
	import SkillsPanel from '$lib/components/SkillsPanel.svelte';
	import {
		Button,
		EmptyState,
		Field,
		Input,
		Pill,
		ScreenState,
		Select,
		SkeletonRows,
		Toggle
	} from '$lib/ui';
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
		runnerOptions,
		runnerSelection,
		toolFormFromRow,
		type ToolForm,
		dedupeByName
	} from '$lib/toolDraft';

	// `$state`, unlike the other management pages: this one PASSES the
	// connection to a child. Left as a plain `let`, `<McpServers>` would be
	// handed the null it was born with and would never load — svelte-check
	// says so, and it is right.
	let conn = $state<Connection | null>(null);
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let busy = $state(false);
	let loading = $state(true);

	let tools = $state<ToolDescription[]>([]);
	let source = $state<'tools' | 'services' | ''>('');
	let entries = $state<EntityRegistryEntry[]>([]);
	let states = $state<EntityState[]>([]);
	/** The one search (M55): filters every fold on this page at once. */
	let query = $state('');

	/** What the three sources of callables report, for the disclosure headers. */
	let extCount = $state(0);
	let extMatches = $state(0);
	let mcpMatches = $state(0);
	let skillMatches = $state(0);
	let mcpCount = $state(0);
	let skillCount = $state(0);

	/**
	 * Which folds are open. The three a person looks at first are; the rarer
	 * three — servers, skills, exposure — are one click in.
	 */
	let folds = $state<Record<string, boolean>>({
		extensions: true,
		callables: true,
		'test-run': true,
		mcp: false,
		skills: false,
		exposure: false
	});

	/** Ticks when the catalogue installs something, so the folds re-read. */
	let installEpoch = $state(0);
	/** The MCP fold's add form, opened from the catalogue's one MCP line. */
	let mcpAdding = $state(false);

	/**
	 * The catalogue's "add by URL": open the MCP fold ON its form and put the
	 * caret in it. The fold is closed at rest, so a sentence saying "below"
	 * with nothing to press would be a pointer to a form nobody can see.
	 */
	async function addMcp(): Promise<void> {
		folds.mcp = true;
		mcpAdding = true;
		await tick();
		const name = document.querySelector<HTMLElement>('[data-testid="mcp-name"]');
		name?.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
		name?.focus({ preventScroll: true });
	}

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

	/**
	 * What the open editor started as, and whether it still is that.
	 *
	 * Rebuilt from the row each time rather than snapshotted at open, so a form
	 * that has just been saved and re-read is not reported as still edited.
	 */
	let pristineTool = $derived.by<ToolForm>(() => {
		if (editing === 'new' || !editing) return blankToolForm();
		const row = rowMap.get(editing);
		return row ? toolFormFromRow(row) : blankToolForm();
	});
	let dirty = $derived(Boolean(editing) && formsDiffer(form, pristineTool));

	/**
	 * A tool's URL template, headers and field schema are typed by hand and
	 * exist nowhere else until CREATE is pressed. Opening another editor used to
	 * take all of it with no warning.
	 */
	const discard = new DiscardGuard((target) =>
		toasts.info(
			editing === 'new' ? 'Unsaved new tool' : `Unsaved changes to ${editing}`,
			target === editing ? 'Press again to discard it.' : 'Press again to discard them.'
		)
	);

	/** CANCEL. Goes through the same guard: it is a discard like any other. */
	function closeEditor(): void {
		if (!discard.allows(editing, dirty)) return;
		editing = '';
	}

	function openNewTool(): void {
		if (!discard.allows('new', dirty)) return;
		editing = editing === 'new' ? '' : 'new';
		form = blankToolForm();
		formError = '';
		confirming = '';
	}

	function openEditTool(row: ToolRow): void {
		if (!discard.allows(row.name, dirty)) return;
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
			document.querySelector<HTMLElement>(`[data-testid="tool-field-${parsed.field}"]`)?.focus();
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
			discard.reset();
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
		// `dedupeByName` on the way out, not just `seen` on the way in: `seen`
		// only protects the second source from the first, and a duplicate
		// WITHIN the first reached the keyed `{#each}` below and threw
		// `each_key_duplicate`, blanking the whole runner with nothing on
		// screen to explain it.
		return dedupeByName([
			...tools,
			...rows
				.filter((row) => !seen.has(row.name))
				.map((row) => ({
					name: row.name,
					description: row.description,
					domain: row.domain,
					parameters: row.parameters ?? undefined
				}))
		]);
	});
	let visibleTools = $derived(
		catalogue.filter((t) => `${t.name} ${t.description ?? ''}`.toLowerCase().includes(query.trim().toLowerCase()))
	);
	let visibleEntities = $derived(
		entries
			.filter((e) => {
				const needle = query.trim().toLowerCase();
				if (!needle) return true;
				return (
					e.entity_id.toLowerCase().includes(needle) ||
					friendlyName(stateMap.get(e.entity_id), e).toLowerCase().includes(needle)
				);
			})
			.sort((a, b) => a.entity_id.localeCompare(b.entity_id))
	);
	let exposedCount = $derived(entries.filter((e) => e.exposed !== false).length);
	/**
	 * What the test runner may run, and what it is pointed at.
	 *
	 * Both read the CATALOGUE — the union of what the model sees and what this
	 * console manages — rather than `tools`, which is only the first half of it.
	 * A tool created here against a backend whose `jarvis/tools/list` does not
	 * report it yet was listed, was runnable, and showed no description at all,
	 * because the description was looked up in a list it was never in.
	 */
	let runnable = $derived(runnerOptions(catalogue, visibleTools, selected));
	$effect(() => {
		selected = runnerSelection(runnable, selected);
	});
	let selectedTool = $derived(catalogue.find((t) => t.name === selected));

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
			// A held tool did not run, and saying "Ran it" over an approval
			// card is the kind of small lie that teaches people to distrust
			// the whole page. jarvis-core answers `approval_required`; the
			// banner at the top of the console is where it gets answered.
			const held =
				(outcome as any)?.result?.status === 'approval_required' ||
				(outcome as any)?.status === 'approval_required';
			if (held) {
				toasts.info(
					`${selected} is waiting for you`,
					'it needs approval — answer it in the banner at the top'
				);
			} else {
				toasts.success(`Ran ${selected}`);
			}
		} catch (e) {
			err = describeError(e);
			toasts.error(`${selected} failed`, describeError(e));
		} finally {
			busy = false;
		}
	}

	// Dial and load, as a function the RECONNECT button can run again. See
	// `$lib/ui` OfflineState for why a page’s socket does not reattach on its own.
	let disposed = false;
	let redialling = $state(false);
	// The socket being replaced reports its close asynchronously; without a
	// generation the late 'closed' overwrites the new socket's 'open'.
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mine = ++dial;
		conn?.close();
		conn = null;
		err = '';
		loading = true;
		try {
			const connection = await openConnection({
				onStatus: (s) => {
					if (mine === dial) status = s;
				}
			});
			if (disposed || mine !== dial) {
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
			redialling = false;
			if (!disposed) loading = false;
		}
	}

	onMount(() => {
		disposed = false;
		void connect();
		return () => {
			disposed = true;
			conn?.close();
			conn = null;
		};
	});

	// The screen's status region. Loading and empty belong to the individual
	// lists below (this page has more than one); what is page-wide is the link
	// being down and the page's own failure, and `ScreenState` owns both.
	let screen = $derived<'ready' | 'error' | 'offline'>(
		status === 'closed' || status === 'error' ? 'offline' : err ? 'error' : 'ready'
	);
</script>

{#snippet toolEditor()}
	{#if formError}
		<p class="bad" data-testid="tool-form-error" role="alert">{formError}</p>
	{/if}

	<Field
		label="Name"
		hint={editing === 'new'
			? 'What the model says to call it. Lowercase letters, digits and underscores.'
			: 'A tool cannot be renamed — the model calls it by this word.'}
	>
		<Input bind:value={form.name} testid="tool-field-name" placeholder="paperless_search" disabled={editing !== 'new'} mono />
	</Field>

	<Field label="Description" hint="This is all the model has to decide when to use it. Be specific.">
		<Input bind:value={form.description} testid="tool-field-description" placeholder="Search Paperless-ngx documents by query text" />
	</Field>

	<div class="two">
		<Field label="Tier">
			<Select
				bind:value={form.tier}
				testid="tool-field-tier"
				options={[
					{ value: '1', label: '1 — run it' },
					{ value: '2', label: '2 — run it and tell me' },
					{ value: '3', label: '3 — ask me first' }
				]}
			/>
		</Field>
		<Field label="Method">
			<Select bind:value={form.method} testid="tool-field-method" options={METHODS.map((method) => ({ value: method, label: method }))} />
		</Field>
	</div>

	<Field label="URL" hint="Field values are percent-encoded into the URL.">
		<Input bind:value={form.url} testid="tool-field-url" placeholder="http://paperless.lan/api/documents/?query=&#123;&#123; query &#125;&#125;" mono />
	</Field>

	<Field label="Fields" hint="JSON. What the model may fill in, and which are required.">
		<Input bind:value={form.fields} testid="tool-field-fields" rows={5} mono />
	</Field>

	<Field label="Headers" hint="JSON. Auth tokens go here; they stay on the server.">
		<Input bind:value={form.headers} testid="tool-field-headers" rows={3} mono />
	</Field>

	<Field label="Body" hint="JSON, for POST/PUT/PATCH. Blank sends none.">
		<Input bind:value={form.payload} testid="tool-field-payload" rows={3} mono />
	</Field>

	<div class="editor-acts">
		<Button variant="primary" testid="tool-save" disabled={saving} title={saving ? 'Saving' : 'Save the tool'} onclick={saveTool}>
			{saving ? 'SAVING…' : editing === 'new' ? 'CREATE' : 'SAVE'}
		</Button>
		<Button testid="tool-cancel" title="Close the editor" onclick={closeEditor}>CANCEL</Button>
		{#if editing !== 'new'}
			{@const row = rowMap.get(editing)}
			{#if row?.editable}
				<!-- Deleting lives with editing (M55): a row is USE and EDIT at rest. -->
				<Button
					variant="danger"
					testid="tool-delete-{editing}"
					disabled={removing === editing}
					title={removing === editing ? 'Deleting' : 'Delete it; press twice'}
					aria-label="Delete {editing}"
					onclick={() => removeTool(row)}
				>
					{removing === editing ? 'DELETING…' : confirming === editing ? 'CONFIRM?' : 'DELETE'}
				</Button>
			{/if}
		{/if}
	</div>
{/snippet}

<!--
  One disclosure per topic. The page was seven panels stacked at full density;
  what a person comes for is one of them, so each is a fold with its count on
  the header, the three you look at first open and the rest one click in.
-->
{#snippet fold(id: string, title: string, meta: string, body: Snippet)}
	<!-- `bind:open`, not `open={…}`: the header's count changes when a child
	     finishes loading, which re-renders this snippet, and a plain attribute
	     would put the fold back to its default — closing the one somebody had
	     just opened. -->
	<details class="fold" bind:open={folds[id]} data-testid="tools-section-{id}">
		<summary>
			<span>{title}</span>
			<span class="meta">{meta}</span>
		</summary>
		<div class="fold-body">{@render body()}</div>
	</details>
{/snippet}

{#snippet extensionsBody()}
	<Extensions {conn} {query} epoch={installEpoch} bind:count={extCount} bind:matches={extMatches} />
{/snippet}

{#snippet callablesBody()}
	<div class="bar">
		{#if manageable}
			<Button testid="tool-new" aria-expanded={editing === 'new'} title="An HTTP call the assistant may make" onclick={openNewTool}>
				{editing === 'new' ? 'CANCEL' : '+ NEW TOOL'}
			</Button>
		{/if}
	</div>
	{#if manageable}
		<p class="note">
			A tool is an HTTP call the assistant may make. Built-ins and *.tool.yaml manifests are listed
			but cannot be changed here.
		</p>
	{/if}

	{#if editing === 'new'}
		<div class="editor" data-testid="tool-editor-new">
			{@render toolEditor()}
		</div>
	{/if}

	{#if loading && !tools.length}
		<SkeletonRows rows={5} label="Loading the tool catalogue" />
	{:else}
		<ul class="rows">
			{#each visibleTools as tool, i (tool.name)}
				{@const row = rowMap.get(tool.name)}
				<li class="tool jv-stagger" style={staggerStyle(i)} data-testid="tool-{tool.name}" data-jv-row>
					<div class="what">
						<b>{tool.name}</b>
						<span class="desc">{tool.description || 'no description'}</span>
					</div>
					<div class="acts">
						<Button aria-label="Load {tool.name} into the test runner" title="Load it into the test runner" onclick={() => (selected = tool.name)}>USE</Button>
						{#if row?.editable}
							<Button
								testid="tool-edit-{tool.name}"
								aria-expanded={editing === tool.name}
								aria-label="Edit {tool.name}"
								onclick={() => openEditTool(row)}
							>
								{editing === tool.name ? 'CLOSE' : 'EDIT'}
							</Button>
						{:else if manageable}
							<Pill testid="tool-builtin-{tool.name}">BUILT IN</Pill>
						{/if}
					</div>
					{#if row?.editable && editing === tool.name}
						<div class="editor wide" data-testid="tool-editor-{tool.name}">
							{@render toolEditor()}
						</div>
					{/if}
				</li>
			{:else}
				<li class="empty-row">
					<EmptyState
						testid="empty"
						title={status === 'open' ? 'No tools matched' : 'No link to the backend'}
						body={status === 'open'
							? 'Nothing in the catalogue matches that filter.'
							: `The websocket relay is ${status}.`}
					/>
				</li>
			{/each}
		</ul>
	{/if}
{/snippet}

{#snippet runnerBody()}
	<div class="runner">
		<Select bind:value={selected} testid="tool-select" options={runnable.map((tool) => ({ value: tool.name, label: tool.name }))} />
		<div class="grow">
			<Input bind:value={args} testid="tool-args" placeholder={'{"entity_id": "light.lab_lights"}'} mono />
		</div>
		<Button testid="tool-run" disabled={busy || !selected} title={busy ? 'Running' : !selected ? 'Pick a tool first' : 'Call it, through the same gate the assistant uses'} onclick={testRun}>
			{busy ? 'RUNNING…' : 'RUN'}
		</Button>
	</div>
	{#if selectedTool?.description}
		<p class="note">{selectedTool.description}</p>
	{/if}
	{#if selectedTool?.needs_approval}
		<p class="note held" data-testid="tool-needs-approval">
			<Pill tone="warn">tier {selectedTool.tier ?? 3}</Pill>
			<span>Running this asks you first. It will not run until you answer the approval banner.</span>
		</p>
	{:else if selectedTool?.may_escalate}
		<p class="note" data-testid="tool-may-escalate">
			May ask for approval, depending on what it is pointed at.
		</p>
	{/if}
	{#if result}
		<pre data-testid="tool-result" aria-live="polite">{result}</pre>
	{/if}
{/snippet}

{#snippet mcpBody()}
	<McpServers {conn} {query} epoch={installEpoch} bind:count={mcpCount} bind:matches={mcpMatches} bind:adding={mcpAdding} />
{/snippet}

{#snippet skillsBody()}
	<SkillsPanel {conn} {query} epoch={installEpoch} bind:count={skillCount} bind:matches={skillMatches} />
{/snippet}

{#snippet exposureBody()}
	<p class="note">
		Unexposed entities stay out of the LLM's prompt and cannot be targeted by voice.
	</p>
	<ul class="rows">
		{#each visibleEntities as entry, i (entry.entity_id)}
			{@const exposed = entry.exposed !== false}
			<li class="entity jv-stagger" style={staggerStyle(i)} data-testid="expose-row-{entry.entity_id}" data-jv-row>
				<div class="what">
					<span class="friendly">{friendlyName(stateMap.get(entry.entity_id), entry)}</span>
					<code>{entry.entity_id}</code>
				</div>
				<!-- The switch, with its word: the test id sits on the pair so the
				     label reads EXPOSED or HIDDEN and a click anywhere on it flips it. -->
				<span class="expose" data-testid="expose-{entry.entity_id}">
					<Toggle
						checked={exposed}
						label={exposed ? 'EXPOSED' : 'HIDDEN'}
						onchange={() => toggleExposure(entry)}
					/>
				</span>
			</li>
		{:else}
			<li class="empty-row"><p class="note">No entity registry entries on this backend.</p></li>
		{/each}
	</ul>
{/snippet}

<div class="stack">
	<p class="lede" data-testid="tools-screen">
		{tools.length} callable{tools.length === 1 ? '' : 's'} · {exposedCount} exposed entit{exposedCount === 1 ? 'y' : 'ies'} · link {status}
	</p>

	<ScreenState
		status={screen}
		errorTitle="This page hit an error"
		errorDetail={err}
		onretry={connect}
		onreconnect={connect}
		busy={redialling}
		errorTestid="error"
	/>

	{#if hint}<p class="line warn" data-testid="hint">{hint}</p>{/if}

	<!-- One search over everything below (M55): the catalogue, extensions,
	     callables, MCP servers, skills and exposure all read `query`; each
	     header says how many of its rows match. -->
	<div class="bar search">
		<div class="grow">
			<label class="jv-sr-only" for="tool-filter">Search the catalogue, extensions, tools, servers, skills and entities</label>
			<input
				id="tool-filter"
				class="filter"
				type="text"
				placeholder="search everything  ( / )"
				data-testid="tool-filter"
				data-jv-filter
				bind:value={query}
			/>
		</div>
	</div>

	<!-- The catalogue, above the folds (M65): what can be added is the first
	     thing on the screen, because "I can't browse the tools" was the
	     operator's report and the browse button was inside a fold. It installs
	     with ghost row controls; NEW SKILL, in the Extensions fold, stays the
	     page's one filled primary — the shipped entries are already installed
	     on a fresh box, so lighting INSTALL would light nothing most days, and
	     writing a skill is still what this page is for. -->
	<Catalogue
		{conn}
		{query}
		offline={screen === 'offline'}
		onaddmcp={addMcp}
		oninstalled={() => (installEpoch += 1)}
	/>
	<!-- The house's n8n (M77): one line beside the catalogue, always in view,
	     saying whether it answers — a connection that lives only in .env is
	     invisible to the operator, who judges capability from this screen. -->
	<N8nConnection {conn} />

	<!-- Above the toolbox, because it is the thing that DECIDES the toolbox: what
	     is installed and what it holds is the cause, and the tool list below is
	     the effect. -->
	{@render fold('extensions', 'Extensions', query ? `${extMatches} of ${extCount} match` : `${extCount} installed`, extensionsBody)}
	{@render fold('callables', 'Callables', query ? `${visibleTools.length} of ${catalogue.length} match` : `${catalogue.length}`, callablesBody)}
	{@render fold('test-run', 'Test run', source === 'tools' ? 'jarvis/tools/call' : 'call_service', runnerBody)}
	<!-- Beside the tool editor rather than on its own page: "a tool the assistant
	     may call" is one idea, and an MCP server is the way you get a hundred of
	     them at once. -->
	{@render fold('mcp', 'MCP servers', query ? `${mcpMatches} of ${mcpCount} match` : `${mcpCount} configured`, mcpBody)}
	<!-- And the other way a capability arrives: not a tool the assistant may call,
	     but instructions this house has written down. Same page, because "what can
	     it do" is one question. -->
	{@render fold('skills', 'Skills', query ? `${skillMatches} of ${skillCount} match` : `${skillCount} loaded`, skillsBody)}
	{@render fold('exposure', 'Entity exposure', query ? `${visibleEntities.length} match · ${exposedCount} exposed` : `${exposedCount} of ${entries.length} exposed`, exposureBody)}
</div>

<style>
	.stack {
		display: grid;
		gap: var(--jv-space-4);
	}
	/* A sentence with counts in it, so the body face: a whole paragraph in
	   mono is the M48 look the direction retired, and the look spec reads it
	   as such. */
	.lede {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.line {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.line.warn {
		color: var(--jv-warn);
	}

	/* A fold: a flat panel whose head is its own disclosure. */
	.fold {
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.fold > summary {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		cursor: pointer;
		list-style: none;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.fold > summary:hover {
		color: var(--jv-text);
	}
	.fold > summary::-webkit-details-marker {
		display: none;
	}
	.fold > summary::after {
		content: '▸';
		color: var(--jv-text-faint);
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	.fold[open] > summary {
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.fold[open] > summary::after {
		transform: rotate(90deg);
	}
	.meta {
		margin-left: auto;
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		letter-spacing: var(--jv-track-tight);
		text-transform: none;
		color: var(--jv-text-faint);
	}
	.fold-body {
		padding: var(--jv-space-4);
	}

	.bar {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.grow {
		flex: 1 1 16rem;
		min-width: 0;
	}
	/* The `/` filter: a raw input because it carries an id for its label and
	   the `data-jv-filter` hook the layout focuses; drawn as Input is. */
	.filter {
		width: 100%;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.filter::placeholder {
		color: var(--jv-text-faint);
	}
	.filter:hover {
		border-color: var(--jv-line);
	}
	.note {
		margin: var(--jv-space-3) 0 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 70ch;
	}
	.note.held {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		color: var(--jv-text);
	}
	.bad {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.rows {
		list-style: none;
		margin: var(--jv-space-2) 0 0;
		padding: 0;
	}
	.tool,
	.entity {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.tool:last-child,
	.entity:last-child {
		border-bottom: 0;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	/* A tool's name is what the model says: data, so mono. */
	.what b {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
	}
	.desc {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.friendly {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
	}
	code {
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
		flex-wrap: wrap;
		justify-content: flex-end;
	}
	.expose {
		display: inline-block;
	}
	.empty-row {
		padding: var(--jv-space-3) 0 0;
	}
	/* The editor, inset from the list it edits. */
	.editor {
		display: grid;
		gap: var(--jv-space-3);
		margin: var(--jv-space-3) 0 0;
		padding: var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-left: var(--jv-rule-live) solid var(--jv-accent);
		border-radius: var(--jv-radius-md);
		background: var(--jv-bg-raised);
	}
	.editor.wide {
		grid-column: 1 / -1;
	}
	.two {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		gap: var(--jv-space-3);
	}
	.editor-acts {
		display: flex;
		gap: var(--jv-space-2);
	}
	.runner {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	pre {
		margin: var(--jv-space-3) 0 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.6;
		color: var(--jv-text);
		background: var(--jv-surface-sunken);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-3);
		overflow-x: auto;
		max-height: var(--jv-measure-log);
	}
	@media (max-width: 640px) {
		.tool,
		.entity {
			grid-template-columns: minmax(0, 1fr);
		}
		.acts {
			justify-content: flex-start;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.fold > summary::after {
			transition: none;
		}
	}
</style>
