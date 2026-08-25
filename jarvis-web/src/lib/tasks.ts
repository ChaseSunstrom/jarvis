/**
 * What the console knows about long-running work.
 *
 * jarvis-core keeps the registry (`jarvis/tasks.py`); this file is the console's
 * half — the wire types, the reducer that keeps a list live off the bus, and the
 * handful of questions a progress bar has to answer. All of it is pure, so it is
 * tested in Node and the Svelte components stay thin.
 *
 * ## The one rule
 *
 * **Never invent a number.** jarvis-core sends `fraction` and deliberately sends
 * `null` when a percentage would be a guess — a task with no steps, or one still
 * discovering them. Recomputing `done_steps / total_steps` here would turn every
 * one of those into a bar that sits at 90% for four minutes, which is the exact
 * thing the server-side design refuses to do. So the fraction is passed through,
 * and "no fraction" is drawn as an indeterminate bar rather than as 0%.
 *
 * The corollary catches the other half: a task that FAILED at 2 steps of 5 keeps
 * its 40%. Snapping a failure to 100% (or to 0) loses the only interesting fact
 * about it, which is how far it got.
 */

export const EVENT_TASK_ADDED = 'jarvis_task_added';
export const EVENT_TASK_UPDATED = 'jarvis_task_updated';
export const EVENT_TASK_REMOVED = 'jarvis_task_removed';

export const TASK_EVENTS = [EVENT_TASK_ADDED, EVENT_TASK_UPDATED, EVENT_TASK_REMOVED] as const;

export type TaskStatus = 'queued' | 'running' | 'blocked' | 'done' | 'error' | 'cancelled';

export interface TaskStep {
	title: string;
	status: TaskStatus;
	detail?: string;
}

/** One task, exactly as `jarvis/tasks/list` and the three events carry it. */
export interface TaskRow {
	id: string;
	kind: string;
	title: string;
	status: TaskStatus;
	steps: TaskStep[];
	detail?: string;
	result?: string;
	error?: string;
	created: number;
	updated: number;
	source?: string;
	open_ended?: boolean;
	/** Server-derived. `null` means "do not draw a number" — never 0. */
	fraction: number | null;
	done_steps: number;
	total_steps: number;
	finished: boolean;
	/** The lead this task was spawned under, for a subagent. "" for a lead. */
	parent_id?: string;
	/** Which specialist ran it: `researcher`, `verifier`, … */
	agent?: string;
}

const TERMINAL: readonly TaskStatus[] = ['done', 'error', 'cancelled'];
const STATUSES: readonly TaskStatus[] = ['queued', 'running', 'blocked', ...TERMINAL];

/** Whether a task is over, from the server's flag or its status. */
export function isFinished(task: TaskRow): boolean {
	return task.finished ?? TERMINAL.includes(task.status);
}

/**
 * Coerce one wire object into a `TaskRow`, or null if it is not one.
 *
 * A malformed row must not take out the page it is on: a list is drawn from
 * whatever survives this, so one bad record costs one row rather than the whole
 * panel. `fraction` is the field to be careful with — `Number(null)` is 0, and
 * silently turning "no number" into "0%" is the bug this module exists to avoid.
 */
export function toTaskRow(raw: unknown): TaskRow | null {
	if (!raw || typeof raw !== 'object') return null;
	const r = raw as Record<string, unknown>;
	const id = typeof r.id === 'string' ? r.id : '';
	if (!id) return null;
	const status = (STATUSES as readonly string[]).includes(String(r.status))
		? (r.status as TaskStatus)
		: 'queued';
	const steps = Array.isArray(r.steps)
		? r.steps
				.map((s) => toStep(s))
				.filter((s): s is TaskStep => s !== null)
		: [];
	return {
		id,
		kind: typeof r.kind === 'string' && r.kind ? r.kind : 'background',
		title: typeof r.title === 'string' ? r.title : '',
		status,
		steps,
		detail: str(r.detail),
		result: str(r.result),
		error: str(r.error),
		created: num(r.created),
		updated: num(r.updated),
		source: str(r.source),
		open_ended: Boolean(r.open_ended),
		fraction: typeof r.fraction === 'number' && Number.isFinite(r.fraction) ? r.fraction : null,
		done_steps: typeof r.done_steps === 'number' ? r.done_steps : steps.filter(isStepOver).length,
		total_steps: typeof r.total_steps === 'number' ? r.total_steps : steps.length,
		finished:
			typeof r.finished === 'boolean' ? r.finished : TERMINAL.includes(status),
		parent_id: str(r.parent_id),
		agent: str(r.agent)
	};
}

function toStep(raw: unknown): TaskStep | null {
	if (!raw || typeof raw !== 'object') return null;
	const r = raw as Record<string, unknown>;
	const title = typeof r.title === 'string' ? r.title : '';
	if (!title) return null;
	const status = (STATUSES as readonly string[]).includes(String(r.status))
		? (r.status as TaskStatus)
		: 'queued';
	return { title, status, detail: str(r.detail) };
}

function isStepOver(step: TaskStep): boolean {
	return TERMINAL.includes(step.status);
}

function str(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

function num(value: unknown): number {
	return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** Read a list payload, dropping anything malformed rather than throwing. */
export function toTaskList(raw: unknown): TaskRow[] {
	const list = Array.isArray(raw)
		? raw
		: Array.isArray((raw as { tasks?: unknown })?.tasks)
			? (raw as { tasks: unknown[] }).tasks
			: [];
	return list.map(toTaskRow).filter((t): t is TaskRow => t !== null);
}

// --- keeping a list live ------------------------------------------------------

/**
 * Fold one bus event into the list the page is showing.
 *
 * Returns the SAME array when nothing changed, so a caller can use identity to
 * skip a re-render. Events that are not about tasks are ignored rather than
 * treated as an error — a page may well be subscribed to everything.
 *
 * An `updated` for a task the list has never seen is an INSERT, not a no-op.
 * The alternative loses work that started before the page loaded, or that a
 * `?kind=` filter had excluded until the moment it changed.
 */
export function applyTaskEvent(
	list: TaskRow[],
	event: { event_type?: string; data?: Record<string, unknown> } | null | undefined
): TaskRow[] {
	const type = event?.event_type;
	if (!type || !(TASK_EVENTS as readonly string[]).includes(type)) return list;
	const task = toTaskRow(event?.data?.task);
	if (!task) return list;

	if (type === EVENT_TASK_REMOVED) {
		const without = list.filter((t) => t.id !== task.id);
		return without.length === list.length ? list : without;
	}

	const at = list.findIndex((t) => t.id === task.id);
	if (at < 0) return sortTasks([task, ...list]);
	// Out-of-order delivery is not hypothetical: a `list` response in flight
	// while an event fires lands after it and would otherwise reinstate the
	// older row. Whichever the server stamped later wins.
	if (list[at].updated > task.updated) return list;
	const next = list.slice();
	next[at] = task;
	return next;
}

/**
 * Merge a freshly fetched list into what is on screen.
 *
 * A refresh must not undo an event that arrived while it was in flight, and it
 * must not resurrect a task the server has since forgotten. So: the fetched set
 * decides membership, and per task the later `updated` decides content.
 */
export function mergeTaskList(current: TaskRow[], fetched: TaskRow[]): TaskRow[] {
	const live = new Map(current.map((t) => [t.id, t]));
	return sortTasks(
		fetched.map((incoming) => {
			const held = live.get(incoming.id);
			return held && held.updated > incoming.updated ? held : incoming;
		})
	);
}

/** Newest first, matching what jarvis-core's own listing returns. */
export function sortTasks(list: TaskRow[]): TaskRow[] {
	return list.slice().sort((a, b) => b.created - a.created || a.id.localeCompare(b.id));
}

export function activeTasks(list: TaskRow[]): TaskRow[] {
	return list.filter((t) => !isFinished(t));
}

export function finishedTasks(list: TaskRow[]): TaskRow[] {
	return list.filter(isFinished);
}

// --- what a bar draws ---------------------------------------------------------

/**
 * 'determinate' — a real fraction to fill to.
 * 'indeterminate' — something IS happening and how far along is unknown.
 * 'none' — nothing to animate: not started, or over and gone nowhere.
 */
export type BarMode = 'determinate' | 'indeterminate' | 'none';

export function barMode(task: TaskRow): BarMode {
	if (typeof task.fraction === 'number') return 'determinate';
	// A failure with no steps has nothing to draw, and a crawling bar under a
	// task that stopped an hour ago is a lie about the present tense.
	if (isFinished(task)) return 'none';
	// `blocked` is waiting on a PERSON. A moving bar over it says "working",
	// which is how an approval prompt goes unnoticed.
	if (task.status === 'blocked') return 'none';
	if (task.status === 'running') return 'indeterminate';
	return 'none';
}

/** 0..100 for a determinate bar; 0 for every other mode. */
export function percent(task: TaskRow): number {
	if (typeof task.fraction !== 'number') return 0;
	return Math.round(Math.min(1, Math.max(0, task.fraction)) * 100);
}

/**
 * The step to name beside the bar: the one running, else the next one waiting.
 *
 * Not "the last done one" — after step 2 of 5 finishes, a person wants to read
 * what is happening now, not what has stopped happening.
 */
export function currentStep(task: TaskRow): TaskStep | null {
	return (
		task.steps.find((s) => s.status === 'running') ??
		task.steps.find((s) => s.status === 'blocked') ??
		task.steps.find((s) => s.status === 'queued') ??
		null
	);
}

/** "3 of 8" — or "" when there are no steps and a count would be noise. */
export function stepCount(task: TaskRow): string {
	if (!task.total_steps) return '';
	return `${task.done_steps} of ${task.total_steps}`;
}

/**
 * One line under the title: what is happening, or why it stopped.
 *
 * The server's own wording wherever there is any — `error` and `detail` are
 * written for a person to act on, and replacing them with a generic phrase
 * throws away the actionable part.
 */
export function describeTask(task: TaskRow): string {
	if (task.status === 'error') return task.error || 'it failed, and said no more than that';
	if (task.status === 'cancelled') return task.detail || 'cancelled';
	if (task.status === 'done') return task.result || task.detail || 'finished';
	if (task.status === 'blocked') return task.detail || 'waiting for you';
	if (task.status === 'queued') return task.detail || 'queued';
	const step = currentStep(task);
	if (step) return step.detail || step.title;
	return task.detail || 'working';
}

/** The word to show as the status, in the console's own register. */
export function statusLabel(task: TaskRow): string {
	switch (task.status) {
		case 'running':
			return 'RUNNING';
		case 'blocked':
			return 'WAITING';
		case 'queued':
			return 'QUEUED';
		case 'done':
			return 'DONE';
		case 'error':
			return 'FAILED';
		case 'cancelled':
			return 'CANCELLED';
		default:
			return String(task.status).toUpperCase();
	}
}

/**
 * `aria-valuenow` and friends, so a bar is not a decorative div to a reader.
 *
 * An indeterminate bar omits `aria-valuenow` — that is exactly what ARIA says
 * to do, and it is also the only way a screen reader can say "busy" rather than
 * reading out a number nobody computed.
 */
export function barAria(task: TaskRow): Record<string, string | number | undefined> {
	const mode = barMode(task);
	const base = {
		role: 'progressbar',
		'aria-label': `${task.title || 'task'} progress`
	};
	if (mode !== 'determinate') return base;
	return { ...base, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': percent(task) };
}

/** "just now" / "4m ago" / "2h ago" — short enough for a dense row. */
export function ago(seconds: number, now: number = Date.now() / 1000): string {
	const delta = Math.max(0, now - (seconds || 0));
	if (!seconds || delta < 45) return 'just now';
	if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
	if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
	return `${Math.round(delta / 86400)}d ago`;
}

/** How long a task ran, or has been running. "1m 20s". */
export function elapsed(task: TaskRow, now: number = Date.now() / 1000): string {
	const end = isFinished(task) ? task.updated : now;
	const secs = Math.max(0, Math.round(end - task.created));
	if (secs < 60) return `${secs}s`;
	const mins = Math.floor(secs / 60);
	if (mins < 60) return `${mins}m ${secs % 60}s`;
	return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

/**
 * Whether cancelling this task is worth offering.
 *
 * Only for work that has not finished. Cancel on a finished task is a button
 * that cannot do anything, and jarvis-core answers it with `cancelled: false`.
 */
export function canCancel(task: TaskRow): boolean {
	return !isFinished(task);
}

// --- the dock -----------------------------------------------------------------
//
// The strip docked over every page. It shows live work, and it keeps a task for
// a moment after it ends: a job you were watching vanishing at the instant it
// succeeds is the one frame you actually wanted to see. A failure stays much
// longer, because that one is not a progress report — it is an answer.

export const LINGER_MS = 8_000;
export const LINGER_FAILED_MS = 30_000;

/** Milliseconds a finished task stays in the dock. 0 for work still going. */
export function lingerFor(task: TaskRow): number {
	if (!isFinished(task)) return 0;
	return task.status === 'error' ? LINGER_FAILED_MS : LINGER_MS;
}

/**
 * What the dock draws right now: everything live, plus what just ended.
 *
 * Live work first — the dock is a place to look at what is happening, and a
 * finished job floating above a running one buries the answer to that.
 */
export function dockTasks(list: TaskRow[], now: number = Date.now() / 1000): TaskRow[] {
	const keep = list.filter((task) => {
		if (!isFinished(task)) return true;
		return (now - task.updated) * 1000 < lingerFor(task);
	});
	const live = keep.filter((t) => !isFinished(t));
	const just = keep.filter(isFinished);
	return [...sortTasks(live), ...sortTasks(just)];
}

/**
 * "2 running · 1 waiting" — the one line above the dock.
 *
 * Waiting is counted separately from running on purpose: they are different
 * things to do about it, and folding them together is how an approval sits
 * unnoticed behind a spinner.
 */
export function summarise(list: TaskRow[]): string {
	const running = list.filter((t) => t.status === 'running').length;
	const waiting = list.filter((t) => t.status === 'blocked').length;
	const queued = list.filter((t) => t.status === 'queued').length;
	const failed = list.filter((t) => t.status === 'error').length;
	const parts: string[] = [];
	if (running) parts.push(`${running} running`);
	if (waiting) parts.push(`${waiting} waiting on you`);
	if (queued) parts.push(`${queued} queued`);
	if (failed) parts.push(`${failed} failed`);
	return parts.join(' · ');
}

/**
 * When the dock next needs to redraw itself because a task has aged out.
 *
 * Returned so the component can set ONE timer for the next expiry rather than
 * ticking every second for ever behind an empty dock. `null` means nothing is
 * waiting to expire.
 */
export function nextLingerExpiry(
	list: TaskRow[],
	now: number = Date.now() / 1000
): number | null {
	let soonest: number | null = null;
	for (const task of list) {
		const linger = lingerFor(task);
		if (!linger) continue;
		const left = linger - (now - task.updated) * 1000;
		if (left <= 0) continue;
		soonest = soonest === null ? left : Math.min(soonest, left);
	}
	return soonest;
}
