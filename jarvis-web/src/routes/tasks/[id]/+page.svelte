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
		describeArguments,
		emptyActivity,
		hasRunningCall,
		type Activity
	} from '$lib/taskEvents';
	import { Button, Panel, Pill, Row, ScreenState } from '$lib/ui';
	import TaskBar from '$lib/components/TaskBar.svelte';
	import TaskOutput from '$lib/components/TaskOutput.svelte';
	import TaskTimeline from '$lib/components/TaskTimeline.svelte';

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
				}, event);
			}

			task = await link.client.getTask(taskId);
			// What happened before this page was open.
			activity = { ...activity, log: await link.client.taskLog(taskId) };
			err = '';
		} catch (error) {
			err = error instanceof Error ? error.message : String(error);
		} finally {
			loading = false;
			redialling = false;
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
