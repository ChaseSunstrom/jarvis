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
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import ScheduledJobs from '$lib/components/ScheduledJobs.svelte';
	import TaskCard from '$lib/components/TaskCard.svelte';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported, type Subscription } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';
	import { toasts } from '$lib/toast';
	import { Button, EmptyState, ScreenState } from '$lib/ui';
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
<ScheduledJobs {conn} />

{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

<div class="toolbar">
	<label class="jv-sr-only" for="task-filter">Filter tasks</label>
	<input
		id="task-filter"
		type="text"
		placeholder="filter by title, kind or status  ( / )"
		data-testid="filter"
		data-jv-filter
		bind:value={filter}
	/>
	{#if filter}
		<Button testid="clear-filter" onclick={() => (filter = '')}>
			CLEAR
		</Button>
	{/if}
	{#if over.length}
		<Button testid="clear-finished" disabled={clearing} onclick={clearFinished}>
			CLEAR FINISHED
		</Button>
	{/if}
</div>

{#if loading}
	<Skeleton rows={3} />
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
			<h2 id="tasks-live">RUNNING</h2>
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
			<h2 id="tasks-over">FINISHED</h2>
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
	h1 {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-lg);
		letter-spacing: var(--jv-track-logo);
		color: var(--jv-text-bright);
		margin: 0 0 var(--jv-space-1);
	}
	h2 {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-faint);
		margin: var(--jv-space-4) 0 var(--jv-space-2);
	}
	.lede {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		margin: 0 0 var(--jv-space-3);
	}
	.toolbar {
		display: flex;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-3);
	}
	.toolbar input {
		flex: 1 1 auto;
		min-width: 0;
	}
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.err {
		color: var(--jv-danger-text);
		font-size: var(--jv-fs-xs);
		margin: 0 0 var(--jv-space-2);
	}
	.notice {
		color: var(--jv-warn);
		font-size: var(--jv-fs-xs);
		margin: 0 0 var(--jv-space-2);
	}
</style>
