<!--
@component
The arc reactor, as an instrument: a graduated bezel, a ring of blades, a
counter-rotating coil, a level arc, and a dark lens with two iris arcs. One
component draws it at every size — the voice screen's orb, a task's progress
ring, a dashboard's hero figure.

`level` fills the arc (0–1). `segments` groups the blades into plan steps —
`{ done, running, total }` — which is what makes the task ring a progress ring.
Every colour is a token; nothing here is typed.

```svelte
<Reactor size={320} level={0.38} state="listening" breathing />
<Reactor size={460} segments={{ done: 2, running: 1, total: 5 }} level={0.61} />
```
-->
<script lang="ts">
	import { prefersReducedMotion } from '$lib/motion';

	interface Segments {
		/** Steps finished. */
		done: number;
		/** Steps running now (drawn in the accent). */
		running: number;
		total: number;
	}
	interface Props {
		size?: number;
		/** 0–1: how far round the level arc goes. */
		level?: number;
		/** Which pipeline state the core wears. */
		state?: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
		blades?: number;
		/** Group the blades into plan steps. */
		segments?: Segments | null;
		/** The core breathes and the rings turn. Off for a static figure. */
		breathing?: boolean;
		label?: string;
	}
	let {
		size = 240,
		level = 0.4,
		state = 'idle',
		blades = 36,
		segments = null,
		breathing = true,
		label = 'Jarvis'
	}: Props = $props();

	const still = $derived(!breathing || prefersReducedMotion());
	const c = $derived(size / 2);
	const R = $derived(size / 2 - 4);
	const rBlade = $derived(R * 0.85);
	const rLevel = $derived(R * 0.65);
	const rCore = $derived(R * 0.56);
	const levelCircumference = $derived(2 * Math.PI * rLevel);

	const TICKS = 120;
	const point = (radius: number, angle: number): [number, number] => [
		c + radius * Math.cos(angle),
		c + radius * Math.sin(angle)
	];

	const ticks = $derived(
		Array.from({ length: TICKS }, (_, i) => {
			const angle = (i * 2 * Math.PI) / TICKS - Math.PI / 2;
			const long = i % 10 === 0;
			const [x1, y1] = point(R - (long ? R * 0.07 : R * 0.032), angle);
			const [x2, y2] = point(R, angle);
			return { x1, y1, x2, y2, long };
		})
	);

	/** One blade's arc path, and which group it belongs to. */
	const bladePaths = $derived(
		Array.from({ length: blades }, (_, i) => {
			const step = (2 * Math.PI) / blades;
			const gap = (3 * Math.PI) / 180;
			const a0 = i * step - Math.PI / 2;
			const a1 = a0 + step - gap;
			const [x0, y0] = point(rBlade, a0);
			const [x1, y1] = point(rBlade, a1);
			let tone = 'idle';
			if (segments && segments.total > 0) {
				const slot = Math.floor((i * segments.total) / blades);
				if (slot < segments.done) tone = 'done';
				else if (slot < segments.done + segments.running) tone = 'running';
				else tone = 'pending';
			}
			return {
				d: `M${x0.toFixed(2)} ${y0.toFixed(2)} A${rBlade} ${rBlade} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`,
				tone,
				delay: `${(-i * 14) / blades}s`
			};
		})
	);

	function irisArc(radius: number, from: number, sweep: number): string {
		const [x0, y0] = point(radius, from);
		const [x1, y1] = point(radius, from + sweep);
		return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${radius} ${radius} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
	}
</script>

<svg
	class="reactor"
	class:still
	data-state={state}
	viewBox="0 0 {size} {size}"
	width={size}
	height={size}
	role="img"
	aria-label={label}
	data-testid="reactor"
>
	<defs>
		<radialGradient id="rx-core-{size}" cx="50%" cy="50%" r="50%">
			<stop offset="0" stop-color="var(--jv-bg)" />
			<stop offset="0.82" stop-color="var(--jv-bg-raised)" />
			<stop offset="0.96" stop-color="var(--jv-accent-deep)" stop-opacity="0.55" />
			<stop offset="1" stop-color="var(--jv-accent)" stop-opacity="0.75" />
		</radialGradient>
	</defs>

	<g class="bezel">
		{#each ticks as tick, i (i)}
			<line x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2} class:long={tick.long} />
		{/each}
	</g>

	<g class="blades" style:--blade-width="{Math.max(3, size / 52)}px">
		{#each bladePaths as blade, i (i)}
			<path d={blade.d} class="blade {blade.tone}" style:animation-delay={blade.delay} />
		{/each}
	</g>

	<circle class="coil" cx={c} cy={c} r={R * 0.74} />
	<circle class="level-track" cx={c} cy={c} r={rLevel} />
	<circle
		class="level"
		cx={c}
		cy={c}
		r={rLevel}
		stroke-dasharray="{levelCircumference} {levelCircumference}"
		stroke-dashoffset={levelCircumference * (1 - Math.min(1, Math.max(0, level)))}
	/>

	<g class="core">
		<circle cx={c} cy={c} r={rCore} fill="url(#rx-core-{size})" />
		<circle class="rim" cx={c} cy={c} r={rCore} />
		<path class="iris a" d={irisArc(rCore - rCore * 0.1, -Math.PI / 2, Math.PI * 1.25)} />
		<path class="iris b" d={irisArc(rCore - rCore * 0.18, Math.PI / 2, Math.PI * 1.1)} />
		{#if !segments}
			<circle class="hot" cx={c} cy={c} r={Math.max(2.5, size / 70)} />
		{/if}
	</g>
</svg>

<style>
	.reactor {
		display: block;
		overflow: visible;
	}
	line {
		stroke: var(--jv-tick);
		stroke-width: 1;
	}
	line.long {
		stroke: var(--jv-text-dim);
	}
	.blades {
		transform-origin: 50% 50%;
		animation: spin var(--jv-rx-blades) linear infinite;
	}
	.blade {
		fill: none;
		stroke: var(--jv-line);
		stroke-width: var(--blade-width);
		animation: glint var(--jv-rx-glint) linear infinite;
	}
	.blade.done {
		stroke: var(--jv-text-dim);
		animation: none;
	}
	.blade.running {
		stroke: var(--jv-accent);
		animation: pulse-stroke var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.blade.pending {
		stroke: var(--jv-line-soft);
		animation: none;
	}
	.coil {
		fill: none;
		stroke: var(--jv-tick);
		stroke-width: 1;
		stroke-dasharray: 2 6;
		transform-origin: 50% 50%;
		animation: spin var(--jv-rx-coil) linear infinite reverse;
	}
	.level-track {
		fill: none;
		stroke: var(--jv-line-soft);
		stroke-width: 3;
	}
	.level {
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 3;
		stroke-linecap: round;
		transform: rotate(-90deg);
		transform-origin: 50% 50%;
		filter: drop-shadow(0 0 4px var(--jv-glow));
	}
	.core {
		transform-origin: 50% 50%;
		animation: breathe var(--jv-rx-breathe) var(--jv-ease-in-out) infinite;
	}
	.rim {
		fill: none;
		stroke: var(--jv-accent);
		stroke-opacity: 0.55;
		stroke-width: 1;
	}
	.iris {
		fill: none;
		stroke-width: 1;
		stroke-linecap: round;
		transform-origin: 50% 50%;
	}
	.iris.a {
		stroke: var(--jv-accent-deep);
		stroke-opacity: 0.7;
		animation: spin var(--jv-rx-iris-a) linear infinite;
	}
	.iris.b {
		stroke: var(--jv-text-dim);
		stroke-opacity: 0.6;
		animation: spin var(--jv-rx-iris-b) linear infinite reverse;
	}
	.hot {
		fill: var(--jv-accent);
	}
	[data-state='thinking'] .rim,
	[data-state='thinking'] .hot {
		stroke: var(--jv-amber);
		fill: var(--jv-amber);
	}
	[data-state='speaking'] .rim,
	[data-state='speaking'] .hot {
		stroke: var(--jv-gold);
		fill: var(--jv-gold);
	}
	[data-state='error'] .rim,
	[data-state='error'] .hot {
		stroke: var(--jv-danger);
		fill: var(--jv-danger);
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@keyframes breathe {
		0%,
		100% {
			transform: scale(1);
		}
		50% {
			transform: scale(1.025);
		}
	}
	@keyframes glint {
		0% {
			stroke: var(--jv-line);
		}
		3% {
			stroke: var(--jv-accent);
		}
		11% {
			stroke: var(--jv-line);
		}
		100% {
			stroke: var(--jv-line);
		}
	}
	@keyframes pulse-stroke {
		0%,
		100% {
			stroke: var(--jv-accent);
		}
		50% {
			stroke: var(--jv-accent-deep);
		}
	}
	.still :global(*),
	.still {
		animation: none !important;
	}
	@media (prefers-reduced-motion: reduce) {
		:global(.reactor *) {
			animation: none !important;
		}
	}
</style>
