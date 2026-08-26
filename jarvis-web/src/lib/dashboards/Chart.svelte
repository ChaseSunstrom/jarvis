<!--
@component
One widget's picture. Six types, one component, no charting library — the
console ships `ws` and nothing else, and a line, an area, bars, a number, a
gauge and a table are a hundred lines of SVG between them.

The rule the whole file exists to keep: **a gap is a gap**. `pathFor` breaks the
line where the server sent `null`, so a period nothing was recorded in is empty
rather than a straight line through time that never happened.

Reactor II draws data as light: a line has a gradient under it and draws in
on `--jv-dur-sweep`, bars grow from the baseline one after another, a number
counts up (`Figure`), and the last point carries a tick. `live` puts the accent
on it; everything else is the dim of a value that is not happening now.
-->
<script lang="ts">
	import type { ChartType } from './chartTypes';
	import {
		areaPathFor,
		extentOf,
		format,
		latest,
		pathFor,
		total,
		type SeriesData
	} from './series';
	import { Figure } from '$lib/ui';
	import { staggerStyle } from '$lib/motion';

	interface Props {
		type: ChartType;
		series: SeriesData[];
		/** For a gauge: what counts as full. */
		max?: number;
		/** This is the value happening now: the accent, not the dim. */
		live?: boolean;
	}
	let { type, series, max = 100, live = false }: Props = $props();

	const WIDTH = 300;
	const HEIGHT = 90;

	const extent = $derived(extentOf(series));
	const errors = $derived(series.filter((one) => one.error));
	const empty = $derived(!extent && !errors.length);
	const first = $derived(series[0]);
	const value = $derived(latest(first));
	const gaugeFraction = $derived(
		value === null ? 0 : Math.max(0, Math.min(1, value / (max || 100)))
	);
	/** How many decimals a count-up shows: what the data itself carries. */
	const decimals = $derived(
		value === null ? 0 : Number.isInteger(value) ? 0 : Math.abs(value) < 10 ? 2 : 1
	);
	// One gradient id per instance, so two charts on a page do not share one.
	const uid = `ch-${Math.random().toString(36).slice(2, 8)}`;

	/** Where the last drawn point sits, for the tick that marks "now". */
	const last = $derived.by(() => {
		if (!extent || !first) return null;
		const points = first.points;
		let i = points.length - 1;
		while (i >= 0 && points[i].value === null) i--;
		if (i < 0) return null;
		const span = Math.max(1e-9, extent.max - extent.min);
		const x = points.length > 1 ? (i / (points.length - 1)) * WIDTH : WIDTH;
		const y = HEIGHT - ((points[i].value! - extent.min) / span) * HEIGHT;
		return { x, y };
	});
</script>

{#if errors.length}
	<p class="why" data-testid="chart-error">{errors[0].error}</p>
{:else if empty}
	<p class="why" data-testid="chart-empty">
		Nothing recorded in this window. That is not zero — it is nothing.
	</p>
{:else if type === 'stat'}
	<div class="stat">
		<span data-testid="chart-value"><Figure {value} unit={first?.unit ?? ''} {decimals} {live} /></span>
		{#if extent}
			<svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" aria-hidden="true" class:live>
				<defs>
					<linearGradient id="{uid}-fill" x1="0" y1="0" x2="0" y2="1">
						<stop offset="0" class="stop-a" />
						<stop offset="1" class="stop-b" />
					</linearGradient>
				</defs>
				<path class="area" d={areaPathFor(first, extent, WIDTH, HEIGHT)} fill="url(#{uid}-fill)" />
				<path class="line spark" d={pathFor(first, extent, WIDTH, HEIGHT)} />
			</svg>
		{/if}
	</div>
{:else if type === 'gauge'}
	<div class="gauge" class:live>
		<svg viewBox="0 0 120 120" role="img" aria-label="{format(value)} of {max}">
			<circle class="track" cx="60" cy="60" r="48" />
			<circle
				class="fill"
				cx="60"
				cy="60"
				r="48"
				stroke-dasharray="{(gaugeFraction * 2 * Math.PI * 48).toFixed(1)} 999"
				transform="rotate(-90 60 60)"
			/>
		</svg>
		<span data-testid="chart-value"><Figure {value} unit={first?.unit ?? ''} {decimals} {live} small /></span>
	</div>
{:else if type === 'table'}
	<table data-testid="chart-table">
		<tbody>
			{#each series as one (one.key)}
				<tr>
					<td>{one.label}</td>
					<td class="n">{format(one.aggregate === 'sum' ? total(one) : latest(one), one.unit)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{:else if extent}
	<svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" data-testid="chart-svg" class:live>
		<defs>
			<linearGradient id="{uid}-fill" x1="0" y1="0" x2="0" y2="1">
				<stop offset="0" class="stop-a" />
				<stop offset="1" class="stop-b" />
			</linearGradient>
		</defs>
		{#if type === 'bar'}
			{#each first.points.filter((point) => point.value !== null) as point, i (i)}
				{@const span = Math.max(1, first.points.length)}
				{@const height =
					((point.value! - extent.min) / Math.max(1e-9, extent.max - extent.min)) * HEIGHT}
				<rect
					class="bar"
					class:hi={extent.max > extent.min && point.value! >= extent.min + (extent.max - extent.min) * 0.8}
					class:now={i === first.points.length - 1}
					x={(i / span) * WIDTH}
					y={HEIGHT - height}
					width={Math.max(1, WIDTH / span - 1)}
					height={Math.max(1, height)}
					style={staggerStyle(i)}
				/>
			{/each}
		{:else}
			{#if type === 'area'}
				<path class="area" d={areaPathFor(first, extent, WIDTH, HEIGHT)} fill="url(#{uid}-fill)" />
			{/if}
			{#each series as one, i (one.key)}
				<path class="line spark" data-index={i} d={pathFor(one, extent, WIDTH, HEIGHT)} />
			{/each}
			{#if last}
				<line class="tick" x1={last.x} x2={last.x} y1={last.y - 5} y2={last.y + 5} />
			{/if}
		{/if}
	</svg>
	<div class="axis">
		<span>{format(extent.min, first?.unit ?? '')}</span>
		<span>{format(extent.max, first?.unit ?? '')}</span>
	</div>
{/if}

<style>
	svg {
		width: 100%;
		height: 100%;
		min-height: var(--jv-space-7);
		overflow: visible;
		/* The series colour: the dim for a value at rest, the accent for one
		   happening now. The gradient, the line, the tick and the bars all read
		   it, so a chart is one colour and not four. */
		--series: var(--jv-text-dim);
	}
	svg.live {
		--series: var(--jv-accent);
	}
	.stop-a {
		stop-color: var(--series);
		stop-opacity: 0.28;
	}
	.stop-b {
		stop-color: var(--series);
		stop-opacity: 0;
	}
	.line {
		fill: none;
		stroke: var(--series);
		stroke-width: 1.5;
		vector-effect: non-scaling-stroke;
	}
	/* Drawn in: the dash is longer than any path here, offset away and pulled back. */
	.spark {
		stroke-dasharray: 1200;
		stroke-dashoffset: 1200;
		animation: draw var(--jv-dur-sweep) var(--jv-ease-out) forwards;
	}
	/* More than one series on one chart: the second and third step back so the
	   first is still the one being read. */
	.line[data-index='1'] {
		stroke: var(--jv-text-faint);
	}
	.line[data-index='2'] {
		stroke: var(--jv-accent-deep);
	}
	.area {
		stroke: none;
		animation: jv-fade var(--jv-dur-sweep) var(--jv-ease-out) both;
	}
	.tick {
		stroke: var(--series);
		stroke-width: 2;
		vector-effect: non-scaling-stroke;
	}
	.bar {
		fill: var(--jv-line);
		transform-origin: bottom;
		transform-box: fill-box;
		animation: grow var(--jv-dur-enter) var(--jv-ease-out) both;
		animation-delay: var(--jv-delay, 0ms);
	}
	.bar.hi {
		fill: var(--jv-text-dim);
	}
	.bar.now {
		fill: var(--series);
	}
	svg.live .bar.now {
		fill: var(--jv-accent);
	}
	.axis {
		display: flex;
		justify-content: space-between;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		margin-top: var(--jv-space-2);
	}
	.stat {
		display: grid;
		gap: var(--jv-space-2);
		min-height: 0;
	}
	.gauge {
		display: flex;
		align-items: center;
		gap: var(--jv-space-4);
		--series: var(--jv-text-dim);
	}
	.gauge.live {
		--series: var(--jv-accent);
	}
	.gauge svg {
		width: var(--jv-space-7);
		height: var(--jv-space-7);
		min-height: 0;
		flex: none;
	}
	.track {
		fill: none;
		stroke: var(--jv-line-soft);
		stroke-width: 8;
	}
	.fill {
		fill: none;
		stroke: var(--series);
		stroke-width: 8;
		stroke-linecap: round;
		transition: stroke-dasharray var(--jv-dur-enter) var(--jv-ease-out);
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--jv-fs-xs);
	}
	td {
		padding: var(--jv-space-1) 0;
		border-bottom: 1px solid var(--jv-line-hair);
		color: var(--jv-text-dim);
	}
	.n {
		text-align: right;
		font-family: var(--jv-font-chrome);
		color: var(--jv-text);
		font-variant-numeric: tabular-nums;
	}
	.why {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	@keyframes draw {
		to {
			stroke-dashoffset: 0;
		}
	}
	@keyframes grow {
		from {
			transform: scaleY(0);
		}
		to {
			transform: scaleY(1);
		}
	}
</style>
