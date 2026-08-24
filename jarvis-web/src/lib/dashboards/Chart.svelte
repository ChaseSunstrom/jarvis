<!--
@component
One widget's picture. Six types, one component, no charting library — the
console ships `ws` and nothing else, and a line, an area, bars, a number, a
gauge and a table are a hundred lines of SVG between them.

The rule the whole file exists to keep: **a gap is a gap**. `pathFor` breaks the
line where the server sent `null`, so a period nothing was recorded in is empty
rather than a straight line through time that never happened.
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

	interface Props {
		type: ChartType;
		series: SeriesData[];
		/** For a gauge: what counts as full. */
		max?: number;
	}
	let { type, series, max = 100 }: Props = $props();

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
</script>

{#if errors.length}
	<p class="why" data-testid="chart-error">{errors[0].error}</p>
{:else if empty}
	<p class="why" data-testid="chart-empty">
		Nothing recorded in this window. That is not zero — it is nothing.
	</p>
{:else if type === 'stat'}
	<div class="stat">
		<span class="big" data-testid="chart-value">{format(value, first?.unit ?? '')}</span>
		{#if extent}
			<svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
				<path class="line" d={pathFor(first, extent, WIDTH, HEIGHT)} />
			</svg>
		{/if}
	</div>
{:else if type === 'gauge'}
	<div class="gauge">
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
		<span class="big" data-testid="chart-value">{format(value, first?.unit ?? '')}</span>
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
	<svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" data-testid="chart-svg">
		{#if type === 'bar'}
			{#each first.points.filter((point) => point.value !== null) as point, i (i)}
				{@const span = Math.max(1, first.points.length)}
				{@const height =
					((point.value! - extent.min) / Math.max(1e-9, extent.max - extent.min)) * HEIGHT}
				<rect
					class="bar"
					x={(i / span) * WIDTH}
					y={HEIGHT - height}
					width={Math.max(1, WIDTH / span - 1)}
					height={Math.max(1, height)}
				/>
			{/each}
		{:else}
			{#if type === 'area'}
				<path class="area" d={areaPathFor(first, extent, WIDTH, HEIGHT)} />
			{/if}
			{#each series as one, i (one.key)}
				<path class="line" data-index={i} d={pathFor(one, extent, WIDTH, HEIGHT)} />
			{/each}
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
	}
	.line {
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 1.5;
		vector-effect: non-scaling-stroke;
	}
	/* More than one series on one chart: the second and third step back so the
	   first is still the one being read. */
	.line[data-index='1'] {
		stroke: var(--jv-text-dim);
	}
	.line[data-index='2'] {
		stroke: var(--jv-accent-deep);
	}
	.area {
		fill: var(--jv-wash-strong);
		stroke: none;
	}
	.bar {
		fill: var(--jv-accent-deep);
	}
	.axis {
		display: flex;
		justify-content: space-between;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.stat {
		display: grid;
		gap: var(--jv-space-2);
	}
	.big {
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-2xl);
		color: var(--jv-text-bright);
		font-variant-numeric: tabular-nums;
	}
	.gauge {
		display: flex;
		align-items: center;
		gap: var(--jv-space-4);
	}
	.gauge svg {
		width: var(--jv-space-7);
		height: var(--jv-space-7);
		flex: none;
	}
	.track {
		fill: none;
		stroke: var(--jv-line-soft);
		stroke-width: 8;
	}
	.fill {
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 8;
		stroke-linecap: round;
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
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
</style>
