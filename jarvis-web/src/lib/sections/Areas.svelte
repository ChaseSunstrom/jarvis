<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import type { Subscription } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import { Button, EmptyState, Input, Panel, ScreenState, SkeletonRows } from '$lib/ui';
	import {
		areaForEntity,
		areaKey,
		friendlyName,
		type AreaEntry,
		type DeviceRegistryEntry,
		type EntityRegistryEntry,
		type EntityState
	} from '$lib/jarvisClient';

	const UNASSIGNED = '';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let busy = $state(false);
	let loading = $state(true);
	let newAreaName = $state('');
	let renaming = $state<Record<string, string>>({});
	/** The one area whose rename/delete line is open (M55). */
	let editingArea = $state('');

	let areas = $state<AreaEntry[]>([]);
	let entries = $state<EntityRegistryEntry[]>([]);
	let devices = $state<DeviceRegistryEntry[]>([]);
	let states = $state<EntityState[]>([]);

	let stateMap = $derived(new Map(states.map((s) => [s.entity_id, s])));
	let entryMap = $derived(new Map(entries.map((e) => [e.entity_id, e])));
	let deviceMap = $derived(new Map(devices.map((d) => [d.id, d])));

	let assignments = $derived.by(() => {
		const byArea = new Map<string, EntityRegistryEntry[]>();
		byArea.set(UNASSIGNED, []);
		for (const area of areas) byArea.set(areaKey(area), []);
		for (const entry of entries) {
			const area = areaForEntity(entry.entity_id, entryMap, deviceMap) ?? UNASSIGNED;
			(byArea.get(area) ?? byArea.get(UNASSIGNED)!).push(entry);
		}
		for (const list of byArea.values()) list.sort((a, b) => a.entity_id.localeCompare(b.entity_id));
		return byArea;
	});

	async function refresh(): Promise<void> {
		if (!conn) return;
		const client = conn.client;
		try {
			areas = (await client.listAreas()) ?? [];
		} catch (e) {
			err = describeError(e);
			return;
		}
		try {
			entries = (await client.listEntities()) ?? [];
			devices = (await client.listDevices()) ?? [];
			states = (await client.getStates()) ?? [];
		} catch (e) {
			hint = describeError(e);
		}
	}

	async function run(what: string, fn: () => Promise<any>): Promise<void> {
		if (!conn) return;
		busy = true;
		err = '';
		try {
			await fn();
			await refresh();
			toasts.success(what);
		} catch (e) {
			err = describeError(e);
			toasts.error(`${what} failed`, describeError(e));
		} finally {
			busy = false;
		}
	}

	function createArea(): void {
		const name = newAreaName.trim();
		if (!name) return;
		void run(`Created ${name}`, async () => {
			await conn!.client.createArea(name);
			newAreaName = '';
		});
	}

	function renameArea(area: AreaEntry): void {
		const id = areaKey(area);
		const name = (renaming[id] ?? '').trim();
		if (!name || name === area.name) return;
		void run(`Renamed to ${name}`, async () => {
			await conn!.client.updateArea(id, { name });
			delete renaming[id];
		});
	}

	function deleteArea(area: AreaEntry): void {
		void run(`Deleted ${area.name}`, () => conn!.client.deleteArea(areaKey(area)));
	}

	function assign(entityId: string, areaId: string): void {
		// jarvis-core skips null-valued registry fields, so '' is how an
		// assignment gets cleared.
		void run(areaId ? `Moved ${entityId}` : `Unassigned ${entityId}`, () =>
			conn!.client.updateEntity(entityId, { area_id: areaId })
		);
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
			// Live (M99): a room made by voice, an entity assigned on Devices, a
			// removal after a spoken yes — each fires one of these, and this
			// page used to show none of them until a reload.
			for (const type of ['area_registry_updated', 'entity_registry_updated', 'device_registry_updated']) {
				try {
					subs.push(
						await connection.client.subscribeEvents(() => {
							void refresh();
						}, type)
					);
				} catch {
					// An older server without the event still lists on demand.
				}
			}
			await refresh();
		} catch (e) {
			err = describeError(e);
		} finally {
			redialling = false;
			if (!disposed) loading = false;
		}
	}

	let subs: Subscription[] = [];

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


<p class="lede" data-testid="areas-screen">{areas.length} area(s) · link {status}</p>

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

<div class="panels">
	<!-- The one primary action on this screen: a new room. -->
	<form
		class="new"
		onsubmit={(e) => {
			e.preventDefault();
			createArea();
		}}
	>
		<div class="grow">
			<Input bind:value={newAreaName} placeholder="A new area — Living Room, Garage…" testid="new-area-name" />
		</div>
		<Button variant="primary" type="submit" testid="create-area"
			disabled={busy || !newAreaName.trim()}
			title={busy
				? 'Waiting for the backend to answer'
				: !newAreaName.trim()
					? 'Type a name for the area first'
					: 'Create this area'}>
			Create
		</Button>
	</form>

	{#if loading && !areas.length}
		<Panel title="Areas" meta="…">
			{#snippet children()}
				<div class="pad"><SkeletonRows rows={4} label="Loading areas" /></div>
			{/snippet}
		</Panel>
	{/if}

	{#each areas as area, ai (areaKey(area))}
		{@const id = areaKey(area)}
		<div class="jv-stagger" style={staggerStyle(ai)} data-jv-row data-testid="area-row-{id}">
			<Panel title={area.name} meta={id} testid="area-{id}">
				{#snippet children()}
					<!-- One control at rest (M55): the room's name and what is in it
					     are the row; renaming and deleting are one click in. -->
					<div class="line rename">
						<Button testid="edit-{id}"
							aria-expanded={editingArea === id}
							aria-label="Edit {area.name}"
							onclick={() => (editingArea = editingArea === id ? '' : id)}
						>
							{editingArea === id ? 'Close' : 'Edit'}
						</Button>
					</div>
					{#if editingArea === id}
						<div class="line rename" data-testid="area-editor-{id}">
							<div class="grow">
								<Input
									value={renaming[id] ?? area.name}
									testid="rename-{id}"
									oninput={(e) => (renaming[id] = (e.currentTarget as HTMLInputElement).value)}
								/>
							</div>
							<Button testid="save-{id}"
								disabled={busy}
								aria-label="Rename {area.name}"
								onclick={() => renameArea(area)}
							>
								Rename
							</Button>
							<Button variant="danger" testid="delete-{id}"
								disabled={busy}
								aria-label="Delete {area.name}"
								onclick={() => deleteArea(area)}
							>
								Delete
							</Button>
						</div>
					{/if}

					{#each assignments.get(id) ?? [] as entry (entry.entity_id)}
						<div class="line">
							<span class="who">
								<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
								<span class="eid">{entry.entity_id}</span>
							</span>
							<select
								class="sel"
								data-testid="assign-{entry.entity_id}"
								aria-label="Area for {entry.entity_id}"
								value={id}
								onchange={(e) => assign(entry.entity_id, (e.currentTarget as HTMLSelectElement).value)}
							>
								<option value="">— unassigned —</option>
								{#each areas as option (areaKey(option))}
									<option value={areaKey(option)}>{option.name}</option>
								{/each}
							</select>
						</div>
					{:else}
						<p class="none">No entities in this area.</p>
					{/each}
				{/snippet}
			</Panel>
		</div>
	{:else}
		{#if !loading}
			<EmptyState
				testid="empty"
				title={status === 'open' ? 'No areas yet' : 'No link to the backend'}
				body={status === 'open'
					? 'Areas are how voice commands like “turn off the kitchen” resolve. Create one above, then assign entities to it.'
					: `The websocket relay is ${status}.`}
			/>
		{/if}
	{/each}

	<!--
	  Everything with no area.

	  The list inside is guarded by `loading`, and that is the whole point of the
	  guard: this section sits OUTSIDE the loading branch above, so before the first
	  registry answer arrived it drew an empty list — and an empty list here reads
	  "Everything has an area", which is a claim about a house the page had not yet
	  been told anything about. It is also the exact opposite of the truth on a
	  fresh install, where nothing has an area at all.
	-->
	<Panel
		title="Unassigned"
		meta={loading ? '…' : String((assignments.get(UNASSIGNED) ?? []).length)}
		testid="area-unassigned"
	>
		{#snippet children()}
			{#if loading}
				<div class="pad"><SkeletonRows rows={3} label="Loading unassigned entities" /></div>
			{:else}
				{#each assignments.get(UNASSIGNED) ?? [] as entry (entry.entity_id)}
					<div class="line">
						<span class="who">
							<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
							<span class="eid">{entry.entity_id}</span>
						</span>
						<select
							class="sel"
							data-testid="assign-{entry.entity_id}"
							aria-label="Area for {entry.entity_id}"
							value=""
							onchange={(e) => assign(entry.entity_id, (e.currentTarget as HTMLSelectElement).value)}
						>
							<option value="">— unassigned —</option>
							{#each areas as option (areaKey(option))}
								<option value={areaKey(option)}>{option.name}</option>
							{/each}
						</select>
					</div>
				{:else}
					<p class="none" data-testid="all-assigned">Everything has an area.</p>
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
	.notice {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-sm);
		color: var(--jv-warn);
	}
	.panels {
		display: grid;
		gap: var(--jv-space-4);
	}
	.panels :global(.body) {
		padding: 0 var(--jv-space-4);
	}
	.new {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.grow {
		flex: 1 1 14rem;
		min-width: 0;
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
	.line:last-child {
		border-bottom: 0;
	}
	/* The rename row is the panel's head-of-body: a little more room, and a
	   name box the width of a name rather than of the screen. */
	.line.rename {
		padding-bottom: var(--jv-space-4);
	}
	.line.rename .grow {
		flex: 1 1 14rem;
		max-width: calc(var(--jv-space-7) * 8);
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
		overflow-wrap: anywhere;
	}
	.sel {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-1) var(--jv-space-2);
		max-width: 100%;
	}
	.sel:hover {
		border-color: var(--jv-line);
	}
	.none {
		margin: 0;
		padding: var(--jv-space-3) 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-faint);
	}
	@media (max-width: 640px) {
		.panels :global(.body) {
			padding: 0 var(--jv-space-3);
		}
	}
</style>
