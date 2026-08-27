<script lang="ts">
	/**
	 * One task, in full: what it is, where it has got to, and what you can do
	 * about it.
	 *
	 * The steps are the interesting part and they are collapsed by default —
	 * open while the task is running, because that is when "which step" is the
	 * question, and shut once it is over, when the answer is the result. A page
	 * of twelve finished research jobs should not be forty screens of steps.
	 *
	 * On Reactor II the card is a flat panel: the title in the body face, the
	 * kind and the elapsed time in mono, the bar a thin accent rule, and the
	 * state said twice — a tag, and an inset rule down the left edge in the
	 * state's colour, so a failed job reads as one before the word is read.
	 */
	import TaskBar from './TaskBar.svelte';
	import { Button, Pill } from '$lib/ui';
	import {
		ago,
		canCancel,
		canRetry,
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
		onRetry,
		onForget
	}: {
		task: TaskRow;
		/** An action on this task is in flight, so its buttons are inert. */
		busy?: boolean;
		onCancel?: (task: TaskRow) => void;
		onRetry?: (task: TaskRow) => void;
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
	const tone = $derived(
		task.status === 'running'
			? 'live'
			: task.status === 'blocked'
				? 'warn'
				: task.status === 'error'
					? 'danger'
					: task.status === 'done'
						? 'ok'
						: 'neutral'
	);

	function toggle(): void {
		touched = true;
		opened = !open;
	}
</script>

<article class="task" data-testid="task-card-{task.id}" data-status={task.status} data-kind={task.kind}>
	<header>
		<Pill {tone} testid="task-status-{task.id}">{statusLabel(task)}</Pill>
		<h3 title={task.title}>
			<a class="open" href="/tasks/{task.id}" data-testid="task-open-{task.id}">{task.title}</a>
		</h3>
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
				<span class="chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
				{task.steps.length} step{task.steps.length === 1 ? '' : 's'}
				{#if step && !open}<span class="now">— {step.title}</span>{/if}
			</button>
		{:else}
			<span class="disclose flat">no steps reported</span>
		{/if}

		<span class="actions">
			{#if canCancel(task) && onCancel}
				<Button testid="task-cancel-{task.id}" disabled={busy} onclick={() => onCancel?.(task)}>
					Cancel
				</Button>
			{/if}
			{#if canRetry(task) && onRetry}
				<!-- The button somebody presses after fixing what broke: a model
				     server that was down, "interrupted when Jarvis restarted". -->
				<Button testid="task-retry-{task.id}" disabled={busy} onclick={() => onRetry?.(task)}>
					Retry
				</Button>
			{/if}
			{#if onForget}
				<Button testid="task-forget-{task.id}" disabled={busy} onclick={() => onForget?.(task)}>
					Forget
				</Button>
			{/if}
		</span>
	</footer>

	{#if open && task.steps.length}
		<ol class="steps" data-testid="task-step-list-{task.id}">
			{#each task.steps as s, i (s.title + i)}
				<li data-status={s.status}>
					<span class="n" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
					<span class="what">{s.title}</span>
					{#if s.detail}<span class="say">{s.detail}</span>{/if}
					<span class="state">{s.status}</span>
				</li>
			{/each}
		</ol>
	{/if}
</article>

<style>
	.task {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
		padding: var(--jv-space-4);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
	}
	/* The state, said down the edge: the one rule this card draws in colour. */
	.task[data-status='running'] {
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.task[data-status='blocked'] {
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-warn);
	}
	.task[data-status='error'] {
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-danger);
	}
	header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-3);
	}
	h3 {
		margin: 0;
		font-size: var(--jv-fs-md);
		font-weight: var(--jv-weight-body);
		color: var(--jv-text-bright);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.open {
		color: inherit;
		text-decoration: none;
		border-bottom: 1px solid transparent;
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.open:hover {
		border-bottom-color: var(--jv-accent);
	}
	.open:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.meta {
		display: flex;
		gap: var(--jv-space-3);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
	}
	.kind {
		letter-spacing: var(--jv-track-tight);
		text-transform: uppercase;
	}
	.detail {
		margin: 0;
		font-size: var(--jv-fs-sm);
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
		gap: var(--jv-space-3);
	}
	/* Prose, not chrome: "5 steps — wiring the socket" reads as a sentence, and
	   a test reads it with innerText, which a text-transform would rewrite. */
	.disclose {
		display: inline-flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		background: none;
		border: 0;
		padding: 0;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		cursor: pointer;
		text-align: left;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.disclose.flat {
		cursor: default;
		color: var(--jv-text-faint);
	}
	.disclose:hover:not(.flat) {
		color: var(--jv-text-bright);
	}
	.disclose:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.chevron {
		color: var(--jv-text-faint);
	}
	.now {
		color: var(--jv-text-faint);
	}
	.actions {
		display: flex;
		gap: var(--jv-space-2);
		flex: 0 0 auto;
	}
	.steps {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		border-top: 1px solid var(--jv-line-hair);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.steps li {
		display: grid;
		grid-template-columns: auto minmax(0, auto) minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-2);
		border-bottom: 1px solid var(--jv-line-hair);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.steps li:last-child {
		border-bottom: 0;
	}
	.n,
	.state {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.steps li[data-status='running'] {
		color: var(--jv-text-bright);
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.steps li[data-status='running'] .n,
	.steps li[data-status='running'] .state {
		color: var(--jv-accent);
	}
	.steps li[data-status='done'] .n {
		color: var(--jv-ok);
	}
	.steps li[data-status='error'] .n,
	.steps li[data-status='error'] .say,
	.steps li[data-status='error'] .state {
		color: var(--jv-danger-text);
	}
	.steps li[data-status='blocked'] .n,
	.steps li[data-status='blocked'] .state {
		color: var(--jv-warn);
	}
	.say {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	@media (max-width: 640px) {
		header {
			grid-template-columns: auto minmax(0, 1fr);
		}
		.meta {
			grid-column: 1 / -1;
		}
	}
</style>
