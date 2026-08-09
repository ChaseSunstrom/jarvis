<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import {
		applyStateChanged,
		friendlyName,
		type EntityRegistryEntry,
		type EntityState,
		type Subscription
	} from '$lib/jarvisClient';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let flash = $state('');
	let loading = $state(true);
	let filter = $state('');
	let states = $state<EntityState[]>([]);
	let entries = $state<EntityRegistryEntry[]>([]);

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
				const fresh = await connection.client.getStates();
				stateMap.clear();
				for (const state of fresh) stateMap.set(state.entity_id, state);
				publish();
				try {
					entries = (await connection.client.listEntities()) ?? [];
				} catch {
					entries = [];
				}
				sub = await connection.client.subscribeEvents((event) => {
					if (applyStateChanged(stateMap, event)) publish();
				}, 'state_changed');
			} catch (e) {
				err = describeError(e);
			} finally {
				if (!disposed) loading = false;
			}
		})();
		return () => {
			disposed = true;
			clearTimeout(flashTimer);
			void sub?.unsubscribe();
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · Automations</title></svelte:head>

<h1>AUTOMATIONS</h1>
<p class="lede">{automations.length} automation(s) · link {status}</p>

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if flash}<p class="notice" data-testid="flash">{flash}</p>{/if}

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
			</div>
		{:else}
			<div class="jv-empty" data-testid="empty">
				<span class="jv-empty-mark" aria-hidden="true">[ ∅ ]</span>
				{#if status === 'open'}
					<p class="jv-empty-title">No automations</p>
					<p class="jv-empty-body">
						{filter
							? `Nothing matches “${filter}”.`
							: 'This backend has no automations configured. Add one in jarvis-core and it will appear here, with its last trigger time.'}
					</p>
				{:else}
					<p class="jv-empty-title">No link to the backend</p>
					<p class="jv-empty-body">The websocket relay is {status}.</p>
				{/if}
			</div>
		{/each}
	{/if}
</section>
