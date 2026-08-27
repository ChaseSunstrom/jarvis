<script lang="ts">
	/**
	 * One task's progress bar. The whole of the visual vocabulary lives here so
	 * the dock, the page and (later) the phone all draw the same three things.
	 *
	 * Three modes, and the distinction between them is the point:
	 *
	 * - **determinate** — jarvis-core sent a fraction. Fill to it.
	 * - **indeterminate** — work IS happening and how far along is unknowable.
	 *   A sweep, no number, and no `aria-valuenow`, which is what tells a screen
	 *   reader to say "busy" rather than read out a figure nobody computed.
	 * - **none** — queued, waiting on a person, or over. A rail and nothing else.
	 *   An animation here would say "working" about a task that is not.
	 *
	 * Every one of those decisions is made in `$lib/tasks.ts` and tested in
	 * Node; this file only draws the answer. On Reactor II it is a thin accent
	 * rule on a hairline rail — no gradient, no glow.
	 */
	import { barMode, isFinished, percent, type TaskRow } from '$lib/tasks';

	let { task, compact = false }: { task: TaskRow; compact?: boolean } = $props();

	const mode = $derived(barMode(task));
	const pct = $derived(percent(task));
	const tone = $derived(
		task.status === 'error' ? 'error' : task.status === 'cancelled' ? 'muted' : 'accent'
	);
</script>

<div
	class="track"
	class:compact
	data-testid="task-bar-{task.id}"
	data-mode={mode}
	data-tone={tone}
	data-percent={pct}
	role="progressbar"
	aria-label="{task.title || 'task'} progress"
	aria-valuemin={mode === 'determinate' ? 0 : undefined}
	aria-valuemax={mode === 'determinate' ? 100 : undefined}
	aria-valuenow={mode === 'determinate' ? pct : undefined}
	aria-busy={!isFinished(task)}
>
	{#if mode === 'determinate'}
		<span class="fill" style="width: {pct}%"></span>
	{:else if mode === 'indeterminate'}
		<span class="sweep"></span>
	{/if}
</div>

<style>
	.track {
		position: relative;
		height: var(--jv-radius-sm);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-line-hair);
		overflow: hidden;
	}
	.track.compact {
		height: var(--jv-rule-live);
	}
	.fill {
		display: block;
		height: 100%;
		background: var(--jv-accent);
		/* The width IS the truth; the transition only stops it snapping. */
		transition: width var(--jv-dur-base) var(--jv-ease-out);
	}
	[data-tone='error'] .fill {
		background: var(--jv-danger);
	}
	[data-tone='muted'] .fill {
		background: var(--jv-line);
	}
	.sweep {
		display: block;
		position: absolute;
		inset: 0 auto 0 0;
		width: 40%;
		background: linear-gradient(90deg, transparent, var(--jv-accent), transparent);
		animation: task-sweep var(--jv-dur-sweep) var(--jv-ease-in-out) infinite;
	}
	@keyframes task-sweep {
		0% {
			transform: translateX(-100%);
		}
		100% {
			transform: translateX(350%);
		}
	}

	/*
	 * Reduced motion takes the sweep away, and something has to stand in its
	 * place: the rail alone is indistinguishable from a task that has not
	 * started. base.css cuts animations globally, which would leave the sweep
	 * frozen at its start position — a stub of colour on the left, which reads
	 * as "2% done". A dim full-width wash instead: no motion, no number, and
	 * visibly not the same as an empty rail.
	 */
	@media (prefers-reduced-motion: reduce) {
		.sweep {
			position: static;
			width: 100%;
			animation: none !important;
			background: var(--jv-accent-deep);
			opacity: 0.55;
		}
		.fill {
			transition: none;
		}
	}
</style>
