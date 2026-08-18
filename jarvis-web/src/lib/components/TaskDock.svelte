<script lang="ts">
	/**
	 * Long work, visible from wherever you are standing.
	 *
	 * Same reasoning as the approvals banner and the tool strip above it: a
	 * research run or a scheduled job keeps going while you navigate, so what it
	 * is doing has to be on screen on every route — the HUD included, where
	 * there is no console chrome at all and no other way to find out.
	 *
	 * It is a GLANCE, not a page. Titles, bars, and a link to `/tasks` for the
	 * steps and the buttons. It draws nothing when nothing is happening, which
	 * is almost always.
	 *
	 * Live from the bus, not polled: `jarvis/tasks/list` runs exactly once, to
	 * catch work that began before this page loaded, and every move after that
	 * arrives as `jarvis_task_added` / `_updated` / `_removed`.
	 */
	import { onDestroy } from 'svelte';
	import TaskBar from './TaskBar.svelte';
	import type { Connection } from '$lib/connection';
	import type { BusEvent, Subscription } from '$lib/jarvisClient';
	import {
		TASK_EVENTS,
		applyTaskEvent,
		describeTask,
		dockTasks,
		mergeTaskList,
		nextLingerExpiry,
		statusLabel,
		stepCount,
		summarise,
		type TaskRow
	} from '$lib/tasks';

	let { conn }: { conn: Connection | null } = $props();

	let tasks = $state<TaskRow[]>([]);
	/** Bumped when a lingering task ages out, to force `dockTasks` to re-run. */
	let clock = $state(0);
	let expiry: ReturnType<typeof setTimeout> | null = null;

	const shown = $derived.by(() => {
		void clock;
		return dockTasks(tasks);
	});
	const line = $derived(summarise(shown));

	/*
	 * One timer at the next expiry, rather than a tick every second for ever
	 * behind an empty dock. `$effect` re-runs whenever `tasks` changes, which is
	 * exactly when the next expiry can have moved.
	 */
	$effect(() => {
		void tasks;
		if (expiry) clearTimeout(expiry);
		expiry = null;
		const left = nextLingerExpiry(tasks);
		if (left === null) return;
		expiry = setTimeout(() => {
			clock += 1;
			expiry = null;
		}, left + 50);
		(expiry as any)?.unref?.();
	});

	$effect(() => {
		const connection = conn;
		if (!connection) return;
		let disposed = false;
		const subs: Subscription[] = [];

		(async () => {
			try {
				for (const name of TASK_EVENTS) {
					subs.push(
						await connection.client.subscribeEvents((event: BusEvent) => {
							tasks = applyTaskEvent(tasks, event);
						}, name)
					);
				}
				// After subscribing, never before: a task that moved between the
				// fetch and the subscription would otherwise be missed entirely,
				// and `mergeTaskList` keeps whichever version is newer.
				tasks = mergeTaskList(tasks, await connection.client.listTasks({ active: true }));
			} catch {
				// An older jarvis-core has no task registry. An empty dock is the
				// right outcome and is not worth an error banner — the versioning
				// rule in docs/clients.md is to hide the feature, never fail open.
			}
			if (disposed) for (const sub of subs) void sub.unsubscribe();
		})();

		return () => {
			disposed = true;
			for (const sub of subs) void sub.unsubscribe();
		};
	});

	onDestroy(() => {
		if (expiry) clearTimeout(expiry);
	});
</script>

{#if shown.length}
	<section class="dock" data-testid="task-dock" aria-live="polite">
		<header>
			<span class="label">TASKS</span>
			{#if line}<span class="line" data-testid="task-dock-summary">{line}</span>{/if}
			<a class="more" href="/tasks" data-testid="task-dock-link">ALL</a>
		</header>
		<ul>
			{#each shown as task (task.id)}
				<li data-testid="task-dock-row-{task.id}" data-status={task.status}>
					<span class="top">
						<span class="what" title={task.title}>{task.title}</span>
						<span class="state">
							{#if stepCount(task)}<span class="steps">{stepCount(task)}</span>{/if}
							<span class="badge">{statusLabel(task)}</span>
						</span>
					</span>
					<TaskBar {task} compact />
					<span class="say">{describeTask(task)}</span>
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.dock {
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-panel);
		padding: var(--jv-space-3);
		margin-bottom: var(--jv-space-3);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	header {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-dim);
		margin-bottom: var(--jv-space-2);
	}
	.line {
		color: var(--jv-accent);
		flex: 1 1 auto;
		letter-spacing: 0;
		font-variant-numeric: tabular-nums;
	}
	.more {
		margin-left: auto;
		color: var(--jv-text-faint);
		text-decoration: none;
	}
	.more:hover {
		color: var(--jv-accent);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	li {
		display: flex;
		flex-direction: column;
		gap: 3px;
		animation: jv-rise var(--jv-dur-fast) var(--jv-ease-out) both;
	}
	.top {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-2);
	}
	.what {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.state {
		display: flex;
		gap: var(--jv-space-2);
		flex: 0 0 auto;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	li[data-status='running'] .badge {
		color: var(--jv-accent);
	}
	li[data-status='blocked'] .badge {
		color: var(--jv-warn);
	}
	li[data-status='done'] .badge {
		color: var(--jv-ok);
	}
	li[data-status='error'] .badge {
		color: var(--jv-danger-text);
	}
	.say {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	li[data-status='error'] .say {
		color: var(--jv-danger-text);
	}

	@media (prefers-reduced-motion: reduce) {
		.dock,
		li {
			animation: none;
		}
	}
</style>
