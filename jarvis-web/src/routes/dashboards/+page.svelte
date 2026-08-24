<script lang="ts">
	/**
	 * Graphs somebody arranged.
	 *
	 * A dashboard belongs to the token that saved it — there are no user
	 * accounts here, and a token is one device — so what this page shows is
	 * "yours, plus the shared ones", and EDIT LAYOUT only appears on one you own.
	 *
	 * Arranging is done with controls rather than only with a mouse: move,
	 * widen, grow and remove are buttons, so the layout can be changed from a
	 * keyboard and asserted by a test. Dragging one card onto another swaps
	 * them, which is what "reorder" means on a grid — nothing is left in a gap
	 * and nothing silently overlaps.
	 */
	import { onDestroy, onMount } from 'svelte';
	import { openConnection, type Connection, type ConnectionStatus } from '$lib/connection';
	import type { MetricSource } from '$lib/jarvisClient';
	import { CHART_TYPES, CHART_TYPE_NAMES, RANGES, type ChartType, type Range } from '$lib/dashboards/chartTypes';
	import {
		COLUMNS,
		addWidget,
		moveWidget,
		newWidgetId,
		removeWidget,
		resizeWidget,
		sortWidgets,
		swapWidgets,
		type Dashboard,
		type Widget
	} from '$lib/dashboards/layout';
	import type { SeriesData } from '$lib/dashboards/series';
	import Chart from '$lib/dashboards/Chart.svelte';
	import { Button, Field, IconButton, Input, Panel, Pill, ScreenState, Select, Toolbar } from '$lib/ui';

	let conn = $state<Connection | null>(null);
	let status = $state<ConnectionStatus>('connecting');
	let redialling = $state(false);
	let err = $state('');
	let loading = $state(true);
	let boards = $state<Dashboard[]>([]);
	let currentId = $state('');
	let sources = $state<MetricSource[]>([]);
	let data = $state<Record<string, SeriesData[]>>({});
	let editing = $state(false);
	let saying = $state('');
	let dragging = $state('');

	// The widget editor's draft.
	let newType = $state<ChartType>('line');
	let newSource = $state('internal');
	let newSeries = $state('');
	let newTitle = $state('');

	const current = $derived(boards.find((board) => board.id === currentId) ?? null);
	const mine = $derived(!!current && !current.shipped);

	let screen = $derived<'ready' | 'error' | 'offline' | 'loading' | 'empty'>(
		status === 'closed' || status === 'error'
			? 'offline'
			: err
				? 'error'
				: loading
					? 'loading'
					: boards.length
						? 'ready'
						: 'empty'
	);

	async function connect() {
		redialling = true;
		try {
			conn?.close();
			const link = await openConnection({ onStatus: (s) => (status = s) });
			conn = link;
			boards = await link.client.listDashboards();
			sources = await link.client.metricsSources();
			if (!boards.some((board) => board.id === currentId)) currentId = boards[0]?.id ?? '';
			await refresh();
			err = '';
		} catch (error) {
			err = error instanceof Error ? error.message : String(error);
		} finally {
			loading = false;
			redialling = false;
		}
	}

	/** Fetch every widget's numbers for the window the dashboard is showing. */
	async function refresh() {
		const board = current;
		if (!conn || !board) return;
		const next: Record<string, SeriesData[]> = {};
		for (const widget of board.widgets) {
			try {
				next[widget.id] = await conn.client.metricsQuery({
					source: widget.source,
					series: widget.series,
					range: board.range,
					aggregate: widget.aggregate || undefined
				});
			} catch (error) {
				// One widget's source being down is one widget's problem: the other
				// five still draw.
				next[widget.id] = widget.series.map((key) => ({
					key,
					label: key,
					unit: '',
					aggregate: '',
					error: error instanceof Error ? error.message : String(error),
					points: []
				}));
			}
		}
		data = next;
	}

	async function persist(next: Dashboard) {
		boards = boards.map((board) => (board.id === next.id ? next : board));
		if (!conn || !mine) return;
		try {
			await conn.client.saveDashboard(next);
			saying = 'Saved.';
		} catch (error) {
			saying = error instanceof Error ? error.message : String(error);
		}
	}

	const withWidgets = (widgets: Widget[]): Dashboard => ({ ...current!, widgets });

	async function setRange(range: Range) {
		if (!current) return;
		await persist({ ...current, range });
		await refresh();
	}

	async function addNew() {
		if (!current) return;
		const series = newSeries
			.split(',')
			.map((key) => key.trim())
			.filter(Boolean);
		if (!series.length) {
			saying = 'Pick at least one series.';
			return;
		}
		const widget = {
			id: newWidgetId(current.widgets),
			title: newTitle,
			type: newType,
			source: newSource,
			series,
			aggregate: '' as const,
			w: newType === 'stat' || newType === 'gauge' ? 3 : 6,
			h: 2
		};
		await persist(withWidgets(addWidget(current.widgets, widget)));
		newSeries = '';
		newTitle = '';
		await refresh();
	}

	async function drop(targetId: string) {
		if (!current || !dragging || dragging === targetId) return;
		await persist(withWidgets(swapWidgets(current.widgets, dragging, targetId)));
		dragging = '';
	}

	onMount(connect);
	onDestroy(() => conn?.close());

	const seriesFor = (source: string) => sources.find((one) => one.name === source)?.series ?? [];
</script>

<svelte:head><title>Jarvis · Dashboards</title></svelte:head>

<h1 data-testid="dashboards-screen">DASHBOARDS</h1>
<p class="lede">
	{boards.length} dashboard{boards.length === 1 ? '' : 's'} · {sources.length} source{sources.length ===
	1
		? ''
		: 's'} · link {status}
</p>

<ScreenState
	status={screen}
	rows={3}
	errorTitle="Could not load your dashboards"
	errorDetail={err}
	emptyTitle="No dashboards yet"
	emptyBody="Jarvis ships one worked example. If you cannot see it, this backend has no dashboards integration configured."
	onretry={connect}
	onreconnect={connect}
	busy={redialling}
	errorTestid="error"
	emptyTestid="dashboards-empty"
>
	{#snippet children()}
		<Toolbar>
			{#snippet children()}
				<Select
					bind:value={currentId}
					options={boards.map((board) => ({ value: board.id, label: board.title }))}
					testid="dashboard-picker"
					onchange={refresh}
				/>
				{#if current?.shipped}<Pill>shipped · read only</Pill>{/if}
			{/snippet}
			{#snippet end()}
				{#each RANGES as range (range)}
					<Button
						onclick={() => setRange(range)}
						testid="range-{range}"
						variant={current?.range === range ? 'primary' : 'ghost'}>{range}</Button
					>
				{/each}
				<Button onclick={refresh} testid="dashboard-refresh">Refresh</Button>
				{#if mine}
					<Button
						onclick={() => (editing = !editing)}
						testid="dashboard-edit"
						variant={editing ? 'primary' : 'ghost'}>{editing ? 'Done' : 'Edit layout'}</Button
					>
				{/if}
			{/snippet}
		</Toolbar>

		{#if saying}<p class="said" role="status" data-testid="dashboard-said">{saying}</p>{/if}

		{#if editing && current}
			<Panel title="Add a widget" meta="{current.widgets.length} on this dashboard">
				{#snippet children()}
					<div class="editor">
						<Field label="Chart">
							<Select
								bind:value={newType}
								testid="new-type"
								options={CHART_TYPE_NAMES.map((name) => ({
									value: name,
									label: `${CHART_TYPES[name].label} — ${CHART_TYPES[name].when}`
								}))}
							/>
						</Field>
						<Field label="Source">
							<Select
								bind:value={newSource}
								testid="new-source"
								options={sources.map((one) => ({
									value: one.name,
									label: one.healthy ? one.name : `${one.name} — ${one.detail}`
								}))}
							/>
						</Field>
						<Field
							label="Series"
							hint={seriesFor(newSource)
								.slice(0, 4)
								.map((one) => one.key)
								.join(', ')}
						>
							<Input bind:value={newSeries} placeholder="host.load1, host.load5" mono testid="new-series" />
						</Field>
						<Field label="Title">
							<Input bind:value={newTitle} placeholder="optional" testid="new-title" />
						</Field>
						<Button variant="primary" onclick={addNew} testid="new-widget">Add widget</Button>
					</div>
				{/snippet}
			</Panel>
		{/if}

		{#if current}
			<div class="grid" data-testid="dashboard-grid" style="--columns:{COLUMNS}">
				{#each sortWidgets(current.widgets) as widget (widget.id)}
					<section
						class="widget"
						style="grid-column: span {widget.w}; grid-row: span {widget.h};"
						data-testid="widget-{widget.id}"
						data-type={widget.type}
						data-w={widget.w}
						data-x={widget.x}
						draggable={editing}
						ondragstart={() => (dragging = widget.id)}
						ondragover={(event) => editing && event.preventDefault()}
						ondrop={() => drop(widget.id)}
					>
						<header>
							<span class="title">{widget.title || widget.series[0]}</span>
							<span class="src">{widget.source}</span>
						</header>
						<Chart type={widget.type} series={data[widget.id] ?? []} />
						{#if editing}
							<div class="handles">
								<IconButton
									label="Move {widget.title || widget.id} left"
									glyph="←"
									testid="left-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x - 1, widget.y)))}
								/>
								<IconButton
									label="Move {widget.title || widget.id} right"
									glyph="→"
									testid="right-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x + 1, widget.y)))}
								/>
								<IconButton
									label="Move {widget.title || widget.id} up"
									glyph="↑"
									testid="up-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x, widget.y - 1)))}
								/>
								<IconButton
									label="Move {widget.title || widget.id} down"
									glyph="↓"
									testid="down-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x, widget.y + 1)))}
								/>
								<IconButton
									label="Make {widget.title || widget.id} wider"
									glyph="⇥"
									testid="wider-{widget.id}"
									onclick={() =>
										persist(withWidgets(resizeWidget(current.widgets, widget.id, widget.w + 1, widget.h)))}
								/>
								<IconButton
									label="Make {widget.title || widget.id} narrower"
									glyph="⇤"
									testid="narrower-{widget.id}"
									onclick={() =>
										persist(withWidgets(resizeWidget(current.widgets, widget.id, widget.w - 1, widget.h)))}
								/>
								<IconButton
									label="Make {widget.title || widget.id} taller"
									glyph="⇩"
									testid="taller-{widget.id}"
									onclick={() =>
										persist(withWidgets(resizeWidget(current.widgets, widget.id, widget.w, widget.h + 1)))}
								/>
								<IconButton
									label="Remove {widget.title || widget.id}"
									glyph="×"
									testid="remove-{widget.id}"
									onclick={() => persist(withWidgets(removeWidget(current.widgets, widget.id)))}
								/>
							</div>
						{/if}
					</section>
				{/each}
			</div>
		{/if}
	{/snippet}
</ScreenState>

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(var(--columns), 1fr);
		grid-auto-rows: minmax(var(--jv-space-7), auto);
		gap: var(--jv-space-3);
		margin-top: var(--jv-space-4);
	}
	.widget {
		display: grid;
		grid-template-rows: auto 1fr auto;
		gap: var(--jv-space-2);
		min-width: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
	}
	.widget[draggable='true'] {
		cursor: grab;
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-2);
	}
	.title {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.src {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.handles {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1);
	}
	.editor {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		gap: var(--jv-space-3);
		align-items: end;
	}
	.said {
		margin: var(--jv-space-3) 0 0;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
	}
	@media (max-width: 900px) {
		/* One column on a phone: a twelve-column grid at 360px is twelve columns
		   of nothing. Widgets keep their order and take the full width. */
		.grid {
			grid-template-columns: 1fr;
		}
		.widget {
			grid-column: 1 / -1 !important;
		}
	}
</style>
