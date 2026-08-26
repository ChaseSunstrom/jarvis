<!--
@component
A task as a reactor: the blades grouped into its plan's steps — done, running,
still to come — the level arc at its progress, and in the lens the step it is
on, the percentage and the title. Reactor II's task view is built around one
of these; a task card carries a small one.

```svelte
<ProgressRing size={460} done={2} running={1} total={5} percent={61} step="step 3 of 5 · wiring" title="Add an OFFLINE state" elapsed="code · 02:14 elapsed" />
```
-->
<script lang="ts">
	import Reactor from './Reactor.svelte';
	import Figure from './Figure.svelte';

	interface Props {
		size?: number;
		fluid?: boolean;
		done?: number;
		running?: number;
		total?: number;
		/** 0–100, or null for a task that reports no fraction. */
		percent?: number | null;
		step?: string;
		title?: string;
		elapsed?: string;
		state?: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
		/** Draw only the ring, no text in the lens. */
		bare?: boolean;
		testid?: string;
	}
	let {
		size = 460,
		fluid = false,
		done = 0,
		running = 0,
		total = 0,
		percent = null,
		step = '',
		title = '',
		elapsed = '',
		state = 'idle',
		bare = false,
		testid = 'progress-ring'
	}: Props = $props();

	const level = $derived(percent === null ? (total ? done / total : 0) : percent / 100);
</script>

<div class="ring" class:fluid style:width={fluid ? undefined : `${size}px`} data-testid={testid} data-percent={percent ?? ''}>
	<Reactor
		{size}
		fluid
		level={Math.max(0, Math.min(1, level))}
		{state}
		segments={total > 0 ? { done, running, total } : null}
		breathing={running > 0}
		label={title || 'progress'}
		testid="{testid}-reactor"
	/>
	{#if !bare}
		<div class="inner">
			<div>
				{#if step}<div class="step">{step}</div>{/if}
				{#if percent !== null}
					<div class="pct"><Figure value={percent} unit="%" testid="{testid}-figure" /></div>
				{/if}
				{#if title}<h1>{title}</h1>{/if}
				{#if elapsed}<div class="el">{elapsed}</div>{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.ring {
		position: relative;
		aspect-ratio: 1;
		margin: 0 auto;
	}
	.ring.fluid {
		width: 100%;
	}
	.inner {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		text-align: center;
		padding: 24%;
		pointer-events: none;
	}
	.step {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-accent);
	}
	.pct {
		margin: var(--jv-space-2) 0;
	}
	h1 {
		margin: 0;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-md);
		line-height: 1.35;
		color: var(--jv-text);
	}
	.el {
		margin-top: var(--jv-space-3);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
	}
</style>
