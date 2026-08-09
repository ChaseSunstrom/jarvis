<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import { staggerStyle } from '$lib/motion';
	import Skeleton from '$lib/components/Skeleton.svelte';
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
	let loading = $state(true);

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
			toasts.success(`${next ? 'Exposed' : 'Hidden from the LLM'} · ${entry.entity_id}`);
		} catch (e) {
			err = describeError(e);
			toasts.error(`Exposure change failed · ${entry.entity_id}`, describeError(e));
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
			toasts.error('Arguments are not valid JSON', (e as Error).message);
			return;
		}
		try {
			const outcome = await conn.client.callTool(selected, parsed);
			result = JSON.stringify(outcome ?? null, null, 2);
			toasts.success(`Ran ${selected}`);
		} catch (e) {
			err = describeError(e);
			toasts.error(`${selected} failed`, describeError(e));
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

<svelte:head><title>Jarvis · Tools</title></svelte:head>

<h1>TOOLS</h1>
<p class="lede">
	{tools.length} callable{tools.length === 1 ? '' : 's'} · {exposedCount} exposed entit{exposedCount ===
	1
		? 'y'
		: 'ies'} · link {status}
</p>

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<section class="panel">
	<div class="panel-head">
		<span>Test run</span>
		<span class="muted">{source === 'tools' ? 'jarvis/tools/call' : 'call_service'}</span>
	</div>
	<div class="row">
		<select data-testid="tool-select" aria-label="Tool to run" bind:value={selected}>
			{#each visibleTools as tool (tool.name)}
				<option value={tool.name}>{tool.name}</option>
			{/each}
		</select>
		<input
			type="text"
			style="flex:1 1 16rem"
			placeholder={'{"entity_id": "light.lab_lights"}'}
			aria-label="Tool arguments as JSON"
			data-testid="tool-args"
			bind:value={args}
		/>
		<button
			type="button"
			class="btn"
			data-testid="tool-run"
			disabled={busy || !selected}
			onclick={testRun}
		>
			{busy ? 'RUNNING…' : 'RUN'}
		</button>
	</div>
	{#if selectedTool?.description}
		<p class="muted">{selectedTool.description}</p>
	{/if}
	{#if result}
		<pre data-testid="tool-result" aria-live="polite">{result}</pre>
	{/if}
</section>

<section class="panel">
	<div class="panel-head">
		<span>Catalogue</span>
		<input
			type="text"
			placeholder="filter  ( / )"
			aria-label="Filter the tool catalogue"
			data-testid="tool-filter"
			data-jv-filter
			bind:value={toolFilter}
		/>
	</div>
	{#if loading && !tools.length}
		<Skeleton rows={5} label="Loading the tool catalogue" />
	{:else}
		{#each visibleTools as tool, i (tool.name)}
			<div class="row jv-stagger" style={staggerStyle(i)} data-testid="tool-{tool.name}">
				<span class="name">
					<b>{tool.name}</b>
					<span class="eid">{tool.description || 'no description'}</span>
				</span>
				<button
					type="button"
					class="btn ghost"
					aria-label="Load {tool.name} into the test runner"
					onclick={() => (selected = tool.name)}>USE</button
				>
			</div>
		{:else}
			<div class="jv-empty" data-testid="empty">
				<span class="jv-empty-mark" aria-hidden="true">[ ∅ ]</span>
				<p class="jv-empty-title">{status === 'open' ? 'No tools matched' : 'No link to the backend'}</p>
				<p class="jv-empty-body">
					{status === 'open'
						? 'Nothing in the catalogue matches that filter.'
						: `The websocket relay is ${status}.`}
				</p>
			</div>
		{/each}
	{/if}
</section>

<section class="panel">
	<div class="panel-head">
		<span>Entity exposure</span>
		<input
			type="text"
			placeholder="filter"
			aria-label="Filter entities"
			data-testid="entity-filter"
			bind:value={entityFilter}
		/>
	</div>
	<p class="muted">
		Unexposed entities stay out of the LLM's prompt and cannot be targeted by voice.
	</p>
	{#each visibleEntities as entry, i (entry.entity_id)}
		{@const exposed = entry.exposed !== false}
		<div class="row jv-stagger" style={staggerStyle(i)} data-testid="expose-row-{entry.entity_id}">
			<span class="name">
				<b>{friendlyName(stateMap.get(entry.entity_id), entry)}</b>
				<span class="eid">{entry.entity_id}</span>
			</span>
			<button
				type="button"
				class="btn"
				class:on={exposed}
				data-testid="expose-{entry.entity_id}"
				aria-pressed={exposed}
				aria-label="{exposed ? 'Hide' : 'Expose'} {entry.entity_id} to the assistant"
				onclick={() => toggleExposure(entry)}
			>
				{exposed ? 'EXPOSED' : 'HIDDEN'}
			</button>
		</div>
	{:else}
		<p class="muted">No entity registry entries on this backend.</p>
	{/each}
</section>
