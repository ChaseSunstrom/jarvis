/**
 * Watching one task run.
 *
 * `tasks.ts` keeps the list live; this keeps one task's *activity* live — the
 * tool calls as they are made, the output as it arrives, and the log that lets
 * a page opened two minutes into a job show what it missed.
 *
 * The wire contract is `tests/contracts/task_events.json`, which jarvis-core's
 * `tests/test_task_events_contract.py` and this file's tests both read. Two
 * suites, one table: a comment saying "keep in step" has never kept anything
 * in step.
 *
 * Pure, like `tasks.ts`: the reducers are tested in Node and the components
 * stay thin.
 */

export const EVENT_TASK_TOOL_STARTED = 'jarvis_task_tool_started';
export const EVENT_TASK_TOOL_FINISHED = 'jarvis_task_tool_finished';
export const EVENT_TASK_OUTPUT = 'jarvis_task_output';
/**
 * A coding job wanting a human before it edits, runs or checks anything.
 *
 * The same event the model's own safety gate fires — one vocabulary for "this
 * has NOT happened and is waiting on you" — with a `task_id`, which is what
 * lets it appear on the job it belongs to instead of only in the global
 * approval banner.
 */
export const EVENT_APPROVAL_REQUIRED = 'jarvis_approval_required';
export const EVENT_APPROVAL_RESOLVED = 'jarvis_approval_resolved';

/** The events a task detail page subscribes to, beyond the three lifecycle ones. */
export const TASK_ACTIVITY_EVENTS = [
	EVENT_TASK_TOOL_STARTED,
	EVENT_TASK_TOOL_FINISHED,
	EVENT_TASK_OUTPUT,
	EVENT_APPROVAL_REQUIRED,
	EVENT_APPROVAL_RESOLVED
] as const;

export interface ToolCall {
	callId: string;
	name: string;
	/** Already bounded and redacted by the server. */
	arguments: Record<string, unknown> | string;
	index: number;
	total: number;
	startedAt: number;
	/** Undefined while it is still running — which is how "live" is drawn. */
	ok?: boolean;
	status?: string;
	error?: string;
	durationMs?: number;
}

export interface OutputChunk {
	seq: number;
	stream: 'stdout' | 'stderr' | 'note';
	chunk: string;
}

export interface LogEntry {
	at: number;
	kind: 'status' | 'step' | 'tool' | 'output' | 'note';
	text: string;
}

/** One action this job is waiting on a person for. */
export interface HeldAction {
	requestId: string;
	/** `edit`, `check` or `command`. */
	kind: string;
	summary: string;
	/** The change itself, or the command's text — what a person actually reads. */
	detail: string;
	at: number;
}

export interface Activity {
	taskId: string;
	calls: ToolCall[];
	output: OutputChunk[];
	/**
	 * What the job is waiting for, if anything. A list rather than one, because
	 * nothing in the protocol promises a job holds only one action at a time,
	 * and a second request arriving would otherwise replace the first silently.
	 */
	held: HeldAction[];
	/**
	 * Everything that happened, oldest first: replayed once when the page opens
	 * (so somebody arriving late sees what they missed) and extended by every
	 * event after that. The timeline draws this, and it must be one list — a
	 * timeline that started at "when you looked" would be the exact hole this
	 * milestone closes.
	 */
	log: LogEntry[];
}

/** How long the timeline gets before the oldest entries fall off. */
export const MAX_LOG = 400;
/** How much output one page keeps. A job can print more than a browser can hold. */
export const MAX_OUTPUT_CHUNKS = 400;
/** How many calls a page keeps. Older ones are in the log. */
export const MAX_CALLS = 200;

export function emptyActivity(taskId: string): Activity {
	return { taskId, calls: [], output: [], held: [], log: [] };
}

const asRecord = (value: unknown): Record<string, unknown> =>
	value && typeof value === 'object' ? (value as Record<string, unknown>) : {};

const str = (value: unknown, fallback = ''): string =>
	typeof value === 'string' ? value : fallback;

const num = (value: unknown, fallback = 0): number =>
	typeof value === 'number' && Number.isFinite(value) ? value : fallback;

/**
 * Fold one bus event into a task's activity.
 *
 * Returns the same object when the event is for another task or is not one of
 * ours, so a caller can use identity to decide whether to re-render.
 */
export function applyActivityEvent(
	activity: Activity,
	eventType: string,
	data: unknown,
	now: number = Date.now()
): Activity {
	const payload = asRecord(data);
	if (str(payload.task_id) !== activity.taskId) return activity;

	const note = (kind: LogEntry['kind'], text: string): LogEntry[] =>
		[...activity.log, { at: now / 1000, kind, text }].slice(-MAX_LOG);

	if (eventType === EVENT_APPROVAL_REQUIRED) {
		const held: HeldAction = {
			requestId: str(payload.request_id),
			kind: str(payload.kind, 'action'),
			summary: str(payload.summary ?? payload.description),
			detail: str(payload.detail),
			at: now
		};
		if (!held.requestId) return activity;
		return {
			...activity,
			held: [...activity.held.filter((h) => h.requestId !== held.requestId), held],
			log: note('note', `waiting for approval: ${held.summary}`)
		};
	}

	if (eventType === EVENT_APPROVAL_RESOLVED) {
		const requestId = str(payload.request_id);
		const approved = payload.approved === true;
		return {
			...activity,
			held: activity.held.filter((h) => h.requestId !== requestId),
			log: note('note', approved ? 'approved' : 'declined')
		};
	}

	if (eventType === EVENT_TASK_TOOL_STARTED) {
		const call: ToolCall = {
			callId: str(payload.call_id),
			name: str(payload.name, 'tool'),
			arguments:
				typeof payload.arguments === 'string'
					? payload.arguments
					: asRecord(payload.arguments),
			index: num(payload.index),
			total: num(payload.total),
			startedAt: now
		};
		return {
			...activity,
			calls: [...activity.calls, call].slice(-MAX_CALLS),
			log: note('tool', `${call.name} ${describeArguments(call)}`.trim())
		};
	}

	if (eventType === EVENT_TASK_TOOL_FINISHED) {
		const callId = str(payload.call_id);
		// Match by id; fall back to the last unfinished call of that name, because
		// a worker that does not carry ids still deserves to be watchable.
		let index = activity.calls.findIndex((call) => call.callId === callId && callId !== '');
		if (index < 0)
			index = activity.calls.findLastIndex(
				(call) => call.name === str(payload.name) && call.ok === undefined
			);
		if (index < 0) return activity;
		const calls = activity.calls.slice();
		calls[index] = {
			...calls[index],
			ok: payload.ok !== false,
			status: str(payload.status, payload.ok === false ? 'error' : 'ok'),
			error: str(payload.error),
			durationMs: num(payload.duration_ms)
		};
		const done = calls[index];
		return {
			...activity,
			calls,
			log: note(
				'tool',
				`${done.name} ${done.ok ? 'ok' : 'failed'}${done.durationMs ? ` ${done.durationMs} ms` : ''}${done.error ? ` ${done.error}` : ''}`
			)
		};
	}

	if (eventType === EVENT_TASK_OUTPUT) {
		const chunk: OutputChunk = {
			seq: num(payload.seq),
			stream: (['stdout', 'stderr', 'note'] as const).includes(payload.stream as never)
				? (payload.stream as OutputChunk['stream'])
				: 'stdout',
			chunk: str(payload.chunk)
		};
		if (!chunk.chunk) return activity;
		return {
			...activity,
			output: [...activity.output, chunk].slice(-MAX_OUTPUT_CHUNKS),
			log: note('output', chunk.chunk)
		};
	}

	return activity;
}

/** The replayed log, as `jarvis/tasks/log` returns it. */
export function toLog(value: unknown): LogEntry[] {
	const rows = Array.isArray(asRecord(value).log) ? (asRecord(value).log as unknown[]) : [];
	const kinds = ['status', 'step', 'tool', 'output', 'note'] as const;
	return rows.map((row) => {
		const entry = asRecord(row);
		const kind = str(entry.kind, 'note') as LogEntry['kind'];
		return {
			at: num(entry.at),
			kind: kinds.includes(kind) ? kind : 'note',
			text: str(entry.text)
		};
	});
}

/** Is anything still in flight? Drives the "live" mark on the page. */
export function hasRunningCall(activity: Activity): boolean {
	return activity.calls.some((call) => call.ok === undefined);
}

/** The output as one blob, for a pane that scrolls. */
export function outputText(activity: Activity): string {
	return activity.output.map((chunk) => chunk.chunk).join('\n');
}

/**
 * A one-line summary of a tool call's arguments.
 *
 * The page shows the tool's name and this; the whole argument object belongs in
 * the log, not on a row somebody is scanning.
 */
export function describeArguments(call: ToolCall): string {
	if (typeof call.arguments === 'string') return call.arguments.slice(0, 120);
	const parts = Object.entries(call.arguments)
		.slice(0, 3)
		.map(([key, value]) => `${key} ${String(value).slice(0, 60)}`);
	return parts.join(' · ');
}
