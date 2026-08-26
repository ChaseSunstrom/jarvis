<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import EntityRow from '$lib/components/EntityRow.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import { DiscardGuard } from '$lib/unsaved';
	import {
		Button,
		EmptyState,
		Input,
		Panel,
		Pill,
		ScreenState,
		SkeletonRows,
		Toggle,
		Toolbar
	} from '$lib/ui';
	import {
		applyStateChanged,
		type CompanionDevice,
		areaForEntity,
		areaKey,
		domainOf,
		friendlyName,
		type AreaEntry,
		type DeviceRegistryEntry,
		type EntityRegistryEntry,
		type EntityState,
		type Subscription
	} from '$lib/jarvisClient';
	import {
		areaOptions,
		describeChanges,
		entityChanges,
		formFor,
		isUnchanged,
		platformNote,
		whyNotEntityId,
		type EntityForm
	} from '$lib/entityAdmin';

	const UNASSIGNED = '__unassigned__';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let filter = $state('');
	let loading = $state(true);
	let states = $state<EntityState[]>([]);
	let areas = $state<AreaEntry[]>([]);
	let entries = $state<EntityRegistryEntry[]>([]);
	let devices = $state<DeviceRegistryEntry[]>([]);

	// Non-reactive mirror the event handler mutates, then republishes in one go.
	const stateMap = new Map<string, EntityState>();

	let entryMap = $derived(new Map(entries.map((e) => [e.entity_id, e])));
	let deviceMap = $derived(new Map(devices.map((d) => [d.id, d])));

	// The command palette jumps here with ?focus=<entity_id>; narrowing the
	// filter to it is both the highlight and a usable starting point.
	let focused = $derived(page.url.searchParams.get('focus') ?? '');
	$effect(() => {
		if (focused) filter = focused;
	});

	let groups = $derived.by(() => {
		const needle = filter.trim().toLowerCase();
		const buckets = new Map<string, { id: string; name: string; items: EntityState[] }>();
		for (const area of areas) {
			buckets.set(areaKey(area), { id: areaKey(area), name: area.name, items: [] });
		}
		buckets.set(UNASSIGNED, { id: UNASSIGNED, name: 'Unassigned', items: [] });

		for (const state of states) {
			const entry = entryMap.get(state.entity_id);
			if (entry?.disabled) continue;
			const label = friendlyName(state, entry).toLowerCase();
			if (needle && !label.includes(needle) && !state.entity_id.toLowerCase().includes(needle)) {
				continue;
			}
			const area = areaForEntity(state.entity_id, entryMap, deviceMap) ?? UNASSIGNED;
			const bucket = buckets.get(area) ?? buckets.get(UNASSIGNED)!;
			bucket.items.push(state);
		}
		for (const bucket of buckets.values()) {
			bucket.items.sort((a, b) => a.entity_id.localeCompare(b.entity_id));
		}
		return [...buckets.values()].filter((b) => b.items.length > 0);
	});

	let total = $derived(groups.reduce((n, g) => n + g.items.length, 0));

	function publish(): void {
		states = [...stateMap.values()];
	}

	function labelFor(entityId: string): string {
		return friendlyName(stateMap.get(entityId), entryMap.get(entityId));
	}

	async function call(entityId: string, service: string, data: Record<string, any> = {}) {
		if (!conn) return;
		err = '';
		const label = labelFor(entityId);
		try {
			await conn.client.callService(domainOf(entityId), service, { entity_id: entityId, ...data });
			toasts.success(serviceSuccessText(service, label), entityId);
		} catch (e) {
			// Both channels on purpose: the toast is what you notice, the inline
			// error is what is still on screen ten seconds later.
			err = describeError(e);
			toasts.error(serviceFailureText(service, label), describeError(e));
		}
	}

	// --- managing an entry ---------------------------------------------------
	// The console could read and control entities but never manage them, so the
	// one control that matters most — whether the assistant may see a thing —
	// could only be changed by hand-editing a file under `.storage/`.
	let editing = $state('');
	let form = $state<EntityForm>(formFor(undefined));
	let saving = $state(false);

	let options = $derived(areaOptions(areas));
	let pending = $derived(entityChanges(entryMap.get(editing), form));
	// Only once the box has been touched into something different, so opening
	// an editor does not greet you with a complaint about the id you have.
	const idProblem = $derived(
		editing && form.entityId.trim().toLowerCase() !== editing
			? whyNotEntityId(form.entityId, editing)
			: ''
	);
	let summary = $derived(describeChanges(pending));

	/**
	 * Unsaved edits, and the press that would have thrown them away.
	 *
	 * Opening another entity's editor — or closing this one — used to reassign
	 * `form` outright, so a half-typed name or a just-ticked exposure box left no
	 * trace. `pending` already knows whether anything would be sent; this makes
	 * the first press say so and the second one mean it.
	 */
	const discard = new DiscardGuard((target) =>
		toasts.info(
			`Unsaved changes to ${labelFor(editing)}`,
			target === editing ? 'Press CLOSE again to discard them.' : 'Press again to discard them.'
		)
	);

	function edit(entityId: string): void {
		if (!discard.allows(entityId, Boolean(editing) && !isUnchanged(pending))) return;
		if (editing === entityId) {
			editing = '';
			return;
		}
		editing = entityId;
		form = formFor(entryMap.get(entityId));
	}

	async function save(): Promise<void> {
		if (!conn || !editing || isUnchanged(pending)) return;
		const entityId = editing;
		const label = labelFor(entityId);
		saving = true;
		err = '';
		try {
			await conn.client.updateEntity(entityId, pending);
			// Re-read rather than patching the local copy: the backend normalises
			// what it stores (an empty name becomes null), and a form that shows
			// something the server did not keep is the start of a save loop that
			// never settles.
			entries = (await conn.client.listEntities()) ?? entries;
			form = formFor(entryMap.get(entityId));
			discard.reset();
			toasts.success(`Updated ${label}`, entityId);
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not update ${label}`, describeError(e));
		} finally {
			saving = false;
		}
	}

	async function load(connection: Connection): Promise<void> {
		const client = connection.client;
		const fresh = await client.getStates();
		stateMap.clear();
		for (const state of fresh) stateMap.set(state.entity_id, state);
		publish();

		// Registries are optional extras: without them everything lands in
		// "Unassigned" rather than the page failing.
		async function optional(fn: () => Promise<any>): Promise<any[]> {
			try {
				return (await fn()) ?? [];
			} catch (e) {
				hint = describeError(e);
				return [];
			}
		}
		areas = await optional(() => client.listAreas());
		entries = await optional(() => client.listEntities());
		devices = await optional(() => client.listDevices());
	}

	/**
	 * The machines running Jarvis clients, as opposed to the things in the house.
	 *
	 * They register over the same socket and advertise what they will let Jarvis
	 * do to them, and until now nothing showed them: you could grant your phone
	 * forty capabilities and have no way to confirm it had ever connected.
	 */
	let companions = $state<CompanionDevice[]>([]);
	let companionsSupported = $state(true);

	async function loadCompanions(connection: Connection): Promise<void> {
		try {
			companions = (await connection.client.listCompanions()) ?? [];
		} catch (e) {
			// An older jarvis-core has no such command; the rest of the page is
			// unaffected, so this hides rather than shouting.
			companions = [];
			companionsSupported = false;
			console.warn('companion list unavailable', e);
		}
	}

	// --- the socket, and getting it back ------------------------------------
	// Dial, load, subscribe: the three steps that make this page's rows true.
	// `connect()` is a function rather than the body of onMount so the RECONNECT
	// button can run all three again — see `$lib/ui` OfflineState for why a page’s
	// socket does not reattach on its own.
	let disposed = false;
	let subs: Subscription[] = [];
	let redialling = $state(false);
	// Which dial owns the status line. The socket being replaced reports its own
	// close asynchronously, and without this that late 'closed' lands after the
	// new one is already open — leaving the page saying it is down while it is up.
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mine = ++dial;
		for (const sub of subs) void sub.unsubscribe();
		subs = [];
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
			await load(connection);
			await loadCompanions(connection);
			subs.push(
				await connection.client.subscribeEvents((event) => {
					if (applyStateChanged(stateMap, event)) publish();
				}, 'state_changed')
			);
			// A phone that registers while this page is open must appear on
			// it. Loading the list once at mount meant somebody who opened
			// the console, then set up the app, saw an empty panel telling
			// them no device had registered — for as long as they left the
			// tab open. There is no state_changed for a companion; these are
			// the events jarvis-core fires when one arrives or goes away.
			for (const type of ['jarvis_device_registered', 'jarvis_device_disconnected']) {
				try {
					subs.push(
						await connection.client.subscribeEvents(() => {
							void loadCompanions(connection);
						}, type)
					);
				} catch {
					// An older jarvis-core does not fire them. The list is
					// still correct on load, which is what it was before.
				}
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
			for (const sub of subs) void sub.unsubscribe();
			subs = [];
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


<!-- `data-redialling` is not decoration: the RECONNECT banner disappears the
     moment the new socket starts dialling, which is three steps before the rows
     are live again — `connect()` still has to load the states, load the
     companions and re-subscribe. Between those two instants the page looks
     recovered and is not. Nothing else on the page says so, so this does, and
     the e2e suite waits on it before trusting a row. -->
<p class="lede" data-testid="devices-lede" data-redialling={redialling}>
	{total} entit{total === 1 ? 'y' : 'ies'} · live over websocket · link {status}
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

{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<div class="tools">
	<Toolbar>
		{#snippet children()}
			<label class="jv-sr-only" for="device-filter">Filter devices</label>
			<div class="filter">
				<Input bind:value={filter} placeholder="Filter by name or entity_id  ( / )" testid="filter" />
			</div>
			{#if filter}
				<Button testid="clear-filter" onclick={() => (filter = '')}>Clear</Button>
			{/if}
		{/snippet}
	</Toolbar>
</div>

<div class="panels">
	{#if companionsSupported}
		<Panel
			title="Companions"
			meta={companions.length ? `${companions.filter((d) => d.connected).length} of ${companions.length} online` : 'none registered'}
			testid="companions"
		>
			{#snippet children()}
				<!--
					Shown even when empty, on purpose. Hiding it made "my phone is
					connected and the console does not know about it" indistinguishable
					from "this console has no such feature" — and the honest answer,
					when a phone has not registered, is to say so and say where to look.
				-->
				{#if !companions.length}
					<p class="none" data-testid="companions-empty">
						No phone or desktop has registered yet. The app registers over its command
						channel as soon as it connects — check the server URL and token in its
						Settings, and that the connection there reads <b>READY</b>.
					</p>
				{/if}
				{#each companions as device (device.device_id)}
					<div class="line" data-testid="companion-{device.device_id}">
						<span class="who">
							<b>{device.name}</b>
							<span class="eid">
								{device.platform ?? 'unknown'}{device.app_version ? ` · v${device.app_version}` : ''}
							</span>
						</span>
						<span class="count" data-testid="companion-actions-{device.device_id}">
							{device.action_count ?? device.actions?.length ?? 0} action(s)
						</span>
						<Pill tone={device.connected ? 'live' : 'neutral'} testid="companion-state-{device.device_id}">
							{device.connected ? 'online' : 'offline'}
						</Pill>
					</div>
				{/each}
			{/snippet}
		</Panel>
	{/if}

	{#if loading && !states.length}
		<Panel title="Devices" meta="…">
			{#snippet children()}
				<div class="pad"><SkeletonRows rows={6} label="Loading devices" /></div>
			{/snippet}
		</Panel>
	{:else}
		{#each groups as group, gi (group.id)}
			<div class="group jv-stagger" style={staggerStyle(gi)}>
				<Panel title={group.name} meta={String(group.items.length)} testid="area-{group.id}">
					{#snippet children()}
						{#each group.items as state, i (state.entity_id)}
							<div class="row-wrap" class:open={editing === state.entity_id}>
								<EntityRow
									{state}
									index={i}
									name={friendlyName(state, entryMap.get(state.entity_id))}
									call={(service, data) => call(state.entity_id, service, data)}
								/>
								<!-- A disclosure, not a button: it opens the editor under
								     this row, and it is quiet so the row's own control
								     stays the thing to press. -->
								<button
									type="button"
									class="manage"
									data-testid="manage-{state.entity_id}"
									aria-expanded={editing === state.entity_id}
									aria-label="Manage {friendlyName(state, entryMap.get(state.entity_id))}"
									onclick={() => edit(state.entity_id)}
								>
									<span aria-hidden="true">{editing === state.entity_id ? '▾' : '▸'}</span>
									{editing === state.entity_id ? 'CLOSE' : 'MANAGE'}
								</button>
							</div>

							{#if editing === state.entity_id}
								<div class="editor" data-testid="editor-{state.entity_id}">
									{#if platformNote(entryMap.get(state.entity_id))}
										<p class="notice origin" data-testid="origin-{state.entity_id}">
											{platformNote(entryMap.get(state.entity_id))}
										</p>
									{/if}

									<div class="fields">
										<label class="field">
											<span class="label">Entity id</span>
											<input
												type="text"
												class="in mono"
												data-testid="id-{state.entity_id}"
												spellcheck="false"
												autocapitalize="off"
												autocomplete="off"
												bind:value={form.entityId}
											/>
											{#if idProblem}
												<span class="problem" data-testid="id-problem-{state.entity_id}">{idProblem}</span>
											{:else}
												<span class="hint">
													This is the key, not the label — automations that name it are updated to follow.
												</span>
											{/if}
										</label>

										<label class="field">
											<span class="label">Name</span>
											<input
												type="text"
												class="in"
												data-testid="name-{state.entity_id}"
												placeholder={entryMap.get(state.entity_id)?.original_name ?? state.entity_id}
												bind:value={form.name}
											/>
										</label>

										<label class="field">
											<span class="label">Area</span>
											<select class="in" data-testid="area-{state.entity_id}" bind:value={form.areaId}>
												{#each options as option (option.id)}
													<option value={option.id}>{option.name}</option>
												{/each}
											</select>
										</label>

										<label class="field">
											<span class="label">Aliases</span>
											<input
												type="text"
												class="in"
												data-testid="aliases-{state.entity_id}"
												placeholder="other names to call it, comma separated"
												bind:value={form.aliases}
											/>
										</label>
									</div>

									<div class="toggles">
										<Toggle bind:checked={form.exposed} label="Visible to the assistant" testid="exposed-{state.entity_id}" />
										<Toggle bind:checked={form.hidden} label="Hidden from dashboards" testid="hidden-{state.entity_id}" />
										<Toggle bind:checked={form.disabled} label="Disabled" testid="disabled-{state.entity_id}" />
									</div>

									<div class="actions">
										<Button variant="primary" testid="save-{state.entity_id}"
											disabled={saving || isUnchanged(pending)}
											onclick={save}>
											{saving ? 'Saving…' : 'Save'}
										</Button>
										<span class="summary" data-testid="summary-{state.entity_id}">
											{summary || 'No changes yet.'}
										</span>
									</div>
								</div>
							{/if}
						{/each}
					{/snippet}
				</Panel>
			</div>
		{:else}
			{#if status === 'open'}
				<EmptyState
					testid="empty"
					title="No entities matched"
					body={filter
						? `Nothing here is called “${filter}”. Clear the filter to see everything the backend exposes.`
						: 'The backend reported no entities. Add an integration in jarvis-core and they will appear here live.'}
				/>
			{:else}
				<EmptyState
					testid="empty"
					title="No link to the backend"
					body={`The websocket relay is ${status}. Check that jarvis-core is reachable and that JARVIS_URL / JARVIS_TOKEN are set where this server runs.`}
				/>
			{/if}
		{/each}
	{/if}
</div>

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.notice {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-sm);
		color: var(--jv-warn);
	}
	.tools {
		margin-bottom: var(--jv-space-4);
	}
	.filter {
		width: min(100%, calc(var(--jv-space-7) * 8));
	}
	.panels {
		display: grid;
		gap: var(--jv-space-4);
	}
	/* Rows on hairlines want the panel's full width: the panel body's own
	   padding is given up here and each row keeps its own. */
	.panels :global(.body) {
		padding: 0 var(--jv-space-4);
	}
	.pad {
		padding: var(--jv-space-3) 0;
	}
	.none {
		margin: 0;
		padding: var(--jv-space-3) 0;
		font-size: var(--jv-fs-sm);
		line-height: 1.6;
		color: var(--jv-text-dim);
	}
	.none b {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text);
	}
	.line {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.line:last-child {
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
	.eid {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
	.count {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		font-variant-numeric: tabular-nums;
	}
	/*
	 * A row with its MANAGE affordance bolted to the end.
	 *
	 * The wrapper owns the hairline and the row inside gives it up — otherwise
	 * the rule stops where the row ends and the disclosure hangs off it. When
	 * the editor is open the hairline goes: a rule between a row and the panel
	 * it just opened reads as a boundary between two unrelated things.
	 */
	.row-wrap {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.row-wrap:last-child {
		border-bottom: 0;
	}
	.row-wrap.open {
		border-bottom: 0;
	}
	.row-wrap :global(.row) {
		flex: 1 1 20rem;
		min-width: 0;
	}
	.manage {
		flex: 0 0 auto;
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-1);
		background: transparent;
		border: 0;
		padding: var(--jv-space-2) var(--jv-space-1);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		color: var(--jv-text-faint);
		cursor: pointer;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.manage:hover,
	.manage[aria-expanded='true'] {
		color: var(--jv-text);
	}
	/* The editor drops out of its row as an inset: below the panel, not a
	   panel of its own. */
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
	.origin {
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
	.in.mono {
		font-family: var(--jv-font-chrome);
		letter-spacing: var(--jv-track-tight);
	}
	.hint {
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.problem {
		font-size: var(--jv-fs-2xs);
		color: var(--jv-danger-text);
	}
	.toggles {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-4);
	}
	.actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
	}
	.summary {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	@media (max-width: 640px) {
		.panels :global(.body) {
			padding: 0 var(--jv-space-3);
		}
		.editor {
			margin: 0 calc(-1 * var(--jv-space-3)) var(--jv-space-2);
			padding: var(--jv-space-3);
		}
	}
</style>
