<!--
@component
The arc reactor, as an instrument: a graduated bezel, a ring of blades with a
glint walking round, a counter-rotating coil, a level arc, and a dark lens with
two iris arcs and one hot dot. One component draws it at every size — the voice
screen's centrepiece, a task's progress ring, a dashboard's hero figure.

`level` (0–1) fills the arc: real audio amplitude on the voice screen, progress
on a task. `state` is which of the pipeline's five states the instrument
wears; they are distinct on purpose — idle breathes on the deep accent,
listening lifts the level and the rim, thinking turns amber and spins the fine
inner ring, speaking is gold and moves with the voice, error is red. The
palette is `color.orb.*` from the tokens, the clock is `motion.reactor.*`, and
the geometry is `tests/contracts/reactor_geometry.json`, which the phone reads
too (`reactor_orb_test.py`). `segments` groups the blades into plan steps.
`reveal` is the boot sequence's handle: each layer's own 0–1, so the
instrument can be assembled bezel → blades → coil → level → core.

```svelte
<Reactor size={360} fluid level={amplitude} state="listening" />
<Reactor size={460} segments={{ done: 2, running: 1, total: 5 }} level={0.61} breathing={false} />
```

Under `prefers-reduced-motion` nothing here turns, breathes or blinks; the
level still follows its prop, because a level is information and not
decoration.
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
	/** How much of each layer is on screen, 0–1. Every layer at 1 is the instrument. */
	export interface Reveal {
		bezel: number;
		blades: number;
		coil: number;
		level: number;
		core: number;
	}
	interface Props {
		size?: number;
		/** Scale to the container's width rather than `size` px. */
		fluid?: boolean;
		/** 0–1: how far round the level arc goes. */
		level?: number;
		/** Which pipeline state the instrument wears. */
		state?: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
		/** Group the blades into plan steps. */
		segments?: Segments | null;
		/** The core breathes and the rings turn. Off for a static figure. */
		breathing?: boolean;
		/** The boot sequence's per-layer reveal. Omit for the whole instrument. */
		reveal?: Reveal | null;
		label?: string;
		testid?: string;
	}
	let {
		size = 240,
		fluid = false,
		level = 0.4,
		state = 'idle',
		segments = null,
		breathing = true,
		reveal = null,
		label = 'Jarvis',
		testid = 'reactor'
	}: Props = $props();

	// --- the geometry, as tests/contracts/reactor_geometry.json has it -------
	// Typed here rather than imported so the component ships no JSON; the
	// contract is what `reactor.test.ts` holds this to, and `design/build.py
	// --check` refuses the two drifting.
	const TICKS = 120;
	const LONG_TICK_EVERY = 10;
	const LONG_TICK_LEN = 0.07;
	const SHORT_TICK_LEN = 0.032;
	const BLADES = 36;
	const BLADE_GAP_DEG = 3;
	const R_BLADE = 0.85;
	const BLADE_WIDTH_RATIO = 52;
	const BLADE_WIDTH_MIN = 3;
	const R_COIL = 0.74;
	const R_LEVEL = 0.65;
	const LEVEL_WIDTH = 3;
	const R_CORE = 0.56;
	const IRIS_A_R = 0.9;
	const IRIS_A_SWEEP = 1.25;
	const IRIS_B_R = 0.82;
	const IRIS_B_SWEEP = 1.1;
	const R_THINK = 0.47;
	const DOT_RATIO = 70;
	const DOT_MIN = 2.5;
	const DOT_GLOW_RATIO = 34;
	const DOT_GLOW_MIN = 4;
	const IDLE_BREATH_LEVEL = 0.14;

	const still = $derived(!breathing || prefersReducedMotion());
	const c = $derived(size / 2);
	const R = $derived(size / 2 - 4);
	const rBlade = $derived(R * R_BLADE);
	const rLevel = $derived(R * R_LEVEL);
	const rCore = $derived(R * R_CORE);
	const levelCircumference = $derived(2 * Math.PI * rLevel);
	const clampedLevel = $derived(Math.min(1, Math.max(0, Number.isFinite(level) ? level : 0)));
	/** Where the arc rests, and where the idle breath carries it. */
	const levelOffset = $derived(levelCircumference * (1 - clampedLevel));
	const breathOffset = $derived(levelCircumference * (1 - Math.min(1, clampedLevel + IDLE_BREATH_LEVEL)));
	const dotR = $derived(Math.max(DOT_MIN, size / DOT_RATIO));
	const dotGlowR = $derived(Math.max(DOT_GLOW_MIN, size / DOT_GLOW_RATIO));
	const blur = $derived(Math.max(2, size / 70));
	const shown = $derived<Reveal>(reveal ?? { bezel: 1, blades: 1, coil: 1, level: 1, core: 1 });
	// One id per instance, so two reactors on a page do not share a gradient.
	const uid = `rx-${Math.random().toString(36).slice(2, 8)}`;

	const point = (radius: number, angle: number): [number, number] => [
		c + radius * Math.cos(angle),
		c + radius * Math.sin(angle)
	];

	const ticks = $derived(
		Array.from({ length: TICKS }, (_, i) => {
			const angle = (i * 2 * Math.PI) / TICKS - Math.PI / 2;
			const long = i % LONG_TICK_EVERY === 0;
			const [x1, y1] = point(R - (long ? R * LONG_TICK_LEN : R * SHORT_TICK_LEN), angle);
			const [x2, y2] = point(R, angle);
			return { x1, y1, x2, y2, long };
		})
	);

	/** One blade's arc path, and which group it belongs to. */
	const bladePaths = $derived(
		Array.from({ length: BLADES }, (_, i) => {
			const step = (2 * Math.PI) / BLADES;
			const gap = (BLADE_GAP_DEG * Math.PI) / 180;
			const a0 = i * step - Math.PI / 2;
			const a1 = a0 + step - gap;
			const [x0, y0] = point(rBlade, a0);
			const [x1, y1] = point(rBlade, a1);
			let tone = i % 3 === 2 ? 'soft' : 'idle';
			if (segments && segments.total > 0) {
				const slot = Math.floor((i * segments.total) / BLADES);
				if (slot < segments.done) tone = 'done';
				else if (slot < segments.done + segments.running) tone = 'running';
				else tone = 'pending';
			}
			return {
				d: `M${x0.toFixed(2)} ${y0.toFixed(2)} A${rBlade} ${rBlade} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`,
				tone,
				// The glint walks round: each blade is a fraction of the period behind the last.
				delay: `calc(var(--jv-rx-glint) * ${(-i / BLADES).toFixed(4)})`
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
	class:fluid
	data-state={state}
	data-level={clampedLevel.toFixed(2)}
	data-segments={segments ? `${segments.done}/${segments.total}` : undefined}
	viewBox="0 0 {size} {size}"
	width={fluid ? undefined : size}
	height={fluid ? undefined : size}
	role="img"
	aria-label={label}
	data-testid={testid}
	style:--lv-a={levelOffset}
	style:--lv-b={breathOffset}
	style:--blade-width="{Math.max(BLADE_WIDTH_MIN, size / BLADE_WIDTH_RATIO)}px"
>
	<defs>
		<radialGradient id="{uid}-core" cx="50%" cy="50%" r="50%">
			<stop offset="0" stop-color="var(--jv-bg)" />
			<stop offset="0.82" stop-color="var(--jv-bg-raised)" />
			<stop offset="0.96" stop-color="var(--rx-deep)" stop-opacity="0.55" />
			<stop offset="1" stop-color="var(--rx-live)" stop-opacity="0.75" />
		</radialGradient>
		<filter id="{uid}-blur" x="-100%" y="-100%" width="300%" height="300%">
			<feGaussianBlur stdDeviation={blur} />
		</filter>
	</defs>

	<g class="bezel layer" style:opacity={shown.bezel} style:transform="scale({0.9 + shown.bezel * 0.1})">
		{#each ticks as tick, i (i)}
			<line x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2} class:long={tick.long} />
		{/each}
	</g>

	<g class="spin layer" style:opacity={shown.blades} style:transform="scale({0.9 + shown.blades * 0.1})">
		<g class="blades">
			{#each bladePaths as blade, i (i)}
				<path d={blade.d} class="blade {blade.tone}" style:animation-delay={blade.delay} />
			{/each}
		</g>
	</g>

	<circle class="coil layer" cx={c} cy={c} r={R * R_COIL} style:opacity={shown.coil} />

	<g class="layer" style:opacity={shown.level}>
		<circle class="level-track" cx={c} cy={c} r={rLevel} />
		<circle
			class="level"
			class:breathe={!still && state === 'idle' && !segments}
			cx={c}
			cy={c}
			r={rLevel}
			stroke-dasharray="{levelCircumference} {levelCircumference}"
			stroke-dashoffset={levelOffset}
		/>
	</g>

	<g class="core layer" style:opacity={shown.core} style:--core-scale={0.6 + shown.core * 0.4}>
		<circle cx={c} cy={c} r={rCore} fill="url(#{uid}-core)" />
		<circle class="rim" cx={c} cy={c} r={rCore} />
		<path class="iris a" d={irisArc(rCore * IRIS_A_R, -Math.PI / 2, Math.PI * IRIS_A_SWEEP)} />
		<path class="iris b" d={irisArc(rCore * IRIS_B_R, Math.PI / 2, Math.PI * IRIS_B_SWEEP)} />
		{#if !segments}
			<circle class="think" cx={c} cy={c} r={R * R_THINK} />
			<circle class="hot-glow" cx={c} cy={c} r={dotGlowR} filter="url(#{uid}-blur)" />
			<circle class="hot" cx={c} cy={c} r={dotR} />
		{/if}
	</g>
</svg>

<style>
	.reactor {
		display: block;
		overflow: visible;
		/*
		 * The state's colours, in two roles: `--rx-live` is what is lit now (the
		 * level, the rim, the dot's halo) and `--rx-hot` the dot itself. Idle is
		 * the accent at rest; every other state reads its palette from
		 * color.orb.*, the same table the phone draws from.
		 */
		--rx-live: var(--jv-accent-deep);
		--rx-deep: var(--jv-accent-deep);
		--rx-hot: var(--jv-accent);
		--rx-rim-opacity: 0.55;
		--rx-think-opacity: 0;
	}
	.reactor.fluid {
		width: 100%;
		height: auto;
	}
	[data-state='listening'] {
		--rx-live: var(--jv-orb-listening-blob-0);
		--rx-deep: var(--jv-orb-listening-blob-1);
		--rx-hot: var(--jv-orb-listening-core);
		--rx-rim-opacity: 0.85;
	}
	[data-state='thinking'] {
		--rx-live: var(--jv-orb-thinking-blob-0);
		--rx-deep: var(--jv-orb-thinking-blob-1);
		--rx-hot: var(--jv-orb-thinking-core);
		--rx-think-opacity: 0.55;
	}
	[data-state='speaking'] {
		--rx-live: var(--jv-orb-speaking-blob-0);
		--rx-deep: var(--jv-orb-speaking-blob-1);
		--rx-hot: var(--jv-orb-speaking-core);
		--rx-rim-opacity: 0.8;
	}
	[data-state='error'] {
		--rx-live: var(--jv-orb-error-blob-0);
		--rx-deep: var(--jv-orb-error-blob-1);
		--rx-hot: var(--jv-orb-error-core);
	}

	.layer {
		transform-origin: 50% 50%;
		transition: opacity var(--jv-dur-fast) var(--jv-ease-out);
	}
	line {
		stroke: var(--jv-tick);
		stroke-width: 1;
	}
	line.long {
		stroke: var(--jv-text-dim);
		stroke-width: 1.2;
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
	.blade.soft {
		stroke: var(--jv-line-soft);
	}
	.blade.done {
		stroke: var(--jv-text-dim);
		animation: none;
	}
	.blade.running {
		stroke: var(--rx-live);
		animation: pulse-stroke var(--jv-dur-pulse) var(--jv-ease-in-out) infinite alternate;
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
		stroke: var(--rx-live);
		stroke-width: 3;
		stroke-linecap: round;
		transform: rotate(-90deg);
		transform-origin: 50% 50%;
		filter: drop-shadow(0 0 4px var(--jv-glow));
		transition: stroke-dashoffset var(--jv-dur-fast) linear, stroke var(--jv-dur-base) var(--jv-ease-out);
	}
	.level.breathe {
		animation: level var(--jv-rx-level) var(--jv-ease-in-out) infinite;
	}
	.core {
		transform-origin: 50% 50%;
		animation: breathe var(--jv-rx-breathe) var(--jv-ease-in-out) infinite;
	}
	.rim {
		fill: none;
		stroke: var(--rx-live);
		stroke-opacity: var(--rx-rim-opacity);
		stroke-width: 1;
		transition: stroke var(--jv-dur-base) var(--jv-ease-out), stroke-opacity var(--jv-dur-base) var(--jv-ease-out);
	}
	.iris {
		fill: none;
		stroke-width: 1;
		stroke-linecap: round;
		transform-origin: 50% 50%;
	}
	.iris.a {
		stroke: var(--rx-deep);
		stroke-opacity: 0.7;
		animation: spin var(--jv-rx-iris-a) linear infinite;
	}
	.iris.b {
		stroke: var(--jv-text-dim);
		stroke-opacity: 0.6;
		animation: spin var(--jv-rx-iris-b) linear infinite reverse;
	}
	/* The thinking ring: dashed, fine, and the fastest thing on the instrument. */
	.think {
		fill: none;
		stroke: var(--rx-live);
		stroke-opacity: var(--rx-think-opacity);
		stroke-width: 1;
		stroke-dasharray: 1 5;
		transform-origin: 50% 50%;
		animation: spin var(--jv-rx-think) linear infinite;
		transition: stroke-opacity var(--jv-dur-base) var(--jv-ease-out);
	}
	.hot {
		fill: var(--rx-hot);
		transition: fill var(--jv-dur-base) var(--jv-ease-out);
	}
	.hot-glow {
		fill: var(--rx-live);
		opacity: 0.7;
		transition: fill var(--jv-dur-base) var(--jv-ease-out);
	}
	/* A dot that keeps time with the voice. */
	[data-state='speaking'] .hot-glow {
		animation: pulse var(--jv-dur-pulse) var(--jv-ease-in-out) infinite alternate;
	}
	[data-state='listening'] .hot-glow {
		animation: pulse var(--jv-dur-enter) var(--jv-ease-in-out) infinite alternate;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@keyframes breathe {
		0%,
		100% {
			transform: scale(var(--core-scale, 1));
		}
		50% {
			transform: scale(calc(var(--core-scale, 1) * 1.025));
		}
	}
	@keyframes glint {
		0% {
			stroke: var(--jv-line);
		}
		3% {
			stroke: var(--rx-live);
		}
		11% {
			stroke: var(--jv-line);
		}
		100% {
			stroke: var(--jv-line);
		}
	}
	@keyframes pulse-stroke {
		from {
			stroke: var(--rx-live);
		}
		to {
			stroke: var(--rx-deep);
		}
	}
	@keyframes level {
		0%,
		100% {
			stroke-dashoffset: var(--lv-a);
		}
		50% {
			stroke-dashoffset: var(--lv-b);
		}
	}
	@keyframes pulse {
		from {
			opacity: 0.7;
		}
		to {
			opacity: 0.25;
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
