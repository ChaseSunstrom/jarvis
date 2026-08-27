<script lang="ts">
	/**
	 * One task, live.
	 *
	 * The list says how far a task has got. This says what it is doing: the plan
	 * with the current step marked, every tool call as it is made, the output as
	 * it is printed, and a timeline of everything that happened — including what
	 * happened before this page was open, which is replayed from the task's own
	 * log.
	 *
	 * Approve/cancel live here too, because the moment somebody wants to stop a
	 * job is the moment they are watching it.
	 *
	 * On Reactor II it is the task view: the task as a reactor in the middle —
	 * blades grouped into the plan's steps, the level arc at its progress, the
	 * step and the percentage in the lens — the plan and the tool calls at the
	 * left, the output at the right, everything else below, and a held action
	 * as a bar with the warning rule down its edge.
	 */
	import { onDestroy, onMount } from 'svelte';
	import { page } from '$app/state';
	import { openConnection, type Connection, type ConnectionStatus } from '$lib/connection';
	import {
		TASK_EVENTS,
		currentStep,
		describeTask,
		elapsed,
		percent,
		statusLabel,
		type TaskRow,
		toTaskRow
	} from '$lib/tasks';
	import {
		TASK_ACTIVITY_EVENTS,
		applyActivityEvent,
		applyChildEvent,
		describeArguments,
		emptyActivity,
		hasRunningCall,
		type Activity
	} from '$lib/taskEvents';
	import { Button, Panel, Pill, ProgressRing, Row, ScreenState } from '$lib/ui';
	import TaskOutput from '$lib/components/TaskOutput.svelte';
	import TaskTimeline from '$lib/components/TaskTimeline.svelte';
	import CodeDiff from '$lib/components/CodeDiff.svelte';
	import type { CodeResult } from '$lib/code';
	import {
		describeTrace,
		duration,
		spanTone,
		spansOf,
		timeSplit,
		tokens,
		type Trace
	} from '$lib/trace';

	const taskId = $derived(page.params.id ?? '');

	let conn = $state<Connection | null>(null);
	let status = $state<ConnectionStatus>('connecting');
	let redialling = $state(false);
	let err = $state('');
	let loading = $state(true);
	let task = $state<TaskRow | null>(null);
	let activity = $state<Activity>(emptyActivity(''));
	let cancelling = $state(false);
	let said = $state('');
	/** A coding job's branch, commits and diff. Null for every other kind. */
	let code = $state<CodeResult | null>(null);
	let answering = $state('');
	/** Every step this task took and what each cost. Null when tracing is off. */
	let trace = $state<Trace | null>(null);
	let traceOpen = $state(false);

	let screen = $derived<'ready' | 'error' | 'offline' | 'loading' | 'empty'>(
		status === 'closed' || status === 'error'
			? 'offline'
			: err
				? 'error'
				: loading
					? 'loading'
					: task
						? 'ready'
						: 'empty'
	);

	async function connect() {
		redialling = true;
		try {
			conn?.close();
			const link = await openConnection({ onStatus: (s) => (status = s) });
			conn = link;
			activity = emptyActivity(taskId);

			// Subscribe BEFORE loading, or an event fired between the two is lost —
			// which on a fast job is most of them.
			for (const event of [...TASK_EVENTS, ...TASK_ACTIVITY_EVENTS]) {
				await link.client.subscribeEvents((bus) => {
					const data = (bus?.data ?? {}) as { task?: unknown };
					if ((TASK_ACTIVITY_EVENTS as readonly string[]).includes(event)) {
						activity = applyActivityEvent(activity, event, bus?.data);
						return;
					}
					const row = toTaskRow(data.task);
					if (row?.id === taskId) task = row;
					// A subagent's own updates arrive as ordinary task events; the
					// tree has to follow them or a child sits at "queued" forever.
					else if (row) activity = applyChildEvent(activity, bus?.data);
				}, event);
			}

			task = await link.client.getTask(taskId);
			// What happened before this page was open.
			activity = { ...activity, log: await link.client.taskLog(taskId) };
			await loadCode();
			await loadChildren();
			await loadTrace();
			err = '';
		} catch (error) {
			err = error instanceof Error ? error.message : String(error);
		} finally {
			loading = false;
			redialling = false;
		}
	}

	/**
	 * Subagents that were spawned before this page was open.
	 *
	 * The child event only reaches a page that is already watching, and a
	 * fan-out is over in twenty seconds — so arriving late is the normal case,
	 * not the exception.
	 */
	async function loadChildren() {
		if (!conn) return;
		try {
			const all = await conn.client.listTasks();
			for (const row of all) {
				if (row.parent_id === taskId) activity = applyChildEvent(activity, { task: row });
			}
		} catch {
			/* the tree is an extra; the page is still the page without it */
		}
	}

	/**
	 * The trace for this task: what ran, in order, and what it cost.
	 *
	 * Asked for by task id — a task knows its own id and nothing about the
	 * context tree traces are keyed on, so the lookup is the server's. An
	 * install with `observability:` unset answers null, and the panel says so
	 * rather than showing an error: not recording is a choice.
	 */
	async function loadTrace() {
		if (!conn) return;
		try {
			trace = await conn.client.getTrace(taskId);
		} catch {
			trace = null;
		}
	}

	/**
	 * The job's branch, commits and diff, once there are any.
	 *
	 * Only for `code` tasks and only when the job has finished: the record is
	 * written when the run ends, and asking earlier gets a `not_found` that is
	 * not an error, only an "of course not yet".
	 */
	async function loadCode() {
		if (!conn || task?.kind !== 'code') return;
		try {
			code = await conn.client.getCodeResult(taskId);
		} catch {
			code = null;
		}
	}

	/**
	 * Say yes or no to something the job is waiting on.
	 *
	 * The job is BLOCKED while this sits here — it is not a notification, it is
	 * the thing standing between a diff and a commit — so the buttons live on
	 * the job rather than only in the global approval banner.
	 */
	async function answer(requestId: string, approved: boolean) {
		if (!conn) return;
		answering = requestId;
		try {
			await conn.client.resolveApproval(requestId, approved);
			said = approved ? 'Approved.' : 'Declined — it will not retry.';
		} catch (error) {
			said = error instanceof Error ? error.message : String(error);
		} finally {
			answering = '';
		}
	}

	let retrying = $state(false);
	async function retry() {
		if (!conn || !task) return;
		retrying = true;
		try {
			const result = await conn.client.retryTask(taskId);
			said = result?.queued ? 'Back on the queue.' : 'It was not retried.';
		} catch (error) {
			said = error instanceof Error ? error.message : String(error);
		} finally {
			retrying = false;
		}
	}

	async function cancel() {
		if (!conn || !task) return;
		cancelling = true;
		try {
			const result = await conn.client.cancelTask(taskId);
			said = result?.note || result?.reason || 'Asked it to stop at its next safe point.';
		} catch (error) {
			said = error instanceof Error ? error.message : String(error);
		} finally {
			cancelling = false;
		}
	}

	onMount(connect);
	onDestroy(() => conn?.close());

	const live = $derived(!!task && !task.finished);
	// A finished coding job that has not been fetched yet: the run record is
	// written as the task closes, so the page that watched it live has to ask
	// once more at the end.
	$effect(() => {
		if (task?.kind === 'code' && task.finished && !code) void loadCode();
	});

	// --- the ring ------------------------------------------------------------
	const stepNow = $derived(task ? currentStep(task) : null);
	const stepIndex = $derived(task && stepNow ? task.steps.indexOf(stepNow) : -1);
	const runningSteps = $derived(task ? task.steps.filter((s) => s.status === 'running').length : 0);
	/** The level: the server's fraction, else the plan's done/total. */
	const ringPercent = $derived(
		task
			? typeof task.fraction === 'number'
				? percent(task)
				: task.total_steps
					? Math.round((task.done_steps / task.total_steps) * 100)
					: task.finished && task.status === 'done'
						? 100
						: null
			: null
	);
	const ringState = $derived<'idle' | 'listening' | 'thinking' | 'speaking' | 'error'>(
		!task
			? 'idle'
			: task.status === 'error'
				? 'error'
				: task.status === 'running'
					? 'listening'
					: task.status === 'blocked'
						? 'thinking'
						: 'idle'
	);
	const stepCaption = $derived(
		task && stepNow && stepIndex >= 0
			? `step ${stepIndex + 1} of ${task.total_steps || task.steps.length} · ${stepNow.status}`
			: task
				? statusLabel(task).toLowerCase()
				: ''
	);
	const tone = $derived(
		!task
			? 'neutral'
			: task.status === 'running'
				? 'live'
				: task.status === 'blocked'
					? 'warn'
					: task.status === 'error'
						? 'danger'
						: task.status === 'done'
							? 'ok'
							: 'neutral'
	);

	/** The duration a step took, from the log's step entries, when it has one. */
	function stepCost(index: number): string {
		const marks = activity.log.filter((entry) => entry.kind === 'step');
		const at = marks[index]?.at;
		const next = marks[index + 1]?.at;
		if (!at) return '—';
		const end = next ?? (task?.finished ? task.updated : Date.now() / 1000);
		const seconds = Math.max(0, Math.round(end - at));
		if (seconds < 60) return `${seconds} s`;
		return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
	}
</script>

<svelte:head><title>Jarvis · {task?.title ?? 'Task'}</title></svelte:head>

<div class="eyebrow">
	<span class="kind" data-testid="task-detail">Task{task ? ` · ${task.kind}` : ''}</span>
	{#if task}
		<Pill {tone}>{statusLabel(task)}</Pill>
		{#if task.source}<Pill>from {task.source}</Pill>{/if}
	{/if}
	<span class="lede" data-testid="task-lede" data-redialling={redialling}>
		{task ? `${task.kind} · ${task.status}` : taskId} · link {status}
	</span>
</div>

<ScreenState
	status={screen}
	rows={4}
	errorTitle="Could not load this task"
	errorDetail={err}
	emptyTitle="No such task"
	emptyBody="It may have been forgotten, or fallen off the end of the list."
	onretry={connect}
	onreconnect={connect}
	busy={redialling}
	errorTestid="error"
	emptyTestid="task-missing"
>
	{#snippet children()}
		{#if task}
			{@const it = task}
			<div class="view">
				<!-- The instrument: the plan as blades, the progress as the arc. -->
				<div class="stage">
					<ProgressRing
						size={460}
						fluid
						done={it.done_steps}
						running={runningSteps}
						total={it.total_steps || it.steps.length}
						percent={ringPercent}
						step={stepCaption}
						title={stepNow?.title ?? it.title}
						elapsed="{it.kind} · {elapsed(it)}{live ? ' elapsed' : ''}"
						state={ringState}
						testid="task-ring"
					/>
					<h1 class="title" title={it.title}>{it.title}</h1>
					<p class="say" data-testid="task-describe">{describeTask(it)}</p>
				</div>

				<div class="left">
					<Panel title="Plan" meta={it.total_steps ? `${it.done_steps} of ${it.total_steps}` : 'the model reports progress without steps'} testid="task-plan">
						{#snippet children()}
							{#if it.steps.length}
								<ol class="plan">
									{#each it.steps as step, i (i)}
										<li
											data-status={step.status}
											class:now={step.status === 'running'}
											data-testid="task-step-{i}"
										>
											<i>{String(i + 1).padStart(2, '0')}</i>
											<span class="what">{step.title}</span>
											<span class="ms">{step.status === 'done' || step.status === 'running' ? stepCost(i) : step.status === 'queued' ? '—' : step.status}</span>
										</li>
									{/each}
								</ol>
							{:else}
								<p class="none">No plan: this task reports progress without steps.</p>
							{/if}
						{/snippet}
					</Panel>

					<Panel
						title="Tool calls"
						meta={hasRunningCall(activity) ? `live · ${activity.calls.length}` : `${activity.calls.length}`}
						live={hasRunningCall(activity)}
						testid="task-calls"
					>
						{#snippet children()}
							{#if activity.calls.length}
								<ul class="calls">
									{#each activity.calls as call (call.callId + call.startedAt)}
										<li
											class:running={call.ok === undefined}
											class:failed={call.ok === false}
											data-testid="task-call-{call.name}"
											data-state={call.ok === undefined ? 'running' : call.ok ? 'ok' : 'failed'}
										>
											<i aria-hidden="true"></i>
											<b>{call.name}</b>
											<span class="args">{describeArguments(call)}</span>
											{#if call.ok === undefined}
												<em class="live">running</em>
											{:else if call.ok}
												<em class="ok">ok</em><span class="ms">· {call.durationMs ?? 0} ms</span>
											{:else}
												<em class="bad">{call.error || 'failed'}</em>
											{/if}
										</li>
									{/each}
								</ul>
							{:else}
								<p class="none">No tool calls yet.</p>
							{/if}
						{/snippet}
					</Panel>
				</div>

				<div class="right">
					<Panel title="Output" meta="{activity.output.length} chunk{activity.output.length === 1 ? '' : 's'}" testid="task-output-panel">
						{#snippet children()}
							<TaskOutput chunks={activity.output} live={live && activity.output.length > 0} />
						{/snippet}
					</Panel>
				</div>

				{#if activity.held.length}
					<div class="held-list">
						{#each activity.held as held (held.requestId)}
							<section class="held" data-testid="task-approval">
								<div class="what">
									<div class="lbl">Held · {held.kind} · asks before it runs</div>
									<div class="q">{held.summary}</div>
									{#if held.detail}<pre class="args">{held.detail}</pre>{/if}
								</div>
								<Button
									variant="primary"
									disabled={answering === held.requestId}
									onclick={() => answer(held.requestId, true)}
									testid="approve-held">Approve</Button
								>
								<Button
									disabled={answering === held.requestId}
									onclick={() => answer(held.requestId, false)}
									testid="deny-held">Decline</Button
								>
							</section>
						{/each}
					</div>
				{/if}

				<div class="below">
					<Panel title="Timeline" meta={live ? 'live' : 'complete'} live={live} testid="task-timeline-panel">
						{#snippet children()}
							<TaskTimeline entries={activity.log} startedAt={it.created} {live} />
						{/snippet}
					</Panel>

					<Panel title="Trace" meta={trace ? duration(trace.ms) : 'not recorded'} testid="task-trace">
						{#snippet children()}
							{#if trace}
								<Row label="What it cost" value={describeTrace(trace)} testid="trace-summary" />
								<Row
									label="Where the time went"
									value={`model ${timeSplit(trace).model}% · tools ${timeSplit(trace).tools}% · waiting ${timeSplit(trace).other}%`}
									testid="trace-split"
								/>
								<Row
									label="Tokens"
									value={`${tokens(trace.prompt_tokens)} in · ${tokens(trace.completion_tokens)} out`}
									testid="trace-tokens"
								/>
								<div class="disclose">
									<Button
										testid="trace-toggle"
										aria-expanded={traceOpen}
										onclick={() => (traceOpen = !traceOpen)}
									>
										{traceOpen ? '▾' : '▸'} {spansOf(trace).length} step{spansOf(trace).length === 1 ? '' : 's'}
									</Button>
								</div>
								{#if traceOpen}
									{#each spansOf(trace) as span, i (i)}
										<Row label={`${span.kind} · ${span.name}`} testid="trace-span-{i}">
											{#snippet children()}
												<span class="span">
													<Pill tone={spanTone(span)}>{duration(span.ms)}</Pill>
													{#if span.error}<span class="err">{span.error}</span>{/if}
												</span>
											{/snippet}
										</Row>
									{/each}
								{/if}
							{:else}
								<Row
									label="Not recorded"
									value="set `observability:` in configuration.yaml to trace what the agent does"
									testid="trace-off"
								/>
							{/if}
						{/snippet}
					</Panel>

					{#if activity.children.length}
						<Panel title="Specialists" meta={`${activity.children.length}`} testid="task-specialists">
							{#snippet children()}
								<ul class="tree" data-testid="task-children">
									{#each activity.children as child (child.id)}
										<li>
											<Row>
												{#snippet children()}
													<span class="span">
														<Pill
															tone={child.status === 'error'
																? 'danger'
																: child.status === 'done'
																	? 'ok'
																	: 'live'}>{child.agent || 'agent'}</Pill
														>
														<span class="grow">{child.title}</span>
														<span class="stat">{child.status}</span>
													</span>
												{/snippet}
											</Row>
											{#if child.result}
												<p class="finding">{child.result}</p>
											{/if}
										</li>
									{/each}
								</ul>
							{/snippet}
						</Panel>
					{/if}

					{#if code}
						{@const job = code}
						<Panel title="Branch" meta={job.branch} testid="task-branch">
							{#snippet children()}
								{#if job.commits?.length}
									<ul class="commits" data-testid="task-commits">
										{#each job.commits as commit (commit.sha)}
											<li>
												<code>{commit.sha}</code>
												<span>{commit.message}</span>
												<span class="stat">{commit.stat}</span>
											</li>
										{/each}
									</ul>
								{:else}
									<p class="none">Nothing was committed — the job changed no files.</p>
								{/if}
								{#if job.diff}
									<CodeDiff diff={job.diff} stat={job.diff_stat} />
								{/if}
							{/snippet}
						</Panel>
					{/if}

					{#if it.result}
						<Panel title="Result" testid="task-result-panel">
							{#snippet children()}
								<pre class="result" data-testid="task-result">{it.result}</pre>
							{/snippet}
						</Panel>
					{/if}
				</div>

				<div class="actions">
					{#if said}<p class="said" role="status" data-testid="task-said">{said}</p>{/if}
					<Button
						onclick={retry}
						disabled={retrying || !it.finished || (it.status !== 'error' && it.status !== 'cancelled')}
						title={!it.finished
							? 'It is still running'
							: it.status !== 'error' && it.status !== 'cancelled'
								? 'Only a task that failed or was cancelled is retried'
								: 'Put it back on the queue'}
						testid="task-retry">Retry</Button
					>
					<Button
						onclick={cancel}
						disabled={cancelling || it.finished}
						title={it.finished
							? 'This task has already finished'
							: 'Ask the worker to stop at its next safe point'}
						testid="task-cancel">Cancel</Button
					>
					<Button onclick={() => history.back()} testid="task-back">Back</Button>
				</div>
			</div>
		{/if}
	{/snippet}
</ScreenState>

<style>
	.eyebrow {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
		margin-bottom: var(--jv-space-4);
	}
	.kind {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.lede {
		margin-left: auto;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}

	/*
	 * The task view. The instrument in the middle, the plan and the calls at
	 * its left, the output at its right; the rest in two columns below; a
	 * held action across the full width, above the rest, because it is the
	 * one thing waiting on the person reading.
	 */
	.view {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1fr);
		grid-template-areas:
			'left stage right'
			'held held held'
			'below below below'
			'actions actions actions';
		gap: var(--jv-space-5) var(--jv-space-5);
		align-items: start;
	}
	.stage {
		grid-area: stage;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--jv-space-3);
		text-align: center;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.stage :global(.ring) {
		width: min(100%, calc(var(--jv-space-7) * 9.5));
	}
	.title {
		margin: var(--jv-space-2) 0 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-lg);
		line-height: 1.3;
		color: var(--jv-text-bright);
		max-width: 40ch;
	}
	.say {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
		max-width: 48ch;
		overflow-wrap: anywhere;
	}
	.left,
	.right {
		display: grid;
		gap: var(--jv-space-4);
		min-width: 0;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.left {
		grid-area: left;
	}
	.right {
		grid-area: right;
	}
	/* The panels' bodies are lists; the padding belongs to the rows. */
	.left :global(.body),
	.right :global(.body),
	.below :global(.body) {
		padding: 0;
	}
	.plan {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.plan li {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.plan li:last-child {
		border-bottom: 0;
	}
	.plan i,
	.plan .ms {
		font-style: normal;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.plan li[data-status='done'] i {
		color: var(--jv-ok);
	}
	.plan li[data-status='error'] i,
	.plan li[data-status='error'] .ms {
		color: var(--jv-danger-text);
	}
	.plan li[data-status='blocked'] i {
		color: var(--jv-warn);
	}
	.plan li.now {
		color: var(--jv-text-bright);
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.plan li.now i,
	.plan li.now .ms {
		color: var(--jv-accent);
	}
	.none {
		margin: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	/* One tool call, one line — the same shape as the library's CallLine; drawn
	   here because a running call says the word "running", which is what the
	   person watching is asking. */
	.calls {
		list-style: none;
		margin: 0;
		padding: var(--jv-space-2) var(--jv-space-4);
	}
	.calls li {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		min-width: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.9;
		color: var(--jv-text-faint);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.calls i {
		flex: none;
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: 50%;
		background: var(--jv-ok);
		align-self: center;
	}
	.calls li.running i {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.calls li.failed i {
		background: var(--jv-danger);
	}
	.calls b {
		font-weight: var(--jv-weight-body);
		color: var(--jv-text-dim);
	}
	.calls li.running b {
		color: var(--jv-text-bright);
	}
	.calls .args {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}
	.calls em {
		font-style: normal;
	}
	.calls .ok {
		color: var(--jv-ok);
	}
	.calls .bad {
		color: var(--jv-danger-text);
	}
	.calls .live {
		color: var(--jv-accent);
	}
	.calls .ms {
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	/* A held action: the bar with the warning rule down its edge. */
	.held-list {
		grid-area: held;
		display: grid;
		gap: var(--jv-space-3);
	}
	.held {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		align-items: center;
		gap: var(--jv-space-4);
		padding: var(--jv-space-3) var(--jv-space-3) var(--jv-space-3) var(--jv-space-5);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-warn);
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.held .what {
		min-width: 0;
	}
	.lbl {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-warn);
	}
	.q {
		margin: var(--jv-space-1) 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		overflow-wrap: anywhere;
	}
	.args {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.6;
		color: var(--jv-text-faint);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		max-height: calc(var(--jv-space-7) * 3);
		overflow-y: auto;
	}

	.below {
		grid-area: below;
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: var(--jv-space-4);
		align-items: start;
	}
	.disclose {
		padding: var(--jv-space-2) var(--jv-space-4);
	}
	.span {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		min-width: 0;
	}
	.err {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-danger-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 28ch;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.stat {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.tree {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.finding {
		margin: 0;
		padding: 0 var(--jv-space-4) var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.commits {
		margin: 0;
		padding: var(--jv-space-2) var(--jv-space-4);
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.commits li {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.commits code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-accent);
	}
	.below :global(.diff) {
		border: 0;
		border-radius: 0;
	}
	.result {
		margin: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.7;
		color: var(--jv-text);
		background: var(--jv-surface-sunken);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	.actions {
		grid-area: actions;
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-2);
	}
	.said {
		margin: 0 auto 0 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}

	@media (max-width: 1100px) {
		.view {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
			grid-template-areas:
				'stage stage'
				'held held'
				'left right'
				'below below'
				'actions actions';
		}
		.stage :global(.ring) {
			width: min(100%, calc(var(--jv-space-7) * 7));
		}
	}
	@media (max-width: 720px) {
		.view {
			grid-template-columns: minmax(0, 1fr);
			grid-template-areas:
				'stage'
				'held'
				'left'
				'right'
				'below'
				'actions';
			gap: var(--jv-space-4);
		}
		.stage :global(.ring) {
			width: min(100%, calc(var(--jv-space-7) * 5.5));
		}
		.below {
			grid-template-columns: minmax(0, 1fr);
		}
		.held {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
