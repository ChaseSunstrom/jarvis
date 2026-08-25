<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import EntityRow from '$lib/components/EntityRow.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import { DiscardGuard } from '$lib/unsaved';
	import { Button, EmptyState, ScreenState } from '$lib/ui';
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

<div class="toolbar">
	<label class="jv-sr-only" for="device-filter">Filter devices</label>
	<input
		id="device-filter"
		type="text"
		placeholder="filter by name or entity_id  ( / )"
		data-testid="filter"
		data-jv-filter
		bind:value={filter}
	/>
	{#if filter}
		<Button testid="clear-filter" onclick={() => (filter = '')}>
			CLEAR
		</Button>
	{/if}
</div>

{#if companionsSupported}
	<section class="panel" data-testid="companions">
		<div class="panel-head">
			<span>Companions</span>
			<span class="muted">phones, desktops and satellites running Jarvis</span>
		</div>
		<!--
			Shown even when empty, on purpose. Hiding it made "my phone is
			connected and the console does not know about it" indistinguishable
			from "this console has no such feature" — and the honest answer,
			when a phone has not registered, is to say so and say where to look.
		-->
		{#if !companions.length}
			<p class="empty" data-testid="companions-empty">
				No phone or desktop has registered yet. The app registers over its command
				channel as soon as it connects — check the server URL and token in its
				Settings, and that the connection there reads <b>READY</b>.
			</p>
		{/if}
		{#each companions as device (device.device_id)}
			<div class="row" data-testid="companion-{device.device_id}">
				<span class="name">
					<b>{device.name}</b>
					<span class="eid">
						{device.platform ?? 'unknown'}{device.app_version ? ` · v${device.app_version}` : ''}
					</span>
				</span>
				<span class="muted" data-testid="companion-actions-{device.device_id}">
					{device.action_count ?? device.actions?.length ?? 0} action(s)
				</span>
				<span
					class="pill"
					class:on={device.connected}
					data-testid="companion-state-{device.device_id}"
				>
					{device.connected ? 'online' : 'offline'}
				</span>
			</div>
		{/each}
	</section>
{/if}

{#if loading && !states.length}
	<section class="panel" aria-label="Loading devices">
		<div class="panel-head"><span>Devices</span><span class="muted">…</span></div>
		<Skeleton rows={6} label="Loading devices" />
	</section>
{:else}
	{#each groups as group, gi (group.id)}
		<section
			class="panel jv-stagger"
			style={staggerStyle(gi)}
			data-testid="area-{group.id}"
			aria-label={group.name}
		>
			<div class="panel-head">
				<span>{group.name}</span>
				<span class="muted">{group.items.length}</span>
			</div>
			{#each group.items as state, i (state.entity_id)}
				<div class="row-wrap">
					<EntityRow
						{state}
						index={i}
						name={friendlyName(state, entryMap.get(state.entity_id))}
						call={(service, data) => call(state.entity_id, service, data)}
					/>
					<button
						type="button"
						class="btn ghost manage"
						data-testid="manage-{state.entity_id}"
						aria-expanded={editing === state.entity_id}
						aria-label="Manage {friendlyName(state, entryMap.get(state.entity_id))}"
						onclick={() => edit(state.entity_id)}
					>
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

						<div class="field">
							<label for="id-{state.entity_id}">Entity id</label>
							<input
								id="id-{state.entity_id}"
								type="text"
								class="entity-id"
								data-testid="id-{state.entity_id}"
								spellcheck="false"
								autocapitalize="off"
								autocomplete="off"
								bind:value={form.entityId}
							/>
							{#if idProblem}
								<p class="err" data-testid="id-problem-{state.entity_id}">{idProblem}</p>
							{:else}
								<p class="hint">
									This is the key, not the label — automations that name it are
									updated to follow.
								</p>
							{/if}
						</div>

						<div class="field">
							<label for="name-{state.entity_id}">Name</label>
							<input
								id="name-{state.entity_id}"
								type="text"
								data-testid="name-{state.entity_id}"
								placeholder={entryMap.get(state.entity_id)?.original_name ?? state.entity_id}
								bind:value={form.name}
							/>
						</div>

						<div class="field">
							<label for="area-{state.entity_id}">Area</label>
							<select id="area-{state.entity_id}" data-testid="area-{state.entity_id}" bind:value={form.areaId}>
								{#each options as option (option.id)}
									<option value={option.id}>{option.name}</option>
								{/each}
							</select>
						</div>

						<div class="field">
							<label for="aliases-{state.entity_id}">Aliases</label>
							<input
								id="aliases-{state.entity_id}"
								type="text"
								data-testid="aliases-{state.entity_id}"
								placeholder="other names to call it, comma separated"
								bind:value={form.aliases}
							/>
						</div>

						<div class="toggles">
							<label>
								<input
									type="checkbox"
									data-testid="exposed-{state.entity_id}"
									bind:checked={form.exposed}
								/>
								Visible to the assistant
							</label>
							<label>
								<input type="checkbox" data-testid="hidden-{state.entity_id}" bind:checked={form.hidden} />
								Hidden from dashboards
							</label>
							<label>
								<input
									type="checkbox"
									data-testid="disabled-{state.entity_id}"
									bind:checked={form.disabled}
								/>
								Disabled
							</label>
						</div>

						<div class="actions">
							<Button variant="primary" testid="save-{state.entity_id}"
								disabled={saving || isUnchanged(pending)}
								onclick={save}>
								{saving ? 'SAVING…' : 'SAVE'}
							</Button>
							<span class="summary" data-testid="summary-{state.entity_id}">
								{summary || 'No changes yet.'}
							</span>
						</div>
					</div>
				{/if}
			{/each}
		</section>
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

<!-- `.toolbar` now lives in chrome.css: Devices, Automations and Tools all use
     it, and only this page had it styled. -->

