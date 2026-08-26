<script lang="ts">
	/**
	 * Every long job Jarvis is running, has run, or was asked to run.
	 *
	 * The dock over every page is the glance; this is the place you come when
	 * the glance was not enough — the steps, the reasons, and the two buttons.
	 *
	 * Live over the same socket, not polled. `jarvis/tasks/list` runs once on
	 * connect and the three bus events keep it true afterwards, which is the
	 * whole reason the registry fires them.
	 *
	 * Reactor II puts the day across the top: every scheduled firing still to
	 * come and every task that ran today, on one strip, so "did my seven
	 * o'clock reminder run" is a glance and not a search.
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import ScheduledJobs from '$lib/components/ScheduledJobs.svelte';
	import TaskCard from '$lib/components/TaskCard.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported, type Subscription } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';
	import type { ScheduledJob } from '$lib/schedule';
	import { toasts } from '$lib/toast';
	import { Button, DayStrip, EmptyState, Input, ScreenState, SkeletonRows } from '$lib/ui';
	import type { DayNode } from '$lib/ui/DayStrip.svelte';
	import {
		TASK_EVENTS,
		activeTasks,
		applyTaskEvent,
		finishedTasks,
		mergeTaskList,
		summarise,
		type TaskRow
	} from '$lib/tasks';

	// `$state` because it is passed to a child — see the same note on the
	// tools page, where svelte-check caught the version that was not.
	let conn = $state<Connection | null>(null);
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let loading = $state(true);
	let tasks = $state<TaskRow[]>([]);
	let jobs = $state<ScheduledJob[]>([]);
	let filter = $state('');
	/** Ids with an action in flight, so a row's buttons go inert. */
	let busy = $state<string[]>([]);
	let clearing = $state(false);

	// The command palette and the dock can both deep-link to one task.
	let focused = $derived(page.url.searchParams.get('focus') ?? '');
	$effect(() => {
		if (focused) filter = focused;
	});

	const matching = $derived.by(() => {
		const needle = filter.trim().toLowerCase();
		if (!needle) return tasks;
		return tasks.filter(
			(t) =>
				t.id.toLowerCase().includes(needle) ||
				t.title.toLowerCase().includes(needle) ||
				t.kind.toLowerCase().includes(needle) ||
				t.status.toLowerCase().includes(needle)
		);
	});
	const live = $derived(activeTasks(matching));
	const over = $derived(finishedTasks(matching));
	const line = $derived(summarise(tasks));

	/** HH:MM, local, for the strip. */
	function clock(epochSeconds: number): string {
		const d = new Date(epochSeconds * 1000);
		return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
	}
	const MAX_DAY_NODES = 12;
	const WORD_LENGTH = 14;
	const word = (text: string) =>
		text.length > WORD_LENGTH ? `${text.slice(0, WORD_LENGTH - 1)}…` : text;

	/**
	 * The day: today's tasks and the firings still to come, in clock order.
	 *
	 * A task's dot is the state it is in; a scheduled firing is a pending dot
	 * until it mints a task, which then appears as its own. Capped, because a
	 * strip that scrolls is a list.
	 */
	const dayNodes = $derived.by((): DayNode[] => {
		const start = new Date();
		start.setHours(0, 0, 0, 0);
		const dayStart = start.getTime() / 1000;
		const dayEnd = dayStart + 86_400;
		const nodes: (DayNode & { at_s: number })[] = [];
		for (const task of tasks) {
			if (task.created < dayStart) continue;
			nodes.push({
				at_s: task.created,
				at: clock(task.created),
				label: word(task.title),
				state:
					task.status === 'running'
						? 'running'
						: task.status === 'error'
							? 'error'
							: task.status === 'done' || task.status === 'cancelled'
								? 'done'
								: 'pending',
				href: `/work/tasks/${task.id}`,
				testid: `day-task-${task.id}`
			});
		}
		for (const job of jobs) {
			if (!job.enabled || !job.next_at || job.next_at >= dayEnd) continue;
			nodes.push({
				at_s: job.next_at,
				at: clock(job.next_at),
				label: word(job.title),
				state: 'pending',
				testid: `day-job-${job.id}`
			});
		}
		return nodes
			.sort((a, b) => a.at_s - b.at_s)
			.slice(0, MAX_DAY_NODES)
			.map(({ at_s: _at, ...node }) => node);
	});

	function isBusy(id: string): boolean {
		return busy.includes(id);
	}

	async function withBusy(id: string, work: () => Promise<void>): Promise<void> {
		if (isBusy(id)) return;
		busy = [...busy, id];
		try {
			await work();
		} finally {
			busy = busy.filter((b) => b !== id);
		}
	}

	async function cancel(task: TaskRow): Promise<void> {
		if (!conn) return;
		await withBusy(task.id, async () => {
			try {
				const result = await conn!.client.cancelTask(task.id);
				// jarvis-core's registry is a record, not a scheduler: marking a
				// task cancelled is a REQUEST, and it says so. Showing a bare
				// "Cancelled" over work that may still be running would be the
				// same lie one layer up, so its note is what the toast carries.
				if (result.cancelled) {
					toasts.success(`Asked "${task.title}" to stop`, result.note ?? undefined);
				} else {
					toasts.info(`"${task.title}" was already over`, result.reason ?? undefined);
				}
			} catch (e) {
				err = describeError(e);
				toasts.error(`Could not cancel "${task.title}"`, describeError(e));
			}
		});
	}

	async function forget(task: TaskRow): Promise<void> {
		if (!conn) return;
		await withBusy(task.id, async () => {
			try {
				const gone = await conn!.client.deleteTask(task.id);
				// Optimistic, and safe: the removal event does the same thing, and
				// arriving twice is idempotent. Waiting for it instead leaves the
				// row sitting there after a click that plainly worked.
				if (gone) tasks = tasks.filter((t) => t.id !== task.id);
				if (!task.finished && gone) {
					toasts.info(`Forgot "${task.title}"`, 'it was still running — forgetting does not stop it');
				}
			} catch (e) {
				err = describeError(e);
				toasts.error(`Could not forget "${task.title}"`, describeError(e));
			}
		});
	}

	async function clearFinished(): Promise<void> {
		if (!conn || clearing) return;
		clearing = true;
		try {
			const removed = await conn.client.clearFinishedTasks();
			tasks = activeTasks(tasks);
			toasts.success(removed ? `Forgot ${removed} finished task${removed === 1 ? '' : 's'}` : 'Nothing to clear');
		} catch (e) {
			err = describeError(e);
			toasts.error('Could not clear finished tasks', describeError(e));
		} finally {
			clearing = false;
		}
	}

	// --- the socket ----------------------------------------------------------
	let disposed = false;
	let subs: Subscription[] = [];
	let redialling = $state(false);
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mine = ++dial;
		for (const sub of subs) void sub.unsubscribe();
		subs = [];
		conn?.close();
		conn = null;
		err = '';
		hint = '';
		loading = true;
		try {
			const connection = await openConnection({
				onStatus: (s) => {
					if (mine === dial) status = s;
				}
			});
			if (disposed || mine !== dial) {
				connection.close();
				return;
			}
			conn = connection;
			// Subscribe BEFORE listing. A task that moves between the two would
			// otherwise be missed for as long as the tab stays open, and
			// `mergeTaskList` keeps whichever version is newer.
			for (const name of TASK_EVENTS) {
				subs.push(
					await connection.client.subscribeEvents((event) => {
						tasks = applyTaskEvent(tasks, event);
					}, name)
				);
			}
			tasks = mergeTaskList(tasks, await connection.client.listTasks());
		} catch (e) {
			if (isUnsupported(e)) {
				// The versioning rule: an older backend hides the feature rather
				// than showing a fault. Say which, so it is not a mystery.
				hint = 'this backend has no task registry — nothing here will fill in';
			} else {
				err = describeError(e);
			}
		} finally {
			redialling = false;
			if (!disposed) loading = false;
		}
	}

	onMount(() => {
		disposed = false;
		void connect();
		return () => {
			disposed = true;
			for (const sub of subs) void sub.unsubscribe();
			subs = [];
			conn?.close();
			conn = null;
		};
	});

	// The screen's status region. Loading and empty belong to the individual
	// lists below (this page has more than one); what is page-wide is the link
	// being down and the page's own failure, and `ScreenState` owns both.
	let screen = $derived<'ready' | 'error' | 'offline'>(
		status === 'closed' || status === 'error' ? 'offline' : err ? 'error' : 'ready'
	);
</script>

<p class="lede" data-testid="tasks-lede" data-redialling={redialling}>
	{tasks.length} task{tasks.length === 1 ? '' : 's'}{line ? ` · ${line}` : ''} · live over websocket
	· link {status}
</p>

{#if dayNodes.length}
	<DayStrip nodes={dayNodes} label="Today" />
{/if}

<ScreenState
	status={screen}
	errorTitle="This page hit an error"
	errorDetail={err}
	onretry={connect}
	onreconnect={connect}
	busy={redialling}
	errorTestid="error"
/>

<!-- Above the task list, not on a page of its own: a scheduled job and the
     task it mints are the same thing at two moments, and putting them a
     navigation apart makes "did my seven o'clock reminder run?" a two-page
     question. -->
<div class="scheduled">
	<ScheduledJobs {conn} onJobs={(list) => (jobs = list)} />
</div>

{#if hint}<p class="hint" data-testid="hint">{hint}</p>{/if}

<div class="toolbar">
	<div class="filter">
		<label class="jv-sr-only" for="task-filter">Filter tasks</label>
		<Input
			bind:value={filter}
			placeholder="filter by title, kind or status  ( / )"
			testid="filter"
		/>
	</div>
	{#if filter}
		<Button testid="clear-filter" onclick={() => (filter = '')}>Clear</Button>
	{/if}
	{#if over.length}
		<Button testid="clear-finished" disabled={clearing} onclick={clearFinished}>
			Clear finished
		</Button>
	{/if}
</div>

{#if loading}
	<SkeletonRows rows={3} />
{:else if !tasks.length}
	<EmptyState
		testid="tasks-empty"
		title="Nothing running"
		body="Research runs, scheduled jobs and anything else slow enough to ask about will appear here, with where it has got to. Jarvis puts them on this list himself — there is nothing to start from the console."
	/>
{:else if !matching.length}
	<EmptyState testid="tasks-none-matching" title={`No task matches “${filter}”`} />
{:else}
	{#if live.length}
		<section aria-labelledby="tasks-live">
			<h2 id="tasks-live">Running</h2>
			<div class="stack jv-stagger" data-testid="tasks-live">
				{#each live as task, i (task.id)}
					<div style={staggerStyle(i)}>
						<TaskCard {task} busy={isBusy(task.id)} onCancel={cancel} onForget={forget} />
					</div>
				{/each}
			</div>
		</section>
	{/if}

	{#if over.length}
		<section aria-labelledby="tasks-over">
			<h2 id="tasks-over">Finished</h2>
			<div class="stack jv-stagger" data-testid="tasks-finished">
				{#each over as task, i (task.id)}
					<div style={staggerStyle(i)}>
						<TaskCard {task} busy={isBusy(task.id)} onForget={forget} />
					</div>
				{/each}
			</div>
		</section>
	{/if}
{/if}

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.scheduled {
		margin-bottom: var(--jv-space-4);
	}
	h2 {
		margin: var(--jv-space-5) 0 var(--jv-space-3);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	.hint {
		margin: 0 0 var(--jv-space-3);
		font-size: var(--jv-fs-xs);
		color: var(--jv-warn);
	}
	.toolbar {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-4);
	}
	.filter {
		flex: 1 1 auto;
		min-width: 0;
		max-width: calc(var(--jv-space-7) * 9);
	}
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
	}
</style>
