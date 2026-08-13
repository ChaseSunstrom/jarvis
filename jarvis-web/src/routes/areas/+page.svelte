<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import Skeleton from '$lib/components/Skeleton.svelte';
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

<svelte:head><title>Jarvis · Areas</title></svelte:head>

<h1>AREAS</h1>
<p class="lede">{areas.length} area(s) · link {status}</p>

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
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

{#if loading && !areas.length}
	<section class="panel" aria-label="Loading areas">
		<div class="panel-head"><span>Areas</span><span class="muted">…</span></div>
		<Skeleton rows={4} label="Loading areas" />
	</section>
{/if}

{#each areas as area, ai (areaKey(area))}
	{@const id = areaKey(area)}
	<section class="panel jv-stagger" style={staggerStyle(ai)} data-testid="area-{id}">
		<div class="panel-head">
			<span>{area.name}</span>
			<span class="muted">{id}</span>
		</div>
		<div class="row">
			<input
				type="text"
				value={renaming[id] ?? area.name}
				aria-label="New name for {area.name}"
				data-testid="rename-{id}"
				oninput={(e) => (renaming[id] = (e.currentTarget as HTMLInputElement).value)}
			/>
			<button
				type="button"
				class="btn ghost"
				data-testid="save-{id}"
				disabled={busy}
				aria-label="Rename {area.name}"
				onclick={() => renameArea(area)}
			>
				RENAME
			</button>
			<button
				type="button"
				class="btn danger"
				data-testid="delete-{id}"
				disabled={busy}
				aria-label="Delete {area.name}"
				onclick={() => deleteArea(area)}
			>
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
			<p class="muted">No entities in this area.</p>
		{/each}
	</section>
{:else}
	{#if !loading}
		<div class="jv-empty" data-testid="empty">
			<span class="jv-empty-mark" aria-hidden="true">[ ∅ ]</span>
			<p class="jv-empty-title">{status === 'open' ? 'No areas yet' : 'No link to the backend'}</p>
			<p class="jv-empty-body">
				{status === 'open'
					? 'Areas are how voice commands like “turn off the kitchen” resolve. Create one above, then assign entities to it.'
					: `The websocket relay is ${status}.`}
			</p>
		</div>
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
<section class="panel" data-testid="area-unassigned">
	<div class="panel-head">
		<span>Unassigned</span>
		<span class="muted">{loading ? '…' : (assignments.get(UNASSIGNED) ?? []).length}</span>
	</div>
	{#if loading}
		<Skeleton rows={3} label="Loading unassigned entities" />
	{:else}
		{#each assignments.get(UNASSIGNED) ?? [] as entry (entry.entity_id)}
			<div class="row">
				<span class="name">
					<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
					<span class="eid">{entry.entity_id}</span>
				</span>
				<select
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
			<p class="muted" data-testid="all-assigned">Everything has an area.</p>
		{/each}
	{/if}
</section>
