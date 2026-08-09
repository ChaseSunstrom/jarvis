<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import {
		friendlyName,
		type EntityRegistryEntry,
		type EntityState,
		type ToolDescription
	} from '$lib/jarvisClient';

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let busy = $state(false);

	let tools = $state<ToolDescription[]>([]);
	let source = $state<'tools' | 'services' | ''>('');
	let entries = $state<EntityRegistryEntry[]>([]);
	let states = $state<EntityState[]>([]);
	let toolFilter = $state('');
	let entityFilter = $state('');

	let selected = $state('');
	let args = $state('{}');
	let result = $state('');

	let stateMap = $derived(new Map(states.map((s) => [s.entity_id, s])));
	let visibleTools = $derived(
		tools.filter((t) => t.name.toLowerCase().includes(toolFilter.trim().toLowerCase()))
	);
	let visibleEntities = $derived(
		entries
			.filter((e) => {
				const needle = entityFilter.trim().toLowerCase();
				if (!needle) return true;
				return (
					e.entity_id.toLowerCase().includes(needle) ||
					friendlyName(stateMap.get(e.entity_id), e).toLowerCase().includes(needle)
				);
			})
			.sort((a, b) => a.entity_id.localeCompare(b.entity_id))
	);
	let exposedCount = $derived(entries.filter((e) => e.exposed !== false).length);
	let selectedTool = $derived(tools.find((t) => t.name === selected));

	async function toggleExposure(entry: EntityRegistryEntry): Promise<void> {
		if (!conn) return;
		err = '';
		const next = entry.exposed === false;
		try {
			await conn.client.updateEntity(entry.entity_id, { exposed: next });
			entries = entries.map((e) =>
				e.entity_id === entry.entity_id ? { ...e, exposed: next } : e
			);
		} catch (e) {
			err = describeError(e);
		}
	}

	async function testRun(): Promise<void> {
		if (!conn || !selected) return;
		busy = true;
		err = '';
		result = '';
		let parsed: Record<string, any>;
		try {
			parsed = args.trim() ? JSON.parse(args) : {};
		} catch (e) {
			busy = false;
			err = `arguments are not valid JSON: ${(e as Error).message}`;
			return;
		}
		try {
			const outcome = await conn.client.callTool(selected, parsed);
			result = JSON.stringify(outcome ?? null, null, 2);
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = false;
		}
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
				tools = await connection.client.listTools();
				// From the client's own record of which command answered — reading
				// tools[0].source mislabels an empty native list as a fallback.
				source = connection.client.supportsNativeTools ? 'tools' : 'services';
				if (source === 'services') {
					hint =
						'This backend has no jarvis/tools/list command, so the service catalogue is shown instead — the same calls the LLM tool layer makes.';
				}
				if (tools.length && !selected) selected = tools[0].name;
				try {
					entries = (await connection.client.listEntities()) ?? [];
					states = (await connection.client.getStates()) ?? [];
				} catch (e) {
					hint = describeError(e);
				}
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

<svelte:head><title>Jarvis · Tools</title></svelte:head>

<h1>TOOLS</h1>
<p class="lede">
	{tools.length} callable{tools.length === 1 ? '' : 's'} · {exposedCount} exposed entit{exposedCount ===
	1
		? 'y'
		: 'ies'} · link {status}
</p>

{#if err}<p class="err" data-testid="error">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<section class="panel">
	<div class="panel-head">
		<span>Test run</span>
		<span class="muted">{source === 'tools' ? 'jarvis/tools/call' : 'call_service'}</span>
	</div>
	<div class="row">
		<select data-testid="tool-select" bind:value={selected}>
			{#each visibleTools as tool (tool.name)}
				<option value={tool.name}>{tool.name}</option>
			{/each}
		</select>
		<input
			type="text"
			style="flex:1 1 16rem"
			placeholder={'{"entity_id": "light.lab_lights"}'}
			data-testid="tool-args"
			bind:value={args}
		/>
		<button class="btn" data-testid="tool-run" disabled={busy || !selected} onclick={testRun}>
			RUN
		</button>
	</div>
	{#if selectedTool?.description}
		<p class="muted">{selectedTool.description}</p>
	{/if}
	{#if result}
		<pre data-testid="tool-result">{result}</pre>
	{/if}
</section>

<section class="panel">
	<div class="panel-head">
		<span>Catalogue</span>
		<input type="text" placeholder="filter" data-testid="tool-filter" bind:value={toolFilter} />
	</div>
	{#each visibleTools as tool (tool.name)}
		<div class="row" data-testid="tool-{tool.name}">
			<span class="name">
				<b>{tool.name}</b>
				<span class="eid">{tool.description || 'no description'}</span>
			</span>
			<button class="btn ghost" onclick={() => (selected = tool.name)}>USE</button>
		</div>
	{:else}
		<p class="muted" data-testid="empty">
			{status === 'open' ? 'No tools matched.' : 'Connecting to the backend…'}
		</p>
	{/each}
</section>

<section class="panel">
	<div class="panel-head">
		<span>Entity exposure</span>
		<input type="text" placeholder="filter" data-testid="entity-filter" bind:value={entityFilter} />
	</div>
	<p class="muted">
		Unexposed entities stay out of the LLM's prompt and cannot be targeted by voice.
	</p>
	{#each visibleEntities as entry (entry.entity_id)}
		{@const exposed = entry.exposed !== false}
		<div class="row" data-testid="expose-row-{entry.entity_id}">
			<span class="name">
				<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
				<span class="eid">{entry.entity_id}</span>
			</span>
			<button
				class="btn"
				class:on={exposed}
				data-testid="expose-{entry.entity_id}"
				onclick={() => toggleExposure(entry)}
			>
				{exposed ? 'EXPOSED' : 'HIDDEN'}
			</button>
		</div>
	{:else}
		<p class="muted">No entity registry entries on this backend.</p>
	{/each}
</section>
