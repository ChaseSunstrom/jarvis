<!--
  One panel on the voice screen's surface (M83): a thing Jarvis put up, drawn
  live, movable by hand. The body is the dashboard's own widget for the kind —
  the same tile, still, readings, sky and moments the boards draw — so a panel
  can never show something the house cannot. Dragging moves it on a 12-column
  grid over the stage; the drop is told to the server, which is what makes the
  arrangement the same on every screen and the same after a reload.
-->
<script lang="ts">
	import { groupReadings, type CameraStill, type MomentRow, type ReadingsPayload, type SkySummary } from '$lib/dashboards/widgets';
	import Chart from '$lib/dashboards/Chart.svelte';
	import type { SeriesData } from '$lib/dashboards/series';
	import CameraStillView from '$lib/dashboards/CameraStill.svelte';
	import EntityTile from '$lib/dashboards/EntityTile.svelte';
	import Moments from '$lib/dashboards/Moments.svelte';
	import Readings from '$lib/dashboards/Readings.svelte';
	import SkyTonight from '$lib/dashboards/SkyTonight.svelte';
	import type { EntityState, SurfacePanel } from '$lib/jarvisClient';

	interface Props {
		panel: SurfacePanel;
		/** The stage's width in px; a column is a twelfth of it. */
		width: number;
		/** One row's height in px. */
		row: number;
		entityState?: EntityState | null;
		still?: CameraStill | null;
		readings?: ReadingsPayload | null;
		sky?: SkySummary | null;
		moments?: MomentRow[];
		series?: SeriesData[];
		error?: string;
		now: number;
		index: number;
		onmove: (id: string, where: { x: number; y: number; w?: number; h?: number }) => void;
		onremove: (id: string) => void;
		onswitch?: (entityId: string, service: string) => void;
	}

	let {
		panel,
		width,
		row,
		entityState = null,
		still = null,
		readings = null,
		sky = null,
		moments = [],
		series = [],
		error = '',
		now,
		index,
		onmove,
		onremove,
		onswitch
	}: Props = $props();

	const column = $derived(width / 12);
	// While a drag is under way the panel follows the pointer; the grid
	// position is only written when it is dropped, so a half-drag is not a
	// stream of moves down the socket.
	let dragging = $state<{ dx: number; dy: number; x: number; y: number } | null>(null);
	let resizing = $state<{ w: number; h: number; x0: number; y0: number } | null>(null);

	const left = $derived((dragging ? dragging.x : panel.x) * column);
	const top = $derived((dragging ? dragging.y : panel.y) * row);
	const pxWidth = $derived((resizing ? resizing.w : panel.w) * column);
	const pxHeight = $derived((resizing ? resizing.h : panel.h) * row);

	function startDrag(event: PointerEvent) {
		if ((event.target as HTMLElement).closest('button, input, a, [data-no-drag]')) return;
		const el = event.currentTarget as HTMLElement;
		el.setPointerCapture(event.pointerId);
		dragging = { dx: event.clientX - left, dy: event.clientY - top, x: panel.x, y: panel.y };
	}
	function drag(event: PointerEvent) {
		if (!dragging) return;
		const x = Math.round((event.clientX - dragging.dx) / column);
		const y = Math.round((event.clientY - dragging.dy) / row);
		dragging = { ...dragging, x: Math.max(0, Math.min(x, 12 - panel.w)), y: Math.max(0, y) };
	}
	function endDrag(event: PointerEvent) {
		if (!dragging) return;
		const where = { x: dragging.x, y: dragging.y };
		dragging = null;
		(event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
		if (where.x !== panel.x || where.y !== panel.y) onmove(panel.id, where);
	}
	function startResize(event: PointerEvent) {
		event.stopPropagation();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		resizing = { w: panel.w, h: panel.h, x0: event.clientX, y0: event.clientY };
	}
	function resize(event: PointerEvent) {
		if (!resizing) return;
		const w = Math.max(2, Math.min(12 - panel.x, panel.w + Math.round((event.clientX - resizing.x0) / column)));
		const h = Math.max(1, Math.min(12, panel.h + Math.round((event.clientY - resizing.y0) / row)));
		resizing = { ...resizing, w, h };
	}
	function endResize(event: PointerEvent) {
		if (!resizing) return;
		const size = { w: resizing.w, h: resizing.h };
		resizing = null;
		(event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
		if (size.w !== panel.w || size.h !== panel.h) onmove(panel.id, { x: panel.x, y: panel.y, ...size });
	}
</script>

<article
	class="panel"
	class:dragging={!!dragging}
	data-testid="surface-panel-{panel.id}"
	data-kind={panel.kind}
	data-x={dragging ? dragging.x : panel.x}
	data-y={dragging ? dragging.y : panel.y}
	style="left:{left}px; top:{top}px; width:{pxWidth}px; height:{pxHeight}px; --i:{index}"
	onpointerdown={startDrag}
	onpointermove={drag}
	onpointerup={endDrag}
	onpointercancel={endDrag}
	aria-label={panel.title || panel.kind}
>
	<header class="head">
		<span class="title" data-testid="surface-title-{panel.id}">{panel.title || panel.kind}</span>
		<span class="kind">{panel.kind}</span>
		<button
			class="close"
			type="button"
			data-testid="surface-close-{panel.id}"
			title="Take this off the screen"
			aria-label="Take {panel.title || panel.kind} off the screen"
			onclick={() => onremove(panel.id)}>×</button
		>
	</header>
	<div class="body" data-no-drag={panel.kind === 'entity' ? '' : undefined}>
		{#if error}
			<p class="bad" role="alert">{error}</p>
		{:else if panel.kind === 'chart'}
			<div class="chart" data-testid="surface-chart-{panel.id}"><Chart type="line" {series} live /></div>
		{:else if panel.kind === 'entity'}
			<EntityTile entityId={panel.entity} state={entityState ?? undefined} live {now} onswitch={onswitch ? (service) => onswitch(panel.entity, service) : undefined} />
		{:else if panel.kind === 'camera'}
			<CameraStillView {still} camera={panel.camera} />
		{:else if panel.kind === 'readings'}
			<Readings groups={groupReadings(readings?.readings ?? [])} configured={readings?.configured ?? true} area={panel.area} live />
		{:else if panel.kind === 'sky'}
			<SkyTonight {sky} live />
		{:else if panel.kind === 'moments'}
			<Moments moments={moments.slice(0, panel.limit)} live />
		{:else if panel.kind === 'note' || panel.kind === 'page'}
			<div class="text" data-testid="surface-text-{panel.id}">
				{#if panel.url}<span class="src">{panel.url}</span>{/if}
				<p>{panel.text || 'Nothing to show yet.'}</p>
			</div>
		{/if}
	</div>
	<span
		class="grip"
		data-testid="surface-resize-{panel.id}"
		role="presentation"
		onpointerdown={startResize}
		onpointermove={resize}
		onpointerup={endResize}
		onpointercancel={endResize}
	></span>
</article>

<style>
	.panel {
		position: absolute;
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		box-shadow: var(--jv-elev-panel);
		overflow: hidden;
		touch-action: none;
		user-select: none;
		cursor: grab;
		/* Enters with the stagger, like a board's tiles: a thing put up by
		   voice should arrive, not blink into place. */
		animation: enter var(--jv-dur-enter) var(--jv-ease-out) both;
		animation-delay: min(calc(var(--i) * var(--jv-stagger-step)), var(--jv-stagger-cap));
		transition: left var(--jv-dur-base) var(--jv-ease-out), top var(--jv-dur-base) var(--jv-ease-out);
	}
	.panel.dragging {
		cursor: grabbing;
		transition: none;
		box-shadow: var(--jv-elev-float);
		z-index: 2;
	}
	@keyframes enter {
		from {
			opacity: 0;
			transform: translateY(var(--jv-space-2));
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	.head {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		padding: var(--jv-space-2) var(--jv-space-3);
		border-bottom: 1px solid var(--jv-line-hair);
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.title {
		color: var(--jv-text);
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
	}
	.kind {
		color: var(--jv-text-faint);
	}
	.close {
		background: none;
		border: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-md);
		line-height: 1;
		cursor: pointer;
		padding: 0 var(--jv-space-1);
	}
	.close:hover {
		color: var(--jv-text-bright);
	}
	.body {
		min-height: 0;
		overflow: auto;
		padding: var(--jv-space-2) var(--jv-space-3);
	}
	.chart {
		height: 100%;
		min-height: 0;
	}
	.text {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		white-space: pre-wrap;
	}
	.src {
		display: block;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		margin-bottom: var(--jv-space-1);
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.bad {
		color: var(--jv-danger-text);
		font-size: var(--jv-fs-sm);
	}
	.grip {
		position: absolute;
		right: 0;
		bottom: 0;
		width: var(--jv-space-4);
		height: var(--jv-space-4);
		cursor: nwse-resize;
		border-right: 1px solid var(--jv-tick);
		border-bottom: 1px solid var(--jv-tick);
		border-radius: 0 0 var(--jv-radius-md) 0;
	}
	@media (prefers-reduced-motion: reduce) {
		.panel {
			animation: none;
			transition: none;
		}
	}
</style>
