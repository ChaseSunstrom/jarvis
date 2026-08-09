<script lang="ts">
	import { onMount } from 'svelte';
	import EntityRow from '$lib/components/EntityRow.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import {
		applyStateChanged,
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

	const UNASSIGNED = '__unassigned__';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let filter = $state('');
	let states = $state<EntityState[]>([]);
	let areas = $state<AreaEntry[]>([]);
	let entries = $state<EntityRegistryEntry[]>([]);
	let devices = $state<DeviceRegistryEntry[]>([]);

	// Non-reactive mirror the event handler mutates, then republishes in one go.
	const stateMap = new Map<string, EntityState>();

	let entryMap = $derived(new Map(entries.map((e) => [e.entity_id, e])));
	let deviceMap = $derived(new Map(devices.map((d) => [d.id, d])));

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

	async function call(entityId: string, service: string, data: Record<string, any> = {}) {
		if (!conn) return;
		err = '';
		try {
			await conn.client.callService(domainOf(entityId), service, { entity_id: entityId, ...data });
		} catch (e) {
			err = describeError(e);
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

	onMount(() => {
		let disposed = false;
		let sub: Subscription | null = null;
		(async () => {
			try {
				const connection = await openConnection({ onStatus: (s) => (status = s) });
				if (disposed) {
					connection.close();
					return;
				}
				conn = connection;
				await load(connection);
				sub = await connection.client.subscribeEvents((event) => {
					if (applyStateChanged(stateMap, event)) publish();
				}, 'state_changed');
			} catch (e) {
				err = describeError(e);
			}
		})();
		return () => {
			disposed = true;
			void sub?.unsubscribe();
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · Devices</title></svelte:head>

<h1>DEVICES</h1>
<p class="lede">
	{total} entit{total === 1 ? 'y' : 'ies'} · live over websocket · link {status}
</p>

{#if err}<p class="err" data-testid="error">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<div class="toolbar">
	<input
		type="text"
		placeholder="filter by name or entity_id"
		data-testid="filter"
		bind:value={filter}
	/>
</div>

{#each groups as group (group.id)}
	<section class="panel" data-testid="area-{group.id}">
		<div class="panel-head">
			<span>{group.name}</span>
			<span class="muted">{group.items.length}</span>
		</div>
		{#each group.items as state (state.entity_id)}
			<EntityRow
				{state}
				name={friendlyName(state, entryMap.get(state.entity_id))}
				call={(service, data) => call(state.entity_id, service, data)}
			/>
		{/each}
	</section>
{:else}
	<p class="muted" data-testid="empty">
		{status === 'open' ? 'No entities matched.' : 'Connecting to the backend…'}
	</p>
{/each}

<style>
	.toolbar {
		display: flex;
		gap: 0.6rem;
		margin-bottom: 0.9rem;
	}
	.toolbar input {
		flex: 1 1 18rem;
		max-width: 26rem;
	}
</style>
