<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
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
	let states = $state<EntityState[]>([]);
	let entries = $state<EntityRegistryEntry[]>([]);

	const stateMap = new Map<string, EntityState>();
	let flashTimer: ReturnType<typeof setTimeout> | undefined;
	let entryMap = $derived(new Map(entries.map((e) => [e.entity_id, e])));

	let automations = $derived(
		states
			.filter((s) => s.entity_id.startsWith('automation.'))
			.sort((a, b) => a.entity_id.localeCompare(b.entity_id))
	);

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
		try {
			await conn.client.callService('automation', service, { entity_id: entityId });
			flash = `${note} ${entityId}`;
			// One timer, restarted: back-to-back clicks used to stack timers, and
			// the oldest would blank a flash the newest had just set.
			clearTimeout(flashTimer);
			flashTimer = setTimeout(() => (flash = ''), 2500);
		} catch (e) {
			err = describeError(e);
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

{#if err}<p class="err" data-testid="error">{err}</p>{/if}
{#if flash}<p class="notice" data-testid="flash">{flash}</p>{/if}

<section class="panel">
	<div class="panel-head">
		<span>Registered</span>
		<span class="muted">last triggered</span>
	</div>
	{#each automations as automation (automation.entity_id)}
		{@const on = automation.state === 'on'}
		<div class="row" data-testid="automation-{automation.entity_id}">
			<span class="name">
				<b>{friendlyName(automation, entryMap.get(automation.entity_id))}</b>
				<span class="eid">{automation.entity_id}</span>
			</span>
			<span class="muted" data-testid="last-{automation.entity_id}">
				{fmtTime(automation.attributes?.last_triggered)}
			</span>
			<span class="pill" class:on data-testid="state-{automation.entity_id}">{automation.state}</span>
			<button
				class="btn"
				class:on
				data-testid="toggle-{automation.entity_id}"
				onclick={() =>
					act(automation.entity_id, on ? 'turn_off' : 'turn_on', on ? 'disabled' : 'enabled')}
			>
				{on ? 'DISABLE' : 'ENABLE'}
			</button>
			<button
				class="btn ghost"
				data-testid="trigger-{automation.entity_id}"
				onclick={() => act(automation.entity_id, 'trigger', 'triggered')}
			>
				RUN NOW
			</button>
		</div>
	{:else}
		<p class="muted" data-testid="empty">
			{status === 'open'
				? 'No automations are configured on this backend.'
				: 'Connecting to the backend…'}
		</p>
	{/each}
</section>
