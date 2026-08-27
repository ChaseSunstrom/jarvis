<script lang="ts">
	/**
	 * Everything that happened to one task, in the order it happened.
	 *
	 * The task list answers "how far has it got". This answers "what has it been
	 * doing" — which is the question somebody actually has when a job has been
	 * running for four minutes.
	 *
	 * Entries come from two places and are drawn as one: the replayed log
	 * (`jarvis/tasks/log`, for what happened before this page was open) and the
	 * live events. The time axis is relative to the task's start, because
	 * "+02:14" is what somebody watching wants and "17:42:03" is not.
	 */
	import type { LogEntry } from '$lib/taskEvents';

	interface Props {
		entries: LogEntry[];
		/** Epoch seconds the task began, so the axis can be relative. */
		startedAt?: number;
		/** Still running: the last entry gets the live mark. */
		live?: boolean;
	}
	let { entries, startedAt = 0, live = false }: Props = $props();

	function offset(at: number): string {
		if (!startedAt || !at) return '';
		const seconds = Math.max(0, Math.round(at - startedAt));
		const minutes = Math.floor(seconds / 60);
		return `+${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
	}
</script>

<ol class="timeline" data-testid="task-timeline">
	{#each entries as entry, i (i)}
		<li
			class="entry"
			data-kind={entry.kind}
			class:live={live && i === entries.length - 1}
			data-testid="timeline-entry"
		>
			<span class="at">{offset(entry.at)}</span>
			<i class="mark" aria-hidden="true"></i>
			<span class="text">{entry.text}</span>
		</li>
	{:else}
		<li class="empty">Nothing has happened yet.</li>
	{/each}
</ol>

<style>
	.timeline {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.entry {
		display: grid;
		grid-template-columns: auto auto minmax(0, 1fr);
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.entry:last-child {
		border-bottom: 0;
	}
	.at {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		font-variant-numeric: tabular-nums;
		color: var(--jv-text-faint);
		min-width: calc(var(--jv-space-7) + var(--jv-space-2));
	}
	/* A dot per entry, in the colour of what it was: a step or a status in the
	   deep accent, a tool call brighter, output the quiet tick. */
	.mark {
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: 50%;
		background: var(--jv-tick);
		align-self: center;
	}
	.text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.entry[data-kind='status'] .mark,
	.entry[data-kind='step'] .mark {
		background: var(--jv-accent-deep);
	}
	.entry[data-kind='tool'] .mark {
		background: var(--jv-text-dim);
	}
	.entry[data-kind='tool'] .text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	.entry.live {
		color: var(--jv-text-bright);
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.entry.live .mark {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.empty {
		padding: var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
</style>
