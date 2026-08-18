import { describe, expect, it } from 'vitest';
import {
	EVENT_TASK_ADDED,
	EVENT_TASK_REMOVED,
	EVENT_TASK_UPDATED,
	activeTasks,
	ago,
	applyTaskEvent,
	barAria,
	barMode,
	canCancel,
	currentStep,
	describeTask,
	dockTasks,
	elapsed,
	LINGER_FAILED_MS,
	LINGER_MS,
	finishedTasks,
	mergeTaskList,
	percent,
	statusLabel,
	nextLingerExpiry,
	stepCount,
	summarise,
	toTaskList,
	toTaskRow,
	type TaskRow
} from './tasks';

function task(over: Partial<TaskRow> = {}): TaskRow {
	return {
		id: 't1',
		kind: 'research',
		title: 'Read twelve pages',
		status: 'running',
		steps: [],
		detail: '',
		result: '',
		error: '',
		created: 1000,
		updated: 1000,
		source: '',
		open_ended: false,
		fraction: null,
		done_steps: 0,
		total_steps: 0,
		finished: false,
		...over
	};
}

function ev(type: string, t: Partial<TaskRow>) {
	return { event_type: type, data: { task: task(t) } };
}

describe('reading a task off the wire', () => {
	it('keeps a null fraction null instead of turning it into nought per cent', () => {
		// `Number(null)` is 0, and "no number" drawn as 0% is the whole bug this
		// module exists to avoid: an open-ended crawl would show an empty bar
		// that never moves rather than an honest indeterminate one.
		const row = toTaskRow({ id: 'a', title: 'x', fraction: null })!;
		expect(row.fraction).toBeNull();
		expect(barMode(row)).not.toBe('determinate');
	});

	it('keeps a real zero as a real zero', () => {
		expect(toTaskRow({ id: 'a', title: 'x', fraction: 0 })!.fraction).toBe(0);
	});

	it('refuses a record with no id rather than drawing a row that cannot be acted on', () => {
		expect(toTaskRow({ title: 'x' })).toBeNull();
		expect(toTaskRow(null)).toBeNull();
		expect(toTaskRow('nope')).toBeNull();
	});

	it('drops one bad row rather than the whole list', () => {
		const list = toTaskList({ tasks: [{ id: 'a', title: 'a' }, null, { title: 'no id' }] });
		expect(list.map((t) => t.id)).toEqual(['a']);
	});

	it('takes a bare array as well as a {tasks} envelope', () => {
		expect(toTaskList([{ id: 'a', title: 'a' }])).toHaveLength(1);
		expect(toTaskList(undefined)).toEqual([]);
	});

	it('falls back to a status it can draw rather than passing nonsense through', () => {
		expect(toTaskRow({ id: 'a', title: 'x', status: 'exploded' })!.status).toBe('queued');
	});

	it('derives finished from the status when the server did not say', () => {
		expect(toTaskRow({ id: 'a', title: 'x', status: 'error' })!.finished).toBe(true);
		expect(toTaskRow({ id: 'a', title: 'x', status: 'blocked' })!.finished).toBe(false);
	});

	it('keeps steps and skips the unusable ones', () => {
		const row = toTaskRow({
			id: 'a',
			title: 'x',
			steps: [{ title: 'one', status: 'done' }, { status: 'running' }, 7]
		})!;
		expect(row.steps.map((s) => s.title)).toEqual(['one']);
	});
});

describe('keeping a list live off the bus', () => {
	it('adds a task the moment one is announced', () => {
		const list = applyTaskEvent([], ev(EVENT_TASK_ADDED, { id: 'new' }));
		expect(list.map((t) => t.id)).toEqual(['new']);
	});

	it('replaces a task in place when it moves', () => {
		const start = [task({ id: 'a', updated: 1 })];
		const after = applyTaskEvent(
			start,
			ev(EVENT_TASK_UPDATED, { id: 'a', updated: 2, status: 'done', finished: true })
		);
		expect(after).toHaveLength(1);
		expect(after[0].status).toBe('done');
	});

	it('inserts on an update for a task it has never seen', () => {
		// Work that began before the page loaded, or that a `kind` filter had
		// excluded until this very update. Ignoring it loses the task entirely.
		const after = applyTaskEvent([], ev(EVENT_TASK_UPDATED, { id: 'unseen' }));
		expect(after.map((t) => t.id)).toEqual(['unseen']);
	});

	it('removes on a removal', () => {
		const start = [task({ id: 'a' }), task({ id: 'b' })];
		const after = applyTaskEvent(start, ev(EVENT_TASK_REMOVED, { id: 'a' }));
		expect(after.map((t) => t.id)).toEqual(['b']);
	});

	it('ignores an event about something else entirely', () => {
		const start = [task({ id: 'a' })];
		expect(applyTaskEvent(start, { event_type: 'state_changed', data: {} })).toBe(start);
		expect(applyTaskEvent(start, null)).toBe(start);
	});

	it('returns the very same array when nothing changed, so a page can skip a redraw', () => {
		const start = [task({ id: 'a' })];
		expect(applyTaskEvent(start, ev(EVENT_TASK_REMOVED, { id: 'not-here' }))).toBe(start);
	});

	it('refuses to let a stale frame undo a newer one', () => {
		// One socket delivers in order, but a `list` response in flight while an
		// event fires lands after it. Whichever the server stamped later wins.
		const start = [task({ id: 'a', updated: 500, status: 'done', finished: true })];
		const after = applyTaskEvent(
			start,
			ev(EVENT_TASK_UPDATED, { id: 'a', updated: 100, status: 'running' })
		);
		expect(after[0].status).toBe('done');
	});

	it('keeps the newest task at the top when one is inserted', () => {
		const start = [task({ id: 'old', created: 10 })];
		const after = applyTaskEvent(start, ev(EVENT_TASK_ADDED, { id: 'new', created: 99 }));
		expect(after.map((t) => t.id)).toEqual(['new', 'old']);
	});
});

describe('merging a refresh into what is on screen', () => {
	it('lets the fetched list decide what still exists', () => {
		const current = [task({ id: 'gone' }), task({ id: 'a' })];
		const merged = mergeTaskList(current, [task({ id: 'a' })]);
		expect(merged.map((t) => t.id)).toEqual(['a']);
	});

	it('does not undo an event that landed while the fetch was in flight', () => {
		const current = [task({ id: 'a', updated: 900, status: 'done', finished: true })];
		const merged = mergeTaskList(current, [task({ id: 'a', updated: 100, status: 'running' })]);
		expect(merged[0].status).toBe('done');
	});

	it('takes the fetched row when it is the newer one', () => {
		const current = [task({ id: 'a', updated: 100, status: 'running' })];
		const merged = mergeTaskList(current, [
			task({ id: 'a', updated: 900, status: 'done', finished: true })
		]);
		expect(merged[0].status).toBe('done');
	});
});

describe('what the bar draws', () => {
	it('fills to the fraction the server sent', () => {
		expect(percent(task({ fraction: 0.5 }))).toBe(50);
		expect(percent(task({ fraction: 1 / 3 }))).toBe(33);
	});

	it('is indeterminate while running with no fraction', () => {
		expect(barMode(task({ status: 'running', fraction: null }))).toBe('indeterminate');
	});

	it('is indeterminate for an open-ended crawl rather than sitting at a fake number', () => {
		const crawl = task({ status: 'running', open_ended: true, fraction: null, total_steps: 4 });
		expect(barMode(crawl)).toBe('indeterminate');
		expect(percent(crawl)).toBe(0);
	});

	it('keeps a failure where it failed instead of snapping it to nought or one', () => {
		// How far it got is the only interesting fact about a failed task.
		const failed = task({ status: 'error', finished: true, fraction: 0.4, done_steps: 2, total_steps: 5 });
		expect(barMode(failed)).toBe('determinate');
		expect(percent(failed)).toBe(40);
	});

	it('does not animate a task that is waiting on a person', () => {
		// A crawling bar over `blocked` says "working", which is exactly how an
		// approval prompt goes unnoticed.
		expect(barMode(task({ status: 'blocked', fraction: null }))).toBe('none');
	});

	it('does not animate a task that is over', () => {
		expect(barMode(task({ status: 'cancelled', finished: true, fraction: null }))).toBe('none');
	});

	it('does not animate a task that has not started', () => {
		expect(barMode(task({ status: 'queued', fraction: null }))).toBe('none');
	});

	it('clamps a fraction the server should never have sent', () => {
		expect(percent(task({ fraction: 1.4 }))).toBe(100);
		expect(percent(task({ fraction: -1 }))).toBe(0);
	});
});

describe('the words beside the bar', () => {
	it('names the step that is running, not the last one that finished', () => {
		const t = task({
			steps: [
				{ title: 'search', status: 'done' },
				{ title: 'read page 4', status: 'running' },
				{ title: 'write it up', status: 'queued' }
			]
		});
		expect(currentStep(t)?.title).toBe('read page 4');
		expect(describeTask(t)).toBe('read page 4');
	});

	it('falls back to the next waiting step when none is running', () => {
		const t = task({
			steps: [
				{ title: 'search', status: 'done' },
				{ title: 'read', status: 'queued' }
			]
		});
		expect(currentStep(t)?.title).toBe('read');
	});

	it('prefers a step’s own detail to its title', () => {
		const t = task({ steps: [{ title: 'read', status: 'running', detail: 'page 4 of 12' }] });
		expect(describeTask(t)).toBe('page 4 of 12');
	});

	it('keeps jarvis-core’s own wording for a failure', () => {
		const t = task({ status: 'error', finished: true, error: 'the model server refused' });
		expect(describeTask(t)).toBe('the model server refused');
	});

	it('says something rather than nothing when a failure came with no reason', () => {
		expect(describeTask(task({ status: 'error', finished: true }))).toMatch(/failed/);
	});

	it('shows the result of a finished task', () => {
		const t = task({ status: 'done', finished: true, result: 'all twelve read' });
		expect(describeTask(t)).toBe('all twelve read');
	});

	it('says a blocked task is waiting for you', () => {
		expect(describeTask(task({ status: 'blocked' }))).toMatch(/waiting for you/);
	});

	it('counts steps only when there are any', () => {
		expect(stepCount(task({ done_steps: 3, total_steps: 8 }))).toBe('3 of 8');
		expect(stepCount(task({ total_steps: 0 }))).toBe('');
	});

	it('labels each status in one word', () => {
		expect(statusLabel(task({ status: 'blocked' }))).toBe('WAITING');
		expect(statusLabel(task({ status: 'error' }))).toBe('FAILED');
	});
});

describe('the accessibility of a bar', () => {
	it('carries a number a reader can announce when there is one', () => {
		const aria = barAria(task({ title: 'Research', fraction: 0.25 }));
		expect(aria['aria-valuenow']).toBe(25);
		expect(aria['aria-label']).toContain('Research');
	});

	it('omits the number when nobody computed one', () => {
		// ARIA says an indeterminate progressbar has no `aria-valuenow`, and it
		// is the only way a reader says "busy" instead of reading out a fiction.
		const aria = barAria(task({ status: 'running', fraction: null }));
		expect(aria['aria-valuenow']).toBeUndefined();
		expect(aria.role).toBe('progressbar');
	});
});

describe('splitting live work from finished work', () => {
	it('puts blocked with the live ones — it is not over', () => {
		const list = [
			task({ id: 'a', status: 'blocked' }),
			task({ id: 'b', status: 'done', finished: true })
		];
		expect(activeTasks(list).map((t) => t.id)).toEqual(['a']);
		expect(finishedTasks(list).map((t) => t.id)).toEqual(['b']);
	});

	it('offers cancel only for work that could still stop', () => {
		expect(canCancel(task({ status: 'running' }))).toBe(true);
		expect(canCancel(task({ status: 'blocked' }))).toBe(true);
		expect(canCancel(task({ status: 'done', finished: true }))).toBe(false);
	});
});

describe('times', () => {
	it('reads recent as just now', () => {
		expect(ago(1000, 1010)).toBe('just now');
		expect(ago(0, 1000)).toBe('just now');
	});

	it('counts minutes, hours and days', () => {
		expect(ago(1000, 1000 + 600)).toBe('10m ago');
		expect(ago(1000, 1000 + 7200)).toBe('2h ago');
		expect(ago(1000, 1000 + 172800)).toBe('2d ago');
	});

	it('measures a finished task to when it finished, not to now', () => {
		// Otherwise every completed task keeps counting up for ever.
		const t = task({ created: 100, updated: 160, status: 'done', finished: true });
		expect(elapsed(t, 99999)).toBe('1m 0s');
	});

	it('measures a running task to now', () => {
		expect(elapsed(task({ created: 100, updated: 100 }), 130)).toBe('30s');
	});
});

describe('the docked strip', () => {
	it('keeps a task that just finished, so you see it land', () => {
		const done = task({ id: 'a', status: 'done', finished: true, updated: 1000 });
		expect(dockTasks([done], 1002).map((t) => t.id)).toEqual(['a']);
	});

	it('lets it go once its moment has passed', () => {
		const done = task({ id: 'a', status: 'done', finished: true, updated: 1000 });
		expect(dockTasks([done], 1000 + LINGER_MS / 1000 + 1)).toEqual([]);
	});

	it('holds a failure far longer — it is an answer, not a progress report', () => {
		const failed = task({ id: 'a', status: 'error', finished: true, updated: 1000 });
		const afterOrdinaryLinger = 1000 + LINGER_MS / 1000 + 1;
		expect(dockTasks([failed], afterOrdinaryLinger).map((t) => t.id)).toEqual(['a']);
		expect(dockTasks([failed], 1000 + LINGER_FAILED_MS / 1000 + 1)).toEqual([]);
	});

	it('puts live work above what has just ended', () => {
		// A finished job floating above a running one buries the thing you came
		// to the dock to read.
		const list = [
			task({ id: 'done', status: 'done', finished: true, created: 9999, updated: 1000 }),
			task({ id: 'live', status: 'running', created: 1 })
		];
		expect(dockTasks(list, 1001).map((t) => t.id)).toEqual(['live', 'done']);
	});

	it('counts waiting separately from running', () => {
		// Folded together, an approval sits unnoticed behind a spinner.
		const list = [
			task({ id: 'a', status: 'running' }),
			task({ id: 'b', status: 'running' }),
			task({ id: 'c', status: 'blocked' })
		];
		expect(summarise(list)).toBe('2 running · 1 waiting on you');
	});

	it('says nothing when there is nothing to say', () => {
		expect(summarise([])).toBe('');
	});

	it('asks for one timer at the next expiry, not a tick for ever', () => {
		const done = task({ id: 'a', status: 'done', finished: true, updated: 1000 });
		expect(nextLingerExpiry([done], 1000)).toBe(LINGER_MS);
		expect(nextLingerExpiry([done], 1000 + LINGER_MS / 1000 + 1)).toBeNull();
		expect(nextLingerExpiry([task({ status: 'running' })])).toBeNull();
	});

	it('takes the soonest expiry when several are pending', () => {
		const list = [
			task({ id: 'old', status: 'done', finished: true, updated: 996 }),
			task({ id: 'new', status: 'done', finished: true, updated: 1000 })
		];
		expect(nextLingerExpiry(list, 1000)).toBe(LINGER_MS - 4000);
	});
});
