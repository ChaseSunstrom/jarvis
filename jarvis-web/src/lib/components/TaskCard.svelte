<script lang="ts">
	/**
	 * One task, in full: what it is, where it has got to, and what you can do
	 * about it.
	 *
	 * The steps are the interesting part and they are collapsed by default —
	 * open while the task is running, because that is when "which step" is the
	 * question, and shut once it is over, when the answer is the result. A page
	 * of twelve finished research jobs should not be forty screens of steps.
	 */
	import TaskBar from './TaskBar.svelte';
	import {
		ago,
		canCancel,
		currentStep,
		describeTask,
		elapsed,
		isFinished,
		statusLabel,
		stepCount,
		type TaskRow
	} from '$lib/tasks';

	let {
		task,
		busy = false,
		onCancel,
		onForget
	}: {
		task: TaskRow;
		/** An action on this task is in flight, so its buttons are inert. */
		busy?: boolean;
		onCancel?: (task: TaskRow) => void;
		onForget?: (task: TaskRow) => void;
	} = $props();

	// `open` follows the task until somebody touches it, then it is theirs:
	// having a row you deliberately expanded slam shut the moment the task
	// finishes is the worst possible timing.
	let touched = $state(false);
	let opened = $state(false);
	const open = $derived(touched ? opened : !isFinished(task) && task.steps.length > 0);
	const step = $derived(currentStep(task));
	const counts = $derived(stepCount(task));

	function toggle(): void {
		touched = true;
		opened = !open;
	}
</script>

<article class="task" data-testid="task-card-{task.id}" data-status={task.status} data-kind={task.kind}>
	<header>
		<span class="badge" data-testid="task-status-{task.id}">{statusLabel(task)}</span>
		<h3 title={task.title}>{task.title}</h3>
		<span class="meta">
			<span class="kind">{task.kind}</span>
			{#if counts}<span data-testid="task-steps-{task.id}">{counts}</span>{/if}
			<span title="started {ago(task.created)}">{elapsed(task)}</span>
		</span>
	</header>

	<p class="detail" data-testid="task-detail-{task.id}">{describeTask(task)}</p>

	<TaskBar {task} />

	<footer>
		{#if task.steps.length}
			<button
				type="button"
				class="disclose"
				data-testid="task-steps-toggle-{task.id}"
				aria-expanded={open}
				onclick={toggle}
			>
				{open ? '▾' : '▸'} {task.steps.length} step{task.steps.length === 1 ? '' : 's'}
				{#if step && !open}<span class="now">— {step.title}</span>{/if}
			</button>
		{:else}
			<span class="disclose flat">no steps reported</span>
		{/if}

		<span class="actions">
			{#if canCancel(task) && onCancel}
				<button
					type="button"
					class="act"
					data-testid="task-cancel-{task.id}"
					disabled={busy}
					onclick={() => onCancel?.(task)}>CANCEL</button
				>
			{/if}
			{#if onForget}
				<button
					type="button"
					class="act"
					data-testid="task-forget-{task.id}"
					disabled={busy}
					onclick={() => onForget?.(task)}>FORGET</button
				>
			{/if}
		</span>
	</footer>

	{#if open && task.steps.length}
		<ol class="steps" data-testid="task-step-list-{task.id}">
			{#each task.steps as s, i (s.title + i)}
				<li data-status={s.status}>
					<span class="mark" aria-hidden="true">
						{#if s.status === 'done'}[ok]{:else if s.status === 'error'}[--]{:else if s.status === 'running'}[&gt;&gt;]{:else if s.status === 'blocked'}[??]{:else}[&nbsp;&nbsp;]{/if}
					</span>
					<span class="what">{s.title}</span>
					{#if s.detail}<span class="say">{s.detail}</span>{/if}
				</li>
			{/each}
		</ol>
	{/if}
</article>

<style>
	.task {
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-panel);
		padding: var(--jv-space-3);
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.task[data-status='error'] {
		border-color: var(--jv-danger);
	}
	.task[data-status='blocked'] {
		border-color: var(--jv-warn);
	}
	header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-2);
	}
	h3 {
		margin: 0;
		font-size: var(--jv-fs-sm);
		font-weight: 500;
		color: var(--jv-text-bright);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.badge {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
		padding: 1px var(--jv-space-2);
		white-space: nowrap;
	}
	[data-status='running'] .badge {
		color: var(--jv-accent);
		border-color: var(--jv-accent-deep);
	}
	[data-status='blocked'] .badge {
		color: var(--jv-warn);
		border-color: var(--jv-warn);
	}
	[data-status='done'] .badge {
		color: var(--jv-ok);
	}
	[data-status='error'] .badge {
		color: var(--jv-danger-text);
		border-color: var(--jv-danger);
	}
	.meta {
		display: flex;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
	}
	.kind {
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
	}
	.detail {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	[data-status='error'] .detail {
		color: var(--jv-danger-text);
	}
	footer {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.disclose {
		background: none;
		border: 0;
		padding: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
		cursor: pointer;
		text-align: left;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.disclose.flat {
		cursor: default;
		color: var(--jv-text-faint);
	}
	.disclose:hover:not(.flat) {
		color: var(--jv-accent);
	}
	.now {
		color: var(--jv-text-faint);
		text-transform: none;
		letter-spacing: 0;
	}
	.actions {
		display: flex;
		gap: var(--jv-space-2);
		flex: 0 0 auto;
	}
	.act {
		background: none;
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		padding: 2px var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
		cursor: pointer;
	}
	.act:hover:not(:disabled) {
		color: var(--jv-accent);
		border-color: var(--jv-accent-deep);
	}
	.act:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.steps {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
		border-top: 1px dashed var(--jv-line-hair);
		padding-top: var(--jv-space-2);
	}
	.steps li {
		display: grid;
		grid-template-columns: auto minmax(0, auto) minmax(0, 1fr);
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.steps li[data-status='running'] .what {
		color: var(--jv-accent);
	}
	.steps li[data-status='done'] .mark {
		color: var(--jv-ok);
	}
	.steps li[data-status='error'] .mark,
	.steps li[data-status='error'] .say {
		color: var(--jv-danger-text);
	}
	.steps li[data-status='blocked'] .mark {
		color: var(--jv-warn);
	}
	.say {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
