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

	const KIND_MARK: Record<string, string> = {
		status: '●',
		step: '▸',
		tool: '⟩',
		output: '·',
		note: '·'
	};

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
			<span class="mark" aria-hidden="true">{KIND_MARK[entry.kind] ?? '·'}</span>
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
		display: grid;
		gap: 0;
	}
	.entry {
		display: grid;
		grid-template-columns: 4rem var(--jv-space-4) 1fr;
		align-items: baseline;
		gap: var(--jv-space-2);
		padding: var(--jv-space-1) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
	}
	.at {
		font-family: var(--jv-font-chrome);
		font-variant-numeric: tabular-nums;
		color: var(--jv-text-faint);
	}
	.mark {
		color: var(--jv-tick);
	}
	.text {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.entry[data-kind='status'] .mark,
	.entry[data-kind='step'] .mark {
		color: var(--jv-accent-deep);
	}
	.entry[data-kind='tool'] .text {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text);
	}
	.entry.live {
		color: var(--jv-text-bright);
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.entry.live .mark {
		color: var(--jv-accent);
	}
	.empty {
		padding: var(--jv-space-4);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
</style>
