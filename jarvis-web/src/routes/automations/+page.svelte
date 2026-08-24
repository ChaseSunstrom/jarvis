<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import { DiscardGuard, formsDiffer } from '$lib/unsaved';
	import { EmptyState, ScreenState } from '$lib/ui';
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

<svelte:head><title>Jarvis · Automations</title></svelte:head>

{#snippet editorFields()}
	{#if formError}
		<p class="err" data-testid="form-error" role="alert">{formError}</p>
	{/if}

	<div class="field">
		<label for="field-alias">Name</label>
		<input id="field-alias" type="text" data-testid="field-alias" placeholder="Porch light at dusk" bind:value={form.alias} />
	</div>

	<div class="field">
		<label for="field-description">Description</label>
		<input id="field-description" type="text" data-testid="field-description" placeholder="optional" bind:value={form.description} />
	</div>

	<div class="field">
		<label for="field-mode">Mode</label>
		<select id="field-mode" data-testid="field-mode" bind:value={form.mode}>
			{#each MODES as mode (mode)}<option value={mode}>{mode}</option>{/each}
		</select>
	</div>

	<div class="field">
		<label for="field-trigger">Triggers</label>
		<textarea id="field-trigger" rows="6" spellcheck="false" data-testid="field-trigger" bind:value={form.trigger}></textarea>
		<span class="hint">JSON. What starts it — e.g. <code>{'{"platform": "time", "at": "21:00:00"}'}</code></span>
	</div>

	<div class="field">
		<label for="field-condition">Conditions</label>
		<textarea id="field-condition" rows="3" spellcheck="false" data-testid="field-condition" bind:value={form.condition}></textarea>
		<span class="hint">JSON. Optional — leave as <code>[]</code> to always run.</span>
	</div>

	<div class="field">
		<label for="field-action">Actions</label>
		<textarea id="field-action" rows="6" spellcheck="false" data-testid="field-action" bind:value={form.action}></textarea>
		<span class="hint">JSON. What it does — e.g. <code>{'{"service": "light.turn_on"}'}</code></span>
	</div>

	<div class="actions">
		<button type="button" class="btn" data-testid="save" disabled={saving} onclick={save}>
			{saving ? 'SAVING…' : editing === 'new' ? 'CREATE' : 'SAVE'}
		</button>
		<button type="button" class="btn ghost" data-testid="cancel" onclick={closeEditor}>CANCEL</button>
	</div>
{/snippet}

<h1>AUTOMATIONS</h1>
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

{#if flash}<p class="notice" data-testid="flash">{flash}</p>{/if}

{#if manageable}
	<div class="toolbar">
		<button type="button" class="btn" data-testid="new" aria-expanded={editing === 'new'} onclick={openNew}>
			{editing === 'new' ? 'CANCEL' : '+ NEW AUTOMATION'}
		</button>
		<span class="muted">Automations created here are stored separately from your automations.yaml.</span>
	</div>

	{#if editing === 'new'}
		<section class="panel">
			<div class="panel-head"><span>New automation</span></div>
			<div class="editor" data-testid="editor-new">
				{@render editorFields()}
			</div>
		</section>
	{/if}
{/if}

<section class="panel">
	<div class="panel-head">
		<span>Registered</span>
		<label class="jv-sr-only" for="automation-filter">Filter automations</label>
		<input
			id="automation-filter"
			type="text"
			placeholder="filter  ( / )"
			data-testid="filter"
			data-jv-filter
			bind:value={filter}
		/>
	</div>
	{#if loading && !states.length}
		<Skeleton rows={4} label="Loading automations" />
	{:else}
		{#each automations as automation, i (automation.entity_id)}
			{@const on = automation.state === 'on'}
			{@const row = rowMap.get(automation.entity_id)}
			<div
				class="row jv-stagger"
				style={staggerStyle(i)}
				data-testid="automation-{automation.entity_id}"
			>
				<span class="name">
					<b>{friendlyName(automation, entryMap.get(automation.entity_id))}</b>
					<span class="eid">{automation.entity_id}</span>
				</span>
				<span class="muted" data-testid="last-{automation.entity_id}">
					{fmtTime(automation.attributes?.last_triggered)}
				</span>
				<span class="pill" class:on data-testid="state-{automation.entity_id}"
					>{automation.state}</span
				>
				<button
					type="button"
					class="btn"
					class:on
					data-testid="toggle-{automation.entity_id}"
					aria-label="{on ? 'Disable' : 'Enable'} {friendlyName(
						automation,
						entryMap.get(automation.entity_id)
					)}"
					onclick={() =>
						act(automation.entity_id, on ? 'turn_off' : 'turn_on', on ? 'disabled' : 'enabled')}
				>
					{on ? 'DISABLE' : 'ENABLE'}
				</button>
				<button
					type="button"
					class="btn ghost"
					data-testid="trigger-{automation.entity_id}"
					aria-label="Run {friendlyName(automation, entryMap.get(automation.entity_id))} now"
					onclick={() => act(automation.entity_id, 'trigger', 'triggered')}
				>
					RUN NOW
				</button>
				{#if row?.needs_approval}
					<!-- Worth knowing before you press RUN NOW, and it explains the
					     approval prompt when it appears. -->
					<span
						class="pill warn"
						data-testid="gated-{automation.entity_id}"
						title="Running this needs your approval: {row.reach}"
					>
						NEEDS OK
					</span>
				{/if}
				{#if row}
					{#if row.editable}
						<button
							type="button"
							class="btn ghost"
							data-testid="edit-{automation.entity_id}"
							aria-expanded={editing === row.id}
							aria-label="Edit {row.alias}"
							onclick={() => openEdit(row)}
						>
							{editing === row.id ? 'CLOSE' : 'EDIT'}
						</button>
						<button
							type="button"
							class="btn ghost danger"
							data-testid="delete-{automation.entity_id}"
							disabled={removing === row.id}
							aria-label="Delete {row.alias}"
							onclick={() => remove(row)}
						>
							{removing === row.id ? 'DELETING…' : confirming === row.id ? 'CONFIRM?' : 'DELETE'}
						</button>
					{:else}
						<!-- Said on the row, not hidden behind a disabled button: the
						     question "why can I edit that one and not this one" should
						     not need a hover to answer. -->
						<span class="pill" data-testid="yaml-{automation.entity_id}" title={readOnlyNote(row)}>
							FROM YAML
						</span>
					{/if}
				{/if}
			</div>

			{#if row && editing === row.id}
				<div class="editor" data-testid="editor-{row.id}">
					<p class="entity-id">{row.entity_id} · {row.id}</p>
					{@render editorFields()}
				</div>
			{/if}
		{:else}
			{#if status === 'open'}
				<EmptyState
					testid="empty"
					title="No automations"
					body={filter
						? `Nothing matches “${filter}”.`
						: 'This backend has no automations configured. Add one in jarvis-core and it will appear here, with its last trigger time.'}
				/>
			{:else}
				<EmptyState
					testid="empty"
					title="No link to the backend"
					body={`The websocket relay is ${status}.`}
				/>
			{/if}
		{/each}
	{/if}
</section>
