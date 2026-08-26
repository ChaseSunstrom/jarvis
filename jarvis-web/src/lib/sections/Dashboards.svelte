<script lang="ts">
	/**
	 * The house at a glance, on Reactor II's dashboard view.
	 *
	 * A dashboard is a grid of widgets somebody arranged. From M63 a widget
	 * has a KIND: a graph of recorded numbers (M62's metric widgets), one
	 * entity's state with its switch, the newest sensor readings by room, a
	 * still from a camera, the sky tonight, the newest moments. Each kind is
	 * its own component under `$lib/dashboards`; this page fetches what each
	 * needs, keeps the entity tiles, readings and moments live over the
	 * socket, and carries a press on a tile to the backend the way the HOUSE ›
	 * Devices rows do — the same `call_service`, the same toast.
	 *
	 * A dashboard belongs to the token that saved it — there are no user
	 * accounts here, and a token is one device — so what this page shows is
	 * "yours, plus the shared ones", and the editor only appears on one you own.
	 *
	 * Arranging is done with controls rather than only with a mouse: move,
	 * widen, grow and remove are buttons, so the layout can be changed from a
	 * keyboard and asserted by a test. Dragging one card onto another swaps
	 * them, which is what "reorder" means on a grid — nothing is left in a gap
	 * and nothing silently overlaps.
	 */
	import { onDestroy, onMount } from 'svelte';
	import { describeError, openConnection, type Connection, type ConnectionStatus } from '$lib/connection';
	import { domainOf, type EntityState, type MetricSource, type Subscription } from '$lib/jarvisClient';
	import {
		CHART_TYPES,
		CHART_TYPE_NAMES,
		RANGES,
		SOURCE_NOTES,
		WIDGET_KINDS,
		WIDGET_KIND_NAMES,
		type ChartType,
		type Range,
		type WidgetKind
	} from '$lib/dashboards/chartTypes';
	import {
		COLUMNS,
		addWidget,
		blankWidget,
		moveWidget,
		newWidgetId,
		removeWidget,
		resizeWidget,
		sortWidgets,
		swapWidgets,
		toWidget,
		widgetSubject,
		wireWidget,
		type Dashboard,
		type Widget
	} from '$lib/dashboards/layout';
	import { latest, type SeriesData } from '$lib/dashboards/series';
	import {
		addMoment,
		applyReading,
		groupReadings,
		toMoment,
		type CameraStill as Still,
		type MomentRow,
		type ReadingsPayload,
		type SkySummary
	} from '$lib/dashboards/widgets';
	import Chart from '$lib/dashboards/Chart.svelte';
	import EntityTile from '$lib/dashboards/EntityTile.svelte';
	import Readings from '$lib/dashboards/Readings.svelte';
	import CameraStill from '$lib/dashboards/CameraStill.svelte';
	import SkyTonight from '$lib/dashboards/SkyTonight.svelte';
	import Moments from '$lib/dashboards/Moments.svelte';
	import { staggerStyle } from '$lib/motion';
	import { serviceFailureText, serviceSuccessText, toasts } from '$lib/toast';
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
	let editing = $state(false);
	let saying = $state('');
	let dragging = $state('');

	// What each widget shows, by kind. Graphs by widget id; the entity states,
	// the sky and the moments shared across widgets of that kind, because two
	// tiles of the same light are one light.
	let data = $state<Record<string, SeriesData[]>>({});
	let states = $state<Record<string, EntityState>>({});
	/**
	 * Entities this board saw leave the house (M69), by id.
	 *
	 * A `state_changed` with no new state is the one signal a removal gives, and
	 * without remembering it the tile reads "No entity called X on this Jarvis.
	 * Add the device…" — the wording for a tile that was never right, shown for
	 * one that was right until a minute ago.
	 */
	let removed = $state<Record<string, true>>({});
	let readingsBy = $state<Record<string, ReadingsPayload>>({});
	let stills = $state<Record<string, Still | null>>({});
	let sky = $state<SkySummary | null>(null);
	let moments = $state<MomentRow[]>([]);
	/** One widget's fetch failing is one widget's problem: its own sentence. */
	let widgetErrors = $state<Record<string, string>>({});
	/** The entity a press is waiting on, and what the last press said. */
	let busyTile = $state('');
	let tileErrors = $state<Record<string, string>>({});
	let subs: Subscription[] = [];

	// The widget editor's draft: the kind first, then what that kind needs.
	let newKind = $state<WidgetKind>('metric');
	let newType = $state<ChartType>('line');
	let newSource = $state('internal');
	let newSeries = $state('');
	let newEntity = $state('');
	let newCamera = $state('');
	let newArea = $state('');
	let newLimit = $state('');
	let newTitle = $state('');

	const current = $derived(boards.find((board) => board.id === currentId) ?? null);
	const mine = $derived(!!current && !current.shipped);
	/** The most moments any widget on this board asks for: one fetch serves them all. */
	const momentsWanted = $derived(
		Math.max(0, ...(current?.widgets ?? []).filter((w) => w.kind === 'moments').map((w) => w.limit))
	);

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
			subs = [];
			const link = await openConnection({ onStatus: (s) => (status = s) });
			conn = link;
			boards = await link.client.listDashboards();
			sources = await link.client.metricsSources();
			if (!boards.some((board) => board.id === currentId)) currentId = boards[0]?.id ?? '';
			await refresh();
			await listen(link);
			err = '';
		} catch (error) {
			err = error instanceof Error ? error.message : String(error);
		} finally {
			loading = false;
			redialling = false;
		}
	}

	/**
	 * Keep the tiles, the readings and the moments live. Subscribed after the
	 * first fetch so an event cannot land on an empty table; an older backend
	 * without the moment event still gets the list.
	 */
	async function listen(link: Connection) {
		try {
			subs.push(
				await link.client.subscribeEvents((event) => {
					const entityId = String(event.data?.entity_id ?? '');
					const next = event.data?.new_state as EntityState | undefined;
					if (!entityId) return;
					if (next) {
						states[entityId] = next;
						delete removed[entityId];
					} else {
						delete states[entityId];
						removed[entityId] = true;
					}
					for (const id of Object.keys(readingsBy)) {
						readingsBy[id] = {
							...readingsBy[id],
							readings: applyReading(readingsBy[id].readings, next)
						};
					}
				}, 'state_changed')
			);
			subs.push(
				await link.client.subscribeEvents((event) => {
					const moment = toMoment((event.data as { notification?: unknown })?.notification);
					moments = addMoment(moments, moment, Math.max(momentsWanted, 1));
				}, 'jarvis_notification')
			);
		} catch {
			// The list was fetched; only the live updates are missing.
		}
	}

	/** Fetch what every widget on the board needs, each kind once. */
	async function refresh() {
		const board = current;
		const link = conn;
		if (!link || !board) return;
		const kinds = new Set(board.widgets.map((widget) => widget.kind));
		const errors: Record<string, string> = {};
		const jobs: Promise<void>[] = [];

		for (const widget of board.widgets) {
			if (widget.kind === 'metric') {
				jobs.push(
					link.client
						.metricsQuery({
							source: widget.source,
							series: widget.series,
							range: board.range,
							aggregate: widget.aggregate || undefined
						})
						.then((series) => {
							data[widget.id] = series;
						})
						.catch((error) => {
							// One widget's source being down is one widget's problem:
							// the other five still draw.
							data[widget.id] = widget.series.map((key) => ({
								key,
								label: key,
								unit: '',
								aggregate: '',
								error: error instanceof Error ? error.message : String(error),
								points: []
							}));
						})
				);
			} else if (widget.kind === 'readings') {
				jobs.push(
					link.client
						.sensorReadings(widget.area)
						.then((payload) => {
							readingsBy[widget.id] = payload;
						})
						.catch((error) => {
							errors[widget.id] = describeError(error);
						})
				);
			} else if (widget.kind === 'camera') {
				// A still is a look: every refresh is one audited snapshot per
				// camera widget, and a refusal is drawn as the refusal.
				jobs.push(
					link.client
						.visionStill(widget.camera)
						.then((still) => {
							stills[widget.id] = still;
						})
						.catch((error) => {
							errors[widget.id] = describeError(error);
						})
				);
			}
		}
		if (kinds.has('entity')) {
			jobs.push(
				link.client
					.getStates()
					.then((list) => {
						for (const state of list) states[state.entity_id] = state;
					})
					.catch((error) => {
						for (const widget of board.widgets) {
							if (widget.kind === 'entity') errors[widget.id] = describeError(error);
						}
					})
			);
		}
		if (kinds.has('sky')) {
			jobs.push(
				link.client
					.skySummary()
					.then((summary) => {
						sky = summary;
					})
					.catch((error) => {
						for (const widget of board.widgets) {
							if (widget.kind === 'sky') errors[widget.id] = describeError(error);
						}
					})
			);
		}
		if (kinds.has('moments')) {
			jobs.push(
				link.client
					.listMoments(momentsWanted)
					.then((rows) => {
						moments = rows;
					})
					.catch((error) => {
						for (const widget of board.widgets) {
							if (widget.kind === 'moments') errors[widget.id] = describeError(error);
						}
					})
			);
		}
		await Promise.all(jobs);
		widgetErrors = errors;
	}

	/**
	 * A press on an entity tile: the same call the HOUSE › Devices row makes,
	 * the same toast. The tile itself changes when the backend says so
	 * (`state_changed`), not when the button was pressed — a switch that
	 * flipped on the screen and not in the room is the lie this avoids.
	 */
	async function switchEntity(widget: Widget, service: string) {
		if (!conn) return;
		const entityId = widget.entity;
		const label = states[entityId]?.attributes?.friendly_name ?? entityId;
		busyTile = entityId;
		delete tileErrors[entityId];
		try {
			const result = await conn.client.callService(domainOf(entityId), service, {
				entity_id: entityId
			});
			for (const changed of (result?.changed_states ?? []) as EntityState[]) {
				if (changed?.entity_id) states[changed.entity_id] = changed;
			}
			toasts.success(serviceSuccessText(service, label), entityId);
		} catch (error) {
			// Both channels on purpose: the toast is what you notice, the line
			// on the tile is what is still there ten seconds later.
			tileErrors[entityId] = describeError(error);
			toasts.error(serviceFailureText(service, label), describeError(error));
		} finally {
			busyTile = '';
		}
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

	/** The draft as the server would clean it, or why it would refuse it. */
	function draft(): { widget: Widget } | { why: string } {
		if (!current) return { why: 'No dashboard is open.' };
		const widget = blankWidget(newKind, newWidgetId(current.widgets));
		widget.title = newTitle;
		if (newKind === 'metric') {
			widget.type = newType;
			widget.source = newSource;
			widget.series = newSeries
				.split(',')
				.map((key) => key.trim())
				.filter(Boolean);
			widget.w = newType === 'stat' || newType === 'gauge' ? 3 : 6;
			if (!widget.series.length) return { why: 'Pick at least one series.' };
		} else if (newKind === 'entity') {
			widget.entity = newEntity.trim();
		} else if (newKind === 'camera') {
			widget.camera = newCamera.trim();
		} else if (newKind === 'readings') {
			widget.area = newArea.trim();
		} else if (newKind === 'moments') {
			widget.limit = Number(newLimit) || widget.limit;
		}
		const cleaned = toWidget(wireWidget(widget));
		if (!cleaned) {
			return {
				why:
					newKind === 'entity'
						? 'That is not an entity id. It looks like light.hall_lamp — the id under a name on HOUSE › Devices.'
						: 'That widget cannot be drawn.'
			};
		}
		return { widget: cleaned };
	}

	async function addNew() {
		if (!current) return;
		const made = draft();
		if ('why' in made) {
			saying = made.why;
			return;
		}
		await persist(withWidgets(addWidget(current.widgets, made.widget)));
		newSeries = '';
		newEntity = '';
		newCamera = '';
		newArea = '';
		newLimit = '';
		newTitle = '';
		await refresh();
	}

	async function drop(targetId: string) {
		if (!current || !dragging || dragging === targetId) return;
		await persist(withWidgets(swapWidgets(current.widgets, dragging, targetId)));
		dragging = '';
	}

	onMount(connect);
	onDestroy(() => {
		for (const sub of subs) void sub.unsubscribe();
		conn?.close();
	});

	const seriesFor = (source: string) => sources.find((one) => one.name === source)?.series ?? [];
	/** A few entity ids a person could type, for the tile editor's hint. */
	const entityHint = $derived(
		Object.keys(states)
			.filter((id) => ['light', 'switch', 'lock', 'input_boolean', 'sensor'].includes(domainOf(id)))
			.sort()
			.slice(0, 4)
			.join(', ')
	);

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
	emptyBody="Jarvis ships two worked examples, the house and the machine. If you cannot see them, this backend has no dashboards integration configured."
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
							<!-- The kind first: what the widget SHOWS decides which fields follow. -->
							<Field label="Show" hint={WIDGET_KINDS[newKind].when}>
								<Select
									bind:value={newKind}
									testid="new-kind"
									options={WIDGET_KIND_NAMES.map((name) => ({
										value: name,
										label: WIDGET_KINDS[name].label
									}))}
								/>
							</Field>
							{#if newKind === 'metric'}
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
							{:else if newKind === 'entity'}
								<Field label="Entity" hint={entityHint || 'the id under a name on HOUSE › Devices'}>
									<Input bind:value={newEntity} placeholder="light.hall_lamp" mono testid="new-entity" />
								</Field>
							{:else if newKind === 'camera'}
								<Field label="Camera" hint="as named under vision: cameras:; blank means the only one">
									<Input bind:value={newCamera} placeholder="Front Door" testid="new-camera" />
								</Field>
							{:else if newKind === 'readings'}
								<Field label="Room" hint="blank means every room">
									<Input bind:value={newArea} placeholder="Kitchen" testid="new-area" />
								</Field>
							{:else if newKind === 'moments'}
								<Field label="How many" hint="newest first; 1 to 20">
									<Input bind:value={newLimit} placeholder="6" mono testid="new-limit" />
								</Field>
							{/if}
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
						data-kind={widget.kind}
						data-type={widget.kind === 'metric' ? widget.type : undefined}
						data-w={widget.w}
						data-x={widget.x}
						role="group"
						aria-label={widgetSubject(widget)}
						draggable={editing}
						ondragstart={() => (dragging = widget.id)}
						ondragover={(event) => editing && event.preventDefault()}
						ondrop={() => drop(widget.id)}
					>
						<header>
							<span class="title">{widgetSubject(widget)}</span>
							{#if widget.kind === 'metric'}
								<span class="src" class:inf={widget.source !== 'internal'}>{widget.source}</span>
							{:else}
								<span class="src">{widget.kind}</span>
							{/if}
						</header>
						{#if widgetErrors[widget.id]}
							<p class="why" role="alert" data-testid="widget-error">{widgetErrors[widget.id]}</p>
						{:else if widget.kind === 'metric'}
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
						{:else if widget.kind === 'entity'}
							<EntityTile
								entityId={widget.entity}
								state={states[widget.entity]}
								removed={Boolean(removed[widget.entity])}
								live={hero}
								busy={busyTile === widget.entity}
								error={tileErrors[widget.entity] ?? ''}
								onswitch={(service) => switchEntity(widget, service)}
							/>
						{:else if widget.kind === 'readings'}
							<Readings
								groups={groupReadings(readingsBy[widget.id]?.readings ?? [])}
								configured={readingsBy[widget.id]?.configured ?? true}
								area={widget.area}
								live={hero}
							/>
						{:else if widget.kind === 'camera'}
							<CameraStill still={stills[widget.id] ?? null} camera={widget.camera} />
						{:else if widget.kind === 'sky'}
							<SkyTonight {sky} live={hero} />
						{:else if widget.kind === 'moments'}
							<Moments moments={moments.slice(0, widget.limit)} live={hero} />
						{/if}
						{#if editing}
							<div class="handles">
								<IconButton
									label="Move {widgetSubject(widget)} left"
									glyph="←"
									testid="left-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x - 1, widget.y)))}
								/>
								<IconButton
									label="Move {widgetSubject(widget)} right"
									glyph="→"
									testid="right-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x + 1, widget.y)))}
								/>
								<IconButton
									label="Move {widgetSubject(widget)} up"
									glyph="↑"
									testid="up-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x, widget.y - 1)))}
								/>
								<IconButton
									label="Move {widgetSubject(widget)} down"
									glyph="↓"
									testid="down-{widget.id}"
									onclick={() =>
										persist(withWidgets(moveWidget(current.widgets, widget.id, widget.x, widget.y + 1)))}
								/>
								<IconButton
									label="Make {widgetSubject(widget)} wider"
									glyph="⇥"
									testid="wider-{widget.id}"
									onclick={() =>
										persist(withWidgets(resizeWidget(current.widgets, widget.id, widget.w + 1, widget.h)))}
								/>
								<IconButton
									label="Make {widgetSubject(widget)} narrower"
									glyph="⇤"
									testid="narrower-{widget.id}"
									onclick={() =>
										persist(withWidgets(resizeWidget(current.widgets, widget.id, widget.w - 1, widget.h)))}
								/>
								<IconButton
									label="Make {widgetSubject(widget)} taller"
									glyph="⇩"
									testid="taller-{widget.id}"
									onclick={() =>
										persist(withWidgets(resizeWidget(current.widgets, widget.id, widget.w, widget.h + 1)))}
								/>
								<IconButton
									label="Remove {widgetSubject(widget)}"
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
	.said,
	.why {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.why {
		margin: 0;
		color: var(--jv-text-faint);
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
