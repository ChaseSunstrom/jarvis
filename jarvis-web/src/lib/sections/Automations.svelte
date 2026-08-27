<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import { DiscardGuard, formsDiffer } from '$lib/unsaved';
	import { Button, EmptyState, Input, Panel, Pill, ScreenState, SkeletonRows, Toolbar } from '$lib/ui';
	import {
		applyStateChanged,
		friendlyName,
		type AutomationRow,
		type EntityRegistryEntry,
		type EntityState,
		type Subscription
	} from '$lib/jarvisClient';
	import {
		MODES,
		blankForm,
		formFromRow,
		parseForm,
		readOnlyNote,
		type DraftForm
	} from '$lib/automationDraft';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let flash = $state('');
	let loading = $state(true);
	let filter = $state('');
	let states = $state<EntityState[]>([]);
	let entries = $state<EntityRegistryEntry[]>([]);

	/**
	 * The automations as jarvis-core describes them, which is a different thing
	 * from their entities: only this says which ones the console may edit, and
	 * carries the trigger/action bodies the editor needs.
	 */
	let rows = $state<AutomationRow[]>([]);
	let rowMap = $derived(new Map(rows.map((row) => [row.entity_id, row])));
	// Also by id, because that is what `editing` holds — the entity id is what
	// the LIST is keyed by, and the two are different strings.
	let rowById = $derived(new Map(rows.map((row) => [row.id, row])));

	/** '' when closed, 'new' for the create form, otherwise an automation id. */
	let editing = $state('');
	let form = $state<DraftForm>(blankForm());
	let formError = $state('');
	let saving = $state(false);
	let removing = $state('');
	/** Set for a moment after a delete so the button can ask for confirmation. */
	let confirming = $state('');
	/** False against a backend too old to know `config/automation/*`. */
	let manageable = $state(true);

	const stateMap = new Map<string, EntityState>();
	let flashTimer: ReturnType<typeof setTimeout> | undefined;
	let entryMap = $derived(new Map(entries.map((e) => [e.entity_id, e])));

	let focused = $derived(page.url.searchParams.get('focus') ?? '');
	$effect(() => {
		if (focused) filter = focused;
	});

	let automations = $derived.by(() => {
		const needle = filter.trim().toLowerCase();
		return states
			.filter((s) => s.entity_id.startsWith('automation.'))
			.filter((s) => {
				if (!needle) return true;
				const label = friendlyName(s, entryMap.get(s.entity_id)).toLowerCase();
				return label.includes(needle) || s.entity_id.toLowerCase().includes(needle);
			})
			.sort((a, b) => a.entity_id.localeCompare(b.entity_id));
	});

	function fmtTime(value: unknown): string {
		if (!value) return 'never';
		const d = new Date(String(value));
		return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
	}

	function publish(): void {
		states = [...stateMap.values()];
	}

	async function act(entityId: string, service: string, note: string): Promise<void> {
		if (!conn) return;
		err = '';
		const label = friendlyName(stateMap.get(entityId), entryMap.get(entityId));
		try {
			await conn.client.callService('automation', service, { entity_id: entityId });
			flash = `${note} ${entityId}`;
			toasts.success(serviceSuccessText(service, label), entityId);
			// One timer, restarted: back-to-back clicks used to stack timers, and
			// the oldest would blank a flash the newest had just set.
			clearTimeout(flashTimer);
			flashTimer = setTimeout(() => (flash = ''), 2500);
		} catch (e) {
			err = describeError(e);
			toasts.error(serviceFailureText(service, label), describeError(e));
		}
	}

	async function refreshRows(): Promise<void> {
		if (!conn) return;
		try {
			rows = (await conn.client.listAutomations()) ?? [];
		} catch (e) {
			// An older jarvis-core has no such command. The page still lists and
			// runs automations from their entities; it just cannot edit them.
			rows = [];
			manageable = false;
			console.warn('automation list unavailable', e);
		}
	}

	/**
	 * What the open editor started as, so "has this been edited" is answerable.
	 *
	 * Rebuilt from the row rather than remembered at open time: the row is
	 * refreshed after every save, and comparing against a stale snapshot would
	 * report a just-saved form as still dirty.
	 */
	let pristine = $derived.by<DraftForm>(() => {
		if (editing === 'new' || !editing) return blankForm();
		const row = rowById.get(editing);
		return row ? formFromRow(row) : blankForm();
	});
	let dirty = $derived(Boolean(editing) && formsDiffer(form, pristine));

	/**
	 * Unsaved edits survive the press that would have discarded them.
	 *
	 * An automation's triggers and actions are JSON somebody typed by hand; they
	 * are the most expensive thing on this console to lose, and opening another
	 * row used to reassign the form with no warning at all.
	 */
	const discard = new DiscardGuard((target) =>
		toasts.info(
			editing === 'new' ? 'Unsaved new automation' : `Unsaved changes to ${labelOf(editing)}`,
			target === editing ? 'Press again to discard it.' : 'Press again to discard them.'
		)
	);

	/** An automation's own name, for a message about it. */
	function labelOf(id: string): string {
		return rowById.get(id)?.alias ?? id;
	}

	/** CANCEL. Goes through the same guard: it is a discard like any other. */
	function closeEditor(): void {
		if (!discard.allows(editing, dirty)) return;
		editing = '';
	}

	function openNew(): void {
		if (!discard.allows('new', dirty)) return;
		editing = editing === 'new' ? '' : 'new';
		form = blankForm();
		formError = '';
		confirming = '';
	}

	function openEdit(row: AutomationRow): void {
		if (!discard.allows(row.id, dirty)) return;
		if (editing === row.id) {
			editing = '';
			return;
		}
		editing = row.id;
		form = formFromRow(row);
		formError = '';
		confirming = '';
	}

	async function save(): Promise<void> {
		if (!conn || !editing) return;
		const parsed = parseForm(form);
		if (!parsed.ok) {
			formError = parsed.error;
			// Put the cursor where the problem is rather than leaving the user to
			// work out which of three JSON boxes the message is about.
			document.getElementById(`field-${parsed.field}`)?.focus();
			return;
		}
		saving = true;
		formError = '';
		err = '';
		try {
			if (editing === 'new') {
				await conn.client.createAutomation(parsed.draft);
				toasts.success(`Created ${parsed.draft.alias}`);
			} else {
				await conn.client.updateAutomation(editing, parsed.draft);
				toasts.success(`Saved ${parsed.draft.alias}`);
			}
			editing = '';
			discard.reset();
			await refreshRows();
			await refreshStates();
		} catch (e) {
			// The server re-runs every check this form does and knows things it
			// cannot, so its message is the one worth showing.
			formError = describeError(e);
		} finally {
			saving = false;
		}
	}

	async function remove(row: AutomationRow): Promise<void> {
		if (!conn) return;
		if (confirming !== row.id) {
			// Two clicks, not a modal: an automation is recoverable only by
			// typing it again, and a native confirm() blocks the whole tab.
			confirming = row.id;
			setTimeout(() => {
				if (confirming === row.id) confirming = '';
			}, 4000);
			return;
		}
		confirming = '';
		removing = row.id;
		err = '';
		try {
			await conn.client.deleteAutomation(row.id);
			toasts.success(`Deleted ${row.alias}`);
			if (editing === row.id) editing = '';
			await refreshRows();
			await refreshStates();
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not delete ${row.alias}`, describeError(e));
		} finally {
			removing = '';
		}
	}

	async function refreshStates(): Promise<void> {
		if (!conn) return;
		const fresh = await conn.client.getStates();
		stateMap.clear();
		for (const state of fresh) stateMap.set(state.entity_id, state);
		publish();
	}

	// Dial, load, subscribe — as a function the RECONNECT button can run again.
	// See `$lib/ui` OfflineState for why a page’s socket does not reattach.
	let disposed = false;
	let sub: Subscription | null = null;
	let redialling = $state(false);
	// The socket being replaced reports its close asynchronously; without a
	// generation the late 'closed' overwrites the new socket's 'open'.
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mine = ++dial;
		void sub?.unsubscribe();
		sub = null;
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
			const fresh = await connection.client.getStates();
			stateMap.clear();
			for (const state of fresh) stateMap.set(state.entity_id, state);
			publish();
			try {
				entries = (await connection.client.listEntities()) ?? [];
			} catch {
				entries = [];
			}
			await refreshRows();
			sub = await connection.client.subscribeEvents((event) => {
				if (applyStateChanged(stateMap, event)) publish();
			}, 'state_changed');
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
			clearTimeout(flashTimer);
			void sub?.unsubscribe();
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


{#snippet editorFields()}
	{#if formError}
		<p class="problem" data-testid="form-error" role="alert">{formError}</p>
	{/if}

	<div class="fields">
		<label class="field">
			<span class="label">Name</span>
			<input id="field-alias" type="text" class="in" data-testid="field-alias" placeholder="Porch light at dusk" bind:value={form.alias} />
		</label>

		<label class="field">
			<span class="label">Description</span>
			<input id="field-description" type="text" class="in" data-testid="field-description" placeholder="optional" bind:value={form.description} />
		</label>

		<label class="field">
			<span class="label">Mode</span>
			<select id="field-mode" class="in" data-testid="field-mode" bind:value={form.mode}>
				{#each MODES as mode (mode)}<option value={mode}>{mode}</option>{/each}
			</select>
		</label>
	</div>

	<label class="field">
		<span class="label">Triggers</span>
		<textarea id="field-trigger" class="in code" rows="6" spellcheck="false" data-testid="field-trigger" bind:value={form.trigger}></textarea>
		<span class="hint">JSON. What starts it — e.g. <code>{'{"platform": "time", "at": "21:00:00"}'}</code></span>
	</label>

	<label class="field">
		<span class="label">Conditions</span>
		<textarea id="field-condition" class="in code" rows="3" spellcheck="false" data-testid="field-condition" bind:value={form.condition}></textarea>
		<span class="hint">JSON. Optional — leave as <code>[]</code> to always run.</span>
	</label>

	<label class="field">
		<span class="label">Actions</span>
		<textarea id="field-action" class="in code" rows="6" spellcheck="false" data-testid="field-action" bind:value={form.action}></textarea>
		<span class="hint">JSON. What it does — e.g. <code>{'{"service": "light.turn_on"}'}</code></span>
	</label>

	<div class="actions">
		<Button variant="primary" testid="save" disabled={saving} onclick={save}>
			{saving ? 'Saving…' : editing === 'new' ? 'Create' : 'Save'}
		</Button>
		<Button testid="cancel" onclick={closeEditor}>Cancel</Button>
	</div>
{/snippet}

<p class="lede" data-testid="automations-screen">{automations.length} automation(s) · link {status}</p>

<ScreenState
	status={screen}
	errorTitle="This page hit an error"
	errorDetail={err}
	onretry={connect}
	onreconnect={connect}
	busy={redialling}
	errorTestid="error"
/>

{#if flash}<p class="flash" data-testid="flash" role="status">{flash}</p>{/if}

<div class="tools">
	<Toolbar>
		{#snippet children()}
			<label class="jv-sr-only" for="automation-filter">Filter automations</label>
			<div class="filter">
				<Input bind:value={filter} placeholder="Filter  ( / )" testid="filter" />
			</div>
		{/snippet}
		{#snippet end()}
			{#if manageable}
				<span class="aside">Automations created here are stored separately from your automations.yaml.</span>
				<!-- The one primary action on this screen — unless the form is
				     already open, when SAVE inside it takes over and this quietens
				     to its cancel. -->
				<Button
					variant={editing === 'new' ? 'ghost' : 'primary'}
					testid="new"
					aria-expanded={editing === 'new'}
					onclick={openNew}
				>
					{editing === 'new' ? 'Cancel' : '+ New automation'}
				</Button>
			{/if}
		{/snippet}
	</Toolbar>
</div>

<div class="panels">
	{#if manageable && editing === 'new'}
		<Panel title="New automation">
			{#snippet children()}
				<div class="editor" data-testid="editor-new">
					{@render editorFields()}
				</div>
			{/snippet}
		</Panel>
	{/if}

	<Panel title="Registered" meta={loading ? '…' : String(automations.length)}>
		{#snippet children()}
			{#if loading && !states.length}
				<div class="pad"><SkeletonRows rows={4} label="Loading automations" /></div>
			{:else}
				{#each automations as automation, i (automation.entity_id)}
					{@const on = automation.state === 'on'}
					{@const row = rowMap.get(automation.entity_id)}
					{@const name = friendlyName(automation, entryMap.get(automation.entity_id))}
					<div
						class="line jv-stagger"
						class:open={row && editing === row.id}
						style={staggerStyle(i)}
						data-testid="automation-{automation.entity_id}" data-jv-row
					>
						<span class="who">
							<b>{name}</b>
							<span class="eid">{automation.entity_id}</span>
						</span>
						<span class="last" data-testid="last-{automation.entity_id}" title="Last triggered">
							{fmtTime(automation.attributes?.last_triggered)}
						</span>
						<span class="state" class:on data-testid="state-{automation.entity_id}">{automation.state}</span>
						{#if row?.reach}
							<!-- The server's one sentence on what the actions reach ("can
							     lock"): sent since M25 and shown nowhere until M99. -->
							<span class="eid" data-testid="reach-{automation.entity_id}">{row.reach}</span>
						{/if}
						{#if row?.needs_approval}
							<!-- Worth knowing before you press RUN NOW, and it explains the
							     approval prompt when it appears. -->
							<Pill tone="warn" testid="gated-{automation.entity_id}">needs ok</Pill>
						{/if}
						{#if row && !row.editable}
							<!-- Said on the row, not hidden behind a disabled button: the
							     question "why can I edit that one and not this one" should
							     not need a hover to answer. -->
							<span class="yaml" data-testid="yaml-{automation.entity_id}" title={readOnlyNote(row)}>from yaml</span>
						{/if}
						<span class="acts">
							<Button
								pressed={on}
								testid="toggle-{automation.entity_id}"
								aria-label="{on ? 'Disable' : 'Enable'} {name}"
								onclick={() =>
									act(automation.entity_id, on ? 'turn_off' : 'turn_on', on ? 'disabled' : 'enabled')}
							>
								{on ? 'Disable' : 'Enable'}
							</Button>
							<!-- One switch at rest (M55); what you do to an automation less often is one click in. -->
							<details class="more" data-jv-more data-testid="more-{automation.entity_id}">
								<summary aria-label="More for {name}">MORE</summary>
								<span class="more-body">
								<Button testid="trigger-{automation.entity_id}"
									aria-label="Run {name} now"
									onclick={() => act(automation.entity_id, 'trigger', 'triggered')}
								>
									Run now
								</Button>
								{#if row?.editable}
									<Button testid="edit-{automation.entity_id}"
										aria-expanded={editing === row.id}
										aria-label="Edit {row.alias}"
										onclick={() => openEdit(row)}
									>
										{editing === row.id ? 'Close' : 'Edit'}
									</Button>
									<Button variant="danger" testid="delete-{automation.entity_id}"
										disabled={removing === row.id}
										aria-label="Delete {row.alias}"
										onclick={() => remove(row)}
									>
										{removing === row.id ? 'DELETING…' : confirming === row.id ? 'CONFIRM?' : 'DELETE'}
									</Button>
								{/if}
								</span>
							</details>
						</span>
					</div>

					{#if row && editing === row.id}
						<div class="editor" data-testid="editor-{row.id}">
							<p class="entity-id">{row.entity_id} · {row.id}</p>
							{@render editorFields()}
						</div>
					{/if}
				{:else}
					{#if status === 'open'}
						<div class="pad">
							<EmptyState
								testid="empty"
								title="No automations"
								body={filter
									? `Nothing matches “${filter}”.`
									: 'This backend has no automations configured. Add one in jarvis-core and it will appear here, with its last trigger time.'}
							/>
						</div>
					{:else}
						<div class="pad">
							<EmptyState
								testid="empty"
								title="No link to the backend"
								body={`The websocket relay is ${status}.`}
							/>
						</div>
					{/if}
				{/each}
			{/if}
		{/snippet}
	</Panel>
</div>

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.flash {
		margin: 0 0 var(--jv-space-3);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-ok);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.tools {
		margin-bottom: var(--jv-space-4);
	}
	.filter {
		width: min(100%, calc(var(--jv-space-7) * 6));
	}
	.aside {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.panels {
		display: grid;
		gap: var(--jv-space-4);
	}
	.panels :global(.body) {
		padding: 0 var(--jv-space-4);
	}
	.pad {
		padding: var(--jv-space-3) 0;
	}
	.line {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.line:last-child,
	.line.open {
		border-bottom: 0;
	}
	.who {
		flex: 1 1 12rem;
		min-width: 0;
		display: grid;
		gap: var(--jv-space-1);
	}
	.who b {
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.eid,
	.last,
	.entity-id {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.last {
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.state,
	.yaml {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: 0 var(--jv-space-2);
		line-height: 1.7;
		white-space: nowrap;
	}
	.state.on {
		color: var(--jv-accent);
		border-color: color-mix(in srgb, var(--jv-accent) 40%, transparent);
	}
	.acts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.editor {
		display: grid;
		gap: var(--jv-space-4);
		margin: 0 calc(-1 * var(--jv-space-4)) var(--jv-space-2);
		padding: var(--jv-space-4);
		background: var(--jv-surface-sunken);
		border-top: 1px solid var(--jv-line-hair);
		border-bottom: 1px solid var(--jv-line-hair);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.entity-id {
		margin: 0;
	}
	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
		gap: var(--jv-space-4);
	}
	.field {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.label {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.in {
		width: 100%;
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
	.in:hover {
		border-color: var(--jv-line);
	}
	/* Structured JSON: a proportional font makes indentation unreadable. */
	.in.code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		resize: vertical;
		white-space: pre;
		overflow-wrap: normal;
		overflow-x: auto;
	}
	.hint {
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.hint code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-dim);
	}
	.problem {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-danger-text);
	}
	.actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
	}
	@media (max-width: 640px) {
		.panels :global(.body) {
			padding: 0 var(--jv-space-3);
		}
		.editor {
			margin: 0 calc(-1 * var(--jv-space-3)) var(--jv-space-2);
			padding: var(--jv-space-3);
		}
		.aside {
			display: none;
		}
	}
	/* MORE (M55): a hairline word that opens the row's less-used actions. */
	.more {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.more > summary {
		list-style: none;
		cursor: pointer;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
		padding: 0 var(--jv-space-2);
	}
	.more > summary::-webkit-details-marker {
		display: none;
	}
	.more[open] > summary {
		color: var(--jv-accent);
	}
	.more-body {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
</style>
