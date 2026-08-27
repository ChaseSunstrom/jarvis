<script lang="ts">
	/**
	 * Long work, visible from wherever you are standing.
	 *
	 * Same reasoning as the approvals banner and the tool strip above it: a
	 * research run or a scheduled job keeps going while you navigate, so what it
	 * is doing has to be on screen on every route — the voice screen included,
	 * where there is no console chrome at all and no other way to find out.
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
	/**
	 * Rows the person opened. A brief by default (M111): the operator's
	 * report of 27 Aug 2026 — "the tasks taking up the entire screen on the
	 * voice tab and causing a scroll; it should just be a simple brief with
	 * the task, that I can then expand". One line per task; the bar, the
	 * sentence and the steps are behind a click, and the list scrolls inside
	 * itself past a few rows rather than pushing the page.
	 */
	let expanded = $state<Set<string>>(new Set());
	function toggle(id: string): void {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}
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
			<span class="label">Tasks</span>
			{#if line}<span class="line" data-testid="task-dock-summary">{line}</span>{/if}
			<a class="more" href="/tasks" data-testid="task-dock-link">All</a>
		</header>
		<ul data-testid="task-dock-list">
			{#each shown as task (task.id)}
				{@const open = expanded.has(task.id)}
				<li data-testid="task-dock-row-{task.id}" data-status={task.status} data-open={open}>
					<button
						type="button"
						class="brief"
						data-testid="task-dock-brief-{task.id}"
						aria-expanded={open}
						aria-controls="task-dock-detail-{task.id}"
						title={open ? 'Fold the task back to one line' : describeTask(task) || 'Show the steps'}
						onclick={() => toggle(task.id)}
					>
						<span class="chev" aria-hidden="true">{open ? '▾' : '▸'}</span>
						<span class="what">{task.title}</span>
						<span class="state">
							{#if stepCount(task)}<span class="steps">{stepCount(task)}</span>{/if}
							<span class="badge">{statusLabel(task)}</span>
						</span>
					</button>
					{#if open}
						<div class="detail" id="task-dock-detail-{task.id}" data-testid="task-dock-detail-{task.id}">
							<TaskBar {task} compact />
							<span class="say">{describeTask(task)}</span>
							{#if task.steps.length}
								<ol class="plan">
									{#each task.steps as s, i (i)}
										<li data-step-status={s.status}>{s.title}</li>
									{/each}
								</ol>
							{/if}
							<a class="open" href="/work/tasks/{task.id}" data-testid="task-dock-open-{task.id}">Open</a>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.dock {
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		margin-bottom: var(--jv-space-4);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	header {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.label {
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.line {
		flex: 1 1 auto;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-accent);
		font-variant-numeric: tabular-nums;
	}
	.more {
		margin-left: auto;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		text-decoration: none;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.more:hover {
		color: var(--jv-text-bright);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		/* Past a few rows the list scrolls inside itself: the dock sits between
		   the instrument and the exchange, and every row it grew by pushed the
		   one control on the page (the dock at the bottom) off the screen. */
		max-height: min(32vh, 18rem);
		overflow-y: auto;
	}
	li {
		display: flex;
		flex-direction: column;
		border-bottom: 1px solid var(--jv-line-hair);
		animation: jv-rise var(--jv-dur-fast) var(--jv-ease-out) both;
	}
	li:last-child {
		border-bottom: 0;
	}
	.brief {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-3);
		width: 100%;
		padding: var(--jv-space-2) var(--jv-space-4);
		background: none;
		border: 0;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.brief:hover {
		background: var(--jv-panel-raised, rgba(255, 255, 255, 0.03));
	}
	.chev {
		flex: 0 0 auto;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	.detail {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
		padding: 0 var(--jv-space-4) var(--jv-space-3) calc(var(--jv-space-4) * 2);
	}
	.plan {
		margin: 0;
		padding-left: var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.plan li {
		display: list-item;
		border: 0;
		animation: none;
	}
	.plan li[data-step-status='done'] {
		color: var(--jv-text-faint);
		text-decoration: line-through;
	}
	.plan li[data-step-status='running'] {
		color: var(--jv-accent);
	}
	.open {
		align-self: flex-start;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		text-decoration: none;
	}
	.open:hover {
		color: var(--jv-text-bright);
	}
	.what {
		flex: 1 1 auto;
		font-size: var(--jv-fs-sm);
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
		letter-spacing: var(--jv-track-tight);
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
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	li[data-status='error'] .say {
		color: var(--jv-danger-text);
	}
</style>
