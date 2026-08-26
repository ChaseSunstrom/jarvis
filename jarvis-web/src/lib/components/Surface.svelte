<!--
  The surface (M83): what Jarvis has put up on the voice screen, around the
  instrument. "Show me the front door" puts a panel here; a drag moves it; the
  arrangement lives on the server, so it is the same on every screen and after
  a reload. Each panel is drawn live from the house, never from a copy.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import SurfacePanel from './SurfacePanel.svelte';
	import type { CameraStill, MomentRow, ReadingsPayload, SkySummary } from '$lib/dashboards/widgets';
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import type { BusEvent, EntityState, Subscription, SurfacePanel as Panel } from '$lib/jarvisClient';
	import { toSurfacePanels } from '$lib/jarvisClient';

	let { conn }: { conn: Connection | null } = $props();

	let panels = $state<Panel[]>([]);
	let states = $state<Record<string, EntityState>>({});
	let stills = $state<Record<string, CameraStill>>({});
	let readings = $state<Record<string, ReadingsPayload>>({});
	let sky = $state<SkySummary | null>(null);
	let moments = $state<MomentRow[]>([]);
	let errors = $state<Record<string, string>>({});
	let now = $state(Date.now());
	let width = $state(0);
	let host = $state<HTMLElement | null>(null);
	let subs: Subscription[] = [];
	let disposed = false;

	// A row is a twelfth of the width too, so a 4×2 panel keeps its shape
	// whatever the screen: the grid is square cells over the stage.
	const row = $derived(width / 12);

	async function loadFor(list: Panel[], connection: Connection) {
		const client = connection.client;
		const jobs: Promise<unknown>[] = [];
		const kinds = new Set(list.map((p) => p.kind));
		if (kinds.has('entity') || kinds.has('chart')) {
			jobs.push(
				client
					.getStates()
					.then((all) => {
						const next: Record<string, EntityState> = {};
						for (const s of all) next[s.entity_id] = s;
						states = next;
					})
					.catch((e) => {
						for (const p of list) if (p.kind === 'entity') errors[p.id] = describeError(e);
					})
			);
		}
		for (const p of list) {
			if (p.kind === 'camera') {
				jobs.push(
					client
						.visionStill(p.camera)
						.then((still) => {
							stills[p.id] = still;
						})
						.catch((e) => {
							errors[p.id] = describeError(e);
						})
				);
			} else if (p.kind === 'readings') {
				jobs.push(
					client
						.sensorReadings(p.area)
						.then((payload) => {
							readings[p.id] = payload;
						})
						.catch((e) => {
							errors[p.id] = describeError(e);
						})
				);
			}
		}
		if (kinds.has('sky')) {
			jobs.push(client.skySummary().then((s) => (sky = s)).catch((e) => {
				for (const p of list) if (p.kind === 'sky') errors[p.id] = describeError(e);
			}));
		}
		if (kinds.has('moments')) {
			const wanted = Math.max(6, ...list.filter((p) => p.kind === 'moments').map((p) => p.limit));
			jobs.push(client.listMoments(wanted).then((rows) => (moments = rows)).catch((e) => {
				for (const p of list) if (p.kind === 'moments') errors[p.id] = describeError(e);
			}));
		}
		await Promise.all(jobs);
	}

	async function refresh(connection: Connection) {
		try {
			panels = await connection.client.surfaceList();
			errors = {};
			await loadFor(panels, connection);
		} catch (e) {
			console.warn('surface', e);
		}
	}

	async function attach(connection: Connection) {
		for (const s of subs) void s.unsubscribe();
		subs = [];
		try {
			subs.push(
				await connection.client.subscribeEvents((event: BusEvent) => {
					const list = toSurfacePanels((event.data as { panels?: unknown[] })?.panels);
					panels = list;
					void loadFor(list, connection);
				}, 'jarvis_surface_changed')
			);
			subs.push(
				await connection.client.subscribeEvents((event: BusEvent) => {
					const data = event.data as { entity_id?: string; new_state?: EntityState | null };
					if (!data?.entity_id) return;
					if (data.new_state) states[data.entity_id] = data.new_state;
					else delete states[data.entity_id];
				}, 'state_changed')
			);
		} catch (e) {
			console.warn('surface subscribe', e);
		}
		await refresh(connection);
	}

	$effect(() => {
		const connection = conn;
		if (!connection || disposed) return;
		void attach(connection);
	});

	onMount(() => {
		disposed = false;
		const tick = setInterval(() => (now = Date.now()), 1000);
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) width = entry.contentRect.width;
		});
		if (host) observer.observe(host);
		return () => {
			disposed = true;
			clearInterval(tick);
			observer.disconnect();
			for (const s of subs) void s.unsubscribe();
			subs = [];
		};
	});

	function onmove(id: string, where: { x: number; y: number; w?: number; h?: number }) {
		const panel = panels.find((p) => p.id === id);
		if (panel) Object.assign(panel, where);
		void conn?.client.surfaceMove(id, where).catch((e) => console.warn('surface move', e));
	}
	function onremove(id: string) {
		panels = panels.filter((p) => p.id !== id);
		void conn?.client.surfaceRemove(id).catch((e) => console.warn('surface remove', e));
	}
	function onswitch(entityId: string, service: string) {
		// The tile names the service — turn_on, lock, open_cover — and the
		// entity's domain is the service's; the same call the dashboards make.
		const domain = entityId.split('.')[0];
		void conn?.client.callService(domain, service, { entity_id: entityId }).catch((e) => console.warn('surface switch', e));
	}
</script>

<div class="surface" bind:this={host} data-testid="surface" data-count={panels.length} aria-label="What Jarvis has put on the screen">
	{#if width > 0}
		{#each panels as panel, index (panel.id)}
			<SurfacePanel
				{panel}
				{width}
				{row}
				{index}
				{now}
				entityState={states[panel.entity] ?? null}
				still={stills[panel.id] ?? null}
				readings={readings[panel.id] ?? null}
				{sky}
				{moments}
				error={errors[panel.id] ?? ''}
				{onmove}
				{onremove}
				{onswitch}
			/>
		{/each}
	{/if}
</div>

<style>
	.surface {
		position: absolute;
		inset: 0;
		pointer-events: none;
		z-index: 2;
	}
	.surface > :global(*) {
		pointer-events: auto;
	}
</style>
