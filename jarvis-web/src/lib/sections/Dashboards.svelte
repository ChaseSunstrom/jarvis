<script lang="ts">
	/**
	 * Graphs somebody arranged, on Reactor II's dashboard view.
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
	import {
		CHART_TYPES,
		CHART_TYPE_NAMES,
		RANGES,
		SOURCE_NOTES,
		type ChartType,
		type Range
	} from '$lib/dashboards/chartTypes';
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
	import { latest, type SeriesData } from '$lib/dashboards/series';
	import Chart from '$lib/dashboards/Chart.svelte';
	import { staggerStyle } from '$lib/motion';
	import {
		Button,
		Field,
		IconButton,
		Input,
		Panel,
		Pill,
		Reactor,
		ScreenState,
		Select,
		Toolbar
	} from '$lib/ui';

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

	/** The hero's instrument reads the widget's latest value against its window's max. */
	function heroLevel(widget: Widget): number {
		const series = data[widget.id]?.[0];
		const value = latest(series);
		if (value === null || !series) return 0.35;
		const values = series.points.map((p) => p.value).filter((v): v is number => v !== null);
		const max = Math.max(...values, value);
		return max > 0 ? Math.max(0.05, Math.min(1, value / max)) : 0.35;
	}
</script>


<p class="lede" data-testid="dashboards-screen">
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
		<div class="head">
			<Toolbar>
				{#snippet children()}
					<Select
						bind:value={currentId}
						options={boards.map((board) => ({ value: board.id, label: board.title }))}
						testid="dashboard-picker"
						onchange={refresh}
					/>
					{#if current?.shipped}<Pill>shipped · read only</Pill>{/if}
					{#if current}<span class="count">{current.widgets.length} widget{current.widgets.length === 1 ? '' : 's'}</span>{/if}
				{/snippet}
				{#snippet end()}
					<!-- The range, as one segmented control: four words in a hairline box. -->
					<div class="seg" role="group" aria-label="Range">
						{#each RANGES as range (range)}
							<Button onclick={() => setRange(range)} testid="range-{range}" pressed={current?.range === range}>{range}</Button>
						{/each}
					</div>
					<Button onclick={refresh} testid="dashboard-refresh">Refresh</Button>
					{#if mine}
						<!-- One way in (M55): + Widget opens the layout editor; DONE closes
						     it. "Edit layout" beside it was a second door to the same room. -->
						{#if editing}
							<Button onclick={() => (editing = false)} testid="dashboard-edit" pressed={editing}>Done</Button>
						{:else}
							<!-- The one filled control on this screen. -->
							<Button variant="primary" onclick={() => (editing = true)} testid="dashboard-add">+ Widget</Button>
						{/if}
					{/if}
				{/snippet}
			</Toolbar>
		</div>

		{#if saying}<p class="said" role="status" data-testid="dashboard-said">{saying}</p>{/if}

		{#if editing && current}
			<div class="editor-wrap">
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
							<Field
								label="Source"
								hint={SOURCE_NOTES[newSource] ??
									sources.find((one) => one.name === newSource)?.description ??
									''}
							>
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
			</div>
		{/if}

		{#if current}
			<div class="grid" data-testid="dashboard-grid" style="--columns:{COLUMNS}">
				{#each sortWidgets(current.widgets) as widget, wi (widget.id)}
					{@const hero = wi === 0}
					<section
						class="card jv-stagger"
						class:hero
						style="grid-column: span {widget.w}; grid-row: span {widget.h}; {staggerStyle(wi)}"
						data-testid="widget-{widget.id}" data-jv-row
						data-type={widget.type}
						data-w={widget.w}
						data-x={widget.x}
						role="group"
						aria-label={widget.title || widget.series[0]}
						draggable={editing}
						ondragstart={() => (dragging = widget.id)}
						ondragover={(event) => editing && event.preventDefault()}
						ondrop={() => drop(widget.id)}
					>
						<header>
							<span class="title">{widget.title || widget.series[0]}</span>
							<span class="src" class:inf={widget.source !== 'internal'}>{widget.source}</span>
						</header>
						{#if hero && widget.type === 'stat'}
							<!-- The hero carries the instrument: its level is the widget's
							     latest value against the window's own high. -->
							<div class="hero-body">
								<div class="mini" aria-hidden="true">
									<Reactor size={150} fluid level={heroLevel(widget)} state="listening" label="" testid="hero-reactor" />
								</div>
								<Chart type={widget.type} series={data[widget.id] ?? []} live />
							</div>
						{:else}
							<Chart type={widget.type} series={data[widget.id] ?? []} live={hero} />
						{/if}
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
						<i class="handle" aria-hidden="true"></i>
					</section>
				{/each}
			</div>
		{/if}
	{/snippet}
</ScreenState>

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.head {
		margin-bottom: var(--jv-space-4);
	}
	.count {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	/* The range: four buttons in one hairline box, the pressed one lit. */
	.seg {
		display: inline-flex;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.seg :global(.btn) {
		border: 0;
		border-right: 1px solid var(--jv-line-hair);
		border-radius: 0;
	}
	.seg :global(.btn:last-child) {
		border-right: 0;
	}
	.seg :global(.btn.on) {
		background: var(--jv-surface-2);
		color: var(--jv-text-bright);
	}
	.editor-wrap {
		margin-bottom: var(--jv-space-4);
	}
	.editor {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		gap: var(--jv-space-3);
		align-items: end;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(var(--columns), 1fr);
		grid-auto-rows: minmax(var(--jv-space-7), auto);
		gap: var(--jv-space-4);
	}
	.card {
		position: relative;
		display: grid;
		grid-template-rows: auto 1fr auto;
		gap: var(--jv-space-2);
		min-width: 0;
		padding: var(--jv-space-4);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.card.hero {
		border-color: var(--jv-line-soft);
	}
	.card[draggable='true'] {
		cursor: grab;
	}
	header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-2);
	}
	.title {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.src {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		white-space: nowrap;
	}
	/* An external source wears a mark, as Reactor II marks Influx. */
	.src.inf::before {
		content: '◆ ';
		color: var(--jv-warn);
	}
	.hero-body {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		gap: var(--jv-space-4);
		align-items: center;
		min-height: 0;
	}
	.mini {
		width: calc(var(--jv-space-7) * 3.125);
		max-width: 30vw;
	}
	.handles {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1);
	}
	/* The resize corner, as Reactor II draws it: two hairlines, bottom right. */
	.handle {
		position: absolute;
		right: var(--jv-space-2);
		bottom: var(--jv-space-2);
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-right: 1px solid var(--jv-line);
		border-bottom: 1px solid var(--jv-line);
		pointer-events: none;
	}
	.said {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	@media (max-width: 900px) {
		/* One column on a phone: a twelve-column grid at 360px is twelve columns
		   of nothing. Widgets keep their order and take the full width. */
		.grid {
			grid-template-columns: 1fr;
		}
		.card {
			grid-column: 1 / -1 !important;
		}
		.mini {
			width: calc(var(--jv-space-7) * 2.2);
		}
	}
</style>
