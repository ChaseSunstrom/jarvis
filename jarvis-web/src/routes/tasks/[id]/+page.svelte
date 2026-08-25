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
	 */
	import { onDestroy, onMount } from 'svelte';
	import { page } from '$app/state';
	import { openConnection, type Connection, type ConnectionStatus } from '$lib/connection';
	import { TASK_EVENTS, describeTask, type TaskRow, toTaskRow } from '$lib/tasks';
	import {
		TASK_ACTIVITY_EVENTS,
		applyActivityEvent,
		applyChildEvent,
		describeArguments,
		emptyActivity,
		hasRunningCall,
		type Activity
	} from '$lib/taskEvents';
	import { Button, Panel, Pill, Row, ScreenState } from '$lib/ui';
	import TaskBar from '$lib/components/TaskBar.svelte';
	import TaskOutput from '$lib/components/TaskOutput.svelte';
	import TaskTimeline from '$lib/components/TaskTimeline.svelte';
	import CodeDiff from '$lib/components/CodeDiff.svelte';
	import type { CodeResult } from '$lib/code';

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
			err = '';
		} catch (error) {
			err = error instanceof Error ? error.message : String(error);
		} finally {
			loading = false;
			redialling = false;
		}
	}

	/**
	 * The job's branch, commits and diff, once there are any.
	 *
	 * Only for `code` tasks and only when the job has finished: the record is
	 * written when the run ends, and asking earlier gets a `not_found` that is
	 * not an error, only an "of course not yet".
	 */
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
</script>

<svelte:head><title>Jarvis · {task?.title ?? 'Task'}</title></svelte:head>

<h1 data-testid="task-detail">TASK</h1>
<p class="lede" data-testid="task-lede" data-redialling={redialling}>
	{task ? `${task.kind} · ${task.status}` : taskId} · link {status}
</p>

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
			<Panel title={it.title} meta={describeTask(task)} live={live}>
				{#snippet children()}
					<TaskBar task={it} />
					<div class="facts">
						<Pill tone={it.finished ? (it.status === 'done' ? 'ok' : 'danger') : 'live'}>
							{it.status}
						</Pill>
						<Pill>{it.kind}</Pill>
						{#if it.source}<Pill>from {it.source}</Pill>{/if}
						<span class="grow"></span>
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
					{#if said}<p class="said" role="status" data-testid="task-said">{said}</p>{/if}
				{/snippet}
			</Panel>

			<Panel title="Plan" meta={`${it.done_steps} of ${it.total_steps}`}>
				{#snippet children()}
					{#each it.steps as step, i (i)}
						<Row
							label={step.title}
							value={step.status}
							current={step.status === 'running'}
							testid="task-step-{i}"
						/>
					{:else}
						<Row label="No plan" value="this task reports progress without steps" />
					{/each}
				{/snippet}
			</Panel>

			<Panel
				title="Tool calls"
				meta={hasRunningCall(activity) ? 'live' : `${activity.calls.length}`}
				live={hasRunningCall(activity)}
			>
				{#snippet children()}
					{#each activity.calls as call (call.callId + call.startedAt)}
						<Row
							label={call.name}
							current={call.ok === undefined}
							testid="task-call-{call.name}"
						>
							{#snippet children()}
								<span class="call">
									<span class="args">{describeArguments(call)}</span>
									{#if call.ok === undefined}
										<Pill tone="live">running</Pill>
									{:else if call.ok}
										<Pill tone="ok">{call.durationMs ?? 0} ms</Pill>
									{:else}
										<Pill tone="danger">{call.error || 'failed'}</Pill>
									{/if}
								</span>
							{/snippet}
						</Row>
					{:else}
						<Row label="No tool calls yet" value="—" />
					{/each}
				{/snippet}
			</Panel>

			<Panel title="Output" meta={`${activity.output.length}`}>
				{#snippet children()}
					<TaskOutput chunks={activity.output} />
				{/snippet}
			</Panel>

			<Panel title="Timeline" meta={live ? 'live' : 'complete'} live={live}>
				{#snippet children()}
					<TaskTimeline entries={activity.log} startedAt={it.created} {live} />
				{/snippet}
			</Panel>

			{#if activity.held.length}
				<Panel title="Waiting for you" meta={`${activity.held.length}`}>
					{#snippet children()}
						{#each activity.held as held (held.requestId)}
							<div class="held" data-testid="task-approval">
								<Row>
									{#snippet children()}
										<Pill tone="warn">{held.kind}</Pill>
										<span class="grow">{held.summary}</span>
										<Button
											variant="primary"
											disabled={answering === held.requestId}
											onclick={() => answer(held.requestId, true)}
											testid="approve-held">APPROVE</Button
										>
										<Button
											disabled={answering === held.requestId}
											onclick={() => answer(held.requestId, false)}
											testid="deny-held">DECLINE</Button
										>
									{/snippet}
								</Row>
								{#if held.detail}
									<pre class="detail">{held.detail}</pre>
								{/if}
							</div>
						{/each}
					{/snippet}
				</Panel>
			{/if}

			{#if activity.children.length}
				<Panel title="Specialists" meta={`${activity.children.length}`}>
					{#snippet children()}
						<ul class="tree" data-testid="task-children">
							{#each activity.children as child (child.id)}
								<li>
									<Row>
										{#snippet children()}
											<Pill
												tone={child.status === 'error'
													? 'danger'
													: child.status === 'done'
														? 'ok'
														: 'live'}>{child.agent || 'agent'}</Pill
											>
											<span class="grow">{child.title}</span>
											<span class="stat">{child.status}</span>
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
				<Panel title="Branch" meta={job.branch}>
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
							<p class="said">Nothing was committed — the job changed no files.</p>
						{/if}
						{#if job.diff}
							<CodeDiff diff={job.diff} stat={job.diff_stat} />
						{/if}
					{/snippet}
				</Panel>
			{/if}

			{#if it.result}
				<Panel title="Result">
					{#snippet children()}
						<pre class="result" data-testid="task-result">{it.result}</pre>
					{/snippet}
				</Panel>
			{/if}
		{/if}
	{/snippet}
</ScreenState>

<style>
	.facts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
		margin-top: var(--jv-space-3);
	}
	.grow {
		flex: 1;
	}
	.said {
		margin: var(--jv-space-3) 0 0;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
	}
	.call {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.args {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 28ch;
	}
	.held {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
		padding: var(--jv-space-2) 0;
	}
	.detail {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		max-height: var(--jv-measure-log);
		overflow-y: auto;
	}
	.tree {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
	}
	.finding {
		margin: var(--jv-space-1) 0 0;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.commits {
		margin: 0 0 var(--jv-space-3);
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.commits li {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		font-size: var(--jv-fs-2xs);
	}
	.commits code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-accent);
	}
	.stat {
		color: var(--jv-text-faint);
	}
	.result {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.7;
		color: var(--jv-text);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
