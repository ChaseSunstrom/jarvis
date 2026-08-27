<script lang="ts">
	/**
	 * What a job is printing, while it prints it.
	 *
	 * The output of a check used to arrive in the finished job's record: you
	 * watched a bar for four minutes and then read 400 lines at once. This is
	 * the same text as it happens.
	 *
	 * It sticks to the bottom while you are at the bottom, and stops sticking
	 * the moment you scroll up — a pane that yanks itself back down while
	 * somebody is reading is worse than one that never scrolls at all.
	 */
	import type { OutputChunk } from '$lib/taskEvents';

	interface Props {
		chunks: OutputChunk[];
		/** Shown when there is nothing yet. */
		waiting?: string;
		/** Still printing: a caret blinks after the last line. */
		live?: boolean;
	}
	let { chunks, waiting = 'Nothing printed yet.', live = false }: Props = $props();

	let pane = $state<HTMLDivElement | null>(null);
	let stuck = $state(true);
	const SLACK_PX = 24;

	function onscroll() {
		if (!pane) return;
		// A little tolerance: "at the bottom" has to survive a fractional
		// scroll height, or the pane unsticks itself on its own output.
		stuck = pane.scrollHeight - pane.scrollTop - pane.clientHeight < SLACK_PX;
	}

	$effect(() => {
		// Re-runs whenever a chunk arrives.
		chunks.length;
		if (pane && stuck) pane.scrollTop = pane.scrollHeight;
	});
</script>

<div
	class="out"
	bind:this={pane}
	{onscroll}
	role="log"
	aria-live="polite"
	aria-label="Job output"
	data-testid="task-output"
	data-stuck={stuck}
>
	{#if chunks.length}
		{#each chunks as chunk (chunk.seq)}
			<pre class="chunk" data-stream={chunk.stream}>{chunk.chunk}</pre>
		{/each}
		{#if live}<span class="caret" aria-hidden="true"></span>{/if}
	{:else}
		<p class="waiting">{waiting}</p>
	{/if}
</div>

<style>
	.out {
		max-height: var(--jv-measure-log);
		overflow: auto;
		background: var(--jv-surface-sunken);
		padding: var(--jv-space-3) var(--jv-space-4);
	}
	.chunk {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.85;
		color: var(--jv-text-dim);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.chunk[data-stream='stderr'] {
		color: var(--jv-danger-text);
	}
	.chunk[data-stream='note'] {
		color: var(--jv-text);
	}
	.caret {
		display: inline-block;
		width: var(--jv-rule-live);
		height: var(--jv-rel-caret);
		vertical-align: -0.15em;
		background: var(--jv-accent);
		animation: blink var(--jv-dur-enter) steps(2) infinite;
	}
	@keyframes blink {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0;
		}
	}
	.waiting {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
</style>
