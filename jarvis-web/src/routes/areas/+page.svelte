<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
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
	let newAreaName = $state('');
	let renaming = $state<Record<string, string>>({});

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

	async function run(fn: () => Promise<any>): Promise<void> {
		if (!conn) return;
		busy = true;
		err = '';
		try {
			await fn();
			await refresh();
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = false;
		}
	}

	function createArea(): void {
		const name = newAreaName.trim();
		if (!name) return;
		void run(async () => {
			await conn!.client.createArea(name);
			newAreaName = '';
		});
	}

	function renameArea(area: AreaEntry): void {
		const id = areaKey(area);
		const name = (renaming[id] ?? '').trim();
		if (!name || name === area.name) return;
		void run(async () => {
			await conn!.client.updateArea(id, { name });
			delete renaming[id];
		});
	}

	function deleteArea(area: AreaEntry): void {
		void run(() => conn!.client.deleteArea(areaKey(area)));
	}

	function assign(entityId: string, areaId: string): void {
		// jarvis-core skips null-valued registry fields, so '' is how an
		// assignment gets cleared.
		void run(() => conn!.client.updateEntity(entityId, { area_id: areaId }));
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
				await refresh();
			} catch (e) {
				err = describeError(e);
			}
		})();
		return () => {
			disposed = true;
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · Areas</title></svelte:head>

<h1>AREAS</h1>
<p class="lede">{areas.length} area(s) · link {status}</p>

{#if err}<p class="err" data-testid="error">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<section class="panel">
	<div class="panel-head"><span>New area</span></div>
	<div class="row">
		<input
			type="text"
			placeholder="Living Room"
			data-testid="new-area-name"
			bind:value={newAreaName}
			onkeydown={(e) => e.key === 'Enter' && createArea()}
		/>
		<button class="btn" data-testid="create-area" disabled={busy || !newAreaName.trim()} onclick={createArea}>
			CREATE
		</button>
	</div>
</section>

{#each areas as area (areaKey(area))}
	{@const id = areaKey(area)}
	<section class="panel" data-testid="area-{id}">
		<div class="panel-head">
			<span>{area.name}</span>
			<span class="muted">{id}</span>
		</div>
		<div class="row">
			<input
				type="text"
				value={renaming[id] ?? area.name}
				data-testid="rename-{id}"
				oninput={(e) => (renaming[id] = (e.currentTarget as HTMLInputElement).value)}
			/>
			<button class="btn ghost" data-testid="save-{id}" disabled={busy} onclick={() => renameArea(area)}>
				RENAME
			</button>
			<button class="btn danger" data-testid="delete-{id}" disabled={busy} onclick={() => deleteArea(area)}>
				DELETE
			</button>
		</div>

		{#each assignments.get(id) ?? [] as entry (entry.entity_id)}
			<div class="row">
				<span class="name">
					<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
					<span class="eid">{entry.entity_id}</span>
				</span>
				<select
					data-testid="assign-{entry.entity_id}"
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
			<p class="muted">No entities in this area.</p>
		{/each}
	</section>
{:else}
	<p class="muted" data-testid="empty">
		{status === 'open' ? 'No areas yet — create one above.' : 'Connecting to the backend…'}
	</p>
{/each}

<section class="panel" data-testid="area-unassigned">
	<div class="panel-head">
		<span>Unassigned</span>
		<span class="muted">{(assignments.get(UNASSIGNED) ?? []).length}</span>
	</div>
	{#each assignments.get(UNASSIGNED) ?? [] as entry (entry.entity_id)}
		<div class="row">
			<span class="name">
				<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
				<span class="eid">{entry.entity_id}</span>
			</span>
			<select
				data-testid="assign-{entry.entity_id}"
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
		<p class="muted">Everything has an area.</p>
	{/each}
</section>
