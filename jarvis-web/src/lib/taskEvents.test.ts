// The console's half of the task-event contract.
//
// `tests/contracts/task_events.json` is the table; jarvis-core's
// `tests/test_task_events_contract.py` asserts the server fires exactly these,
// and this asserts the console understands exactly these. A field added on one
// side and not the other fails here rather than in a browser.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
	EVENT_TASK_CHILD_ADDED,
	EVENT_TASK_OUTPUT,
	EVENT_TASK_TOOL_FINISHED,
	EVENT_TASK_TOOL_STARTED,
	MAX_OUTPUT_CHUNKS,
	applyActivityEvent,
	applyChildEvent,
	describeArguments,
	emptyActivity,
	hasRunningCall,
	outputText,
	toLog
} from './taskEvents';

const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../tests/contracts/task_events.json', import.meta.url)),
		'utf8'
	)
);

const started = (over: Record<string, unknown> = {}) => ({
	task_id: 't1',
	call_id: 'c1',
	name: 'read_file',
	arguments: { path: 'a.svelte' },
	index: 1,
	total: 3,
	...over
});

describe('the contract', () => {
	it('names the three activity events this module handles', () => {
		for (const event of [EVENT_TASK_TOOL_STARTED, EVENT_TASK_TOOL_FINISHED, EVENT_TASK_OUTPUT]) {
			expect(Object.keys(CONTRACT.events), event).toContain(event);
		}
	});

	it('describes every field the reducer reads', () => {
		const required = (event: string) => CONTRACT.events[event].required as string[];
		expect(required(EVENT_TASK_TOOL_STARTED)).toEqual(
			expect.arrayContaining(['task_id', 'call_id', 'name', 'arguments', 'index', 'total'])
		);
		expect(required(EVENT_TASK_TOOL_FINISHED)).toEqual(
			expect.arrayContaining(['task_id', 'call_id', 'name', 'ok', 'error', 'duration_ms'])
		);
		expect(required(EVENT_TASK_OUTPUT)).toEqual(
			expect.arrayContaining(['task_id', 'stream', 'chunk', 'seq'])
		);
	});
});

describe('a tool call', () => {
	it('appears the moment it starts, and is unfinished until it finishes', () => {
		let activity = emptyActivity('t1');
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_STARTED, started());
		expect(activity.calls).toHaveLength(1);
		expect(activity.calls[0].ok).toBeUndefined();
		expect(hasRunningCall(activity)).toBe(true);

		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_FINISHED, {
			task_id: 't1',
			call_id: 'c1',
			name: 'read_file',
			ok: true,
			status: 'ok',
			error: '',
			duration_ms: 12
		});
		expect(activity.calls[0].ok).toBe(true);
		expect(activity.calls[0].durationMs).toBe(12);
		expect(hasRunningCall(activity)).toBe(false);
	});

	it('keeps a failure’s reason', () => {
		let activity = emptyActivity('t1');
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_STARTED, started({ name: 'run_check' }));
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_FINISHED, {
			task_id: 't1',
			call_id: 'c1',
			name: 'run_check',
			ok: false,
			error: 'exit 1: three failing'
		});
		expect(activity.calls[0].ok).toBe(false);
		expect(activity.calls[0].error).toContain('three failing');
	});

	it('matches a finish with no id to the last unfinished call of that name', () => {
		// A worker that does not carry ids still deserves to be watchable.
		let activity = emptyActivity('t1');
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_STARTED, started({ call_id: '' }));
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_FINISHED, {
			task_id: 't1',
			call_id: '',
			name: 'read_file',
			ok: true
		});
		expect(activity.calls[0].ok).toBe(true);
	});

	it('ignores an event for another task', () => {
		const activity = emptyActivity('t1');
		const same = applyActivityEvent(activity, EVENT_TASK_TOOL_STARTED, started({ task_id: 't2' }));
		expect(same).toBe(activity);
	});

	it('summarises its arguments in one line', () => {
		let activity = emptyActivity('t1');
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_STARTED, started());
		expect(describeArguments(activity.calls[0])).toBe('path a.svelte');
	});
});

describe('output', () => {
	it('arrives in order and keeps its stream', () => {
		let activity = emptyActivity('t1');
		activity = applyActivityEvent(activity, EVENT_TASK_OUTPUT, {
			task_id: 't1',
			seq: 1,
			stream: 'stdout',
			chunk: 'one'
		});
		activity = applyActivityEvent(activity, EVENT_TASK_OUTPUT, {
			task_id: 't1',
			seq: 2,
			stream: 'stderr',
			chunk: 'two'
		});
		expect(outputText(activity)).toBe('one\ntwo');
		expect(activity.output[1].stream).toBe('stderr');
	});

	it('refuses a stream it does not recognise', () => {
		let activity = emptyActivity('t1');
		activity = applyActivityEvent(activity, EVENT_TASK_OUTPUT, {
			task_id: 't1',
			seq: 1,
			stream: '../../etc',
			chunk: 'x'
		});
		expect(activity.output[0].stream).toBe('stdout');
	});

	it('is bounded: a job can print more than a browser can hold', () => {
		let activity = emptyActivity('t1');
		for (let i = 0; i < MAX_OUTPUT_CHUNKS + 40; i++) {
			activity = applyActivityEvent(activity, EVENT_TASK_OUTPUT, {
				task_id: 't1',
				seq: i,
				stream: 'stdout',
				chunk: `line ${i}`
			});
		}
		expect(activity.output).toHaveLength(MAX_OUTPUT_CHUNKS);
		// The tail: what somebody watching wants is the end, not the beginning.
		expect(activity.output.at(-1)?.chunk).toBe(`line ${MAX_OUTPUT_CHUNKS + 39}`);
	});
});

describe('the timeline', () => {
	it('is one list: what was replayed, then what happened while watching', () => {
		let activity = emptyActivity('t1');
		activity = { ...activity, log: [{ at: 1, kind: 'status', text: 'running' }] };
		activity = applyActivityEvent(activity, EVENT_TASK_TOOL_STARTED, started());
		activity = applyActivityEvent(activity, EVENT_TASK_OUTPUT, {
			task_id: 't1',
			seq: 1,
			stream: 'stdout',
			chunk: 'checking 446 files'
		});
		expect(activity.log.map((entry) => entry.kind)).toEqual(['status', 'tool', 'output']);
		expect(activity.log.at(-1)?.text).toBe('checking 446 files');
	});
});

describe('the replayed log', () => {
	it('reads what the server sends, and refuses a kind it does not know', () => {
		const log = toLog({
			task_id: 't1',
			log: [
				{ at: 1, kind: 'tool', text: 'read_file' },
				{ at: 2, kind: 'nonsense', text: 'x' }
			]
		});
		expect(log).toHaveLength(2);
		expect(log[0].kind).toBe('tool');
		expect(log[1].kind).toBe('note');
	});

	it('survives a payload with no log at all', () => {
		expect(toLog(undefined)).toEqual([]);
		expect(toLog({})).toEqual([]);
	});
});

// --- the subagent tree -------------------------------------------------------
//
// M20. A fan-out is the one shape where "what is this task doing" cannot be
// answered by a line of detail: five specialists are running and the
// interesting thing is which of them has come back.

describe('subagents', () => {
	const lead = () => emptyActivity('lead-1');
	const child = (over: Record<string, unknown> = {}) => ({
		task: {
			id: 'child-1',
			parent_id: 'lead-1',
			agent: 'researcher',
			title: 'when was the boiler serviced',
			status: 'running',
			...over
		}
	});

	it('hangs a child off the task it belongs to', () => {
		const after = applyActivityEvent(lead(), EVENT_TASK_CHILD_ADDED, child());
		expect(after.children).toHaveLength(1);
		expect(after.children[0].agent).toBe('researcher');
	});

	it('ignores a child of some other task', () => {
		const after = applyActivityEvent(
			lead(),
			EVENT_TASK_CHILD_ADDED,
			child({ parent_id: 'somebody-else' })
		);
		expect(after.children).toHaveLength(0);
	});

	it('follows a child rather than duplicating it', () => {
		// The child's own lifecycle events arrive as ordinary task updates; a
		// tree that appended each one would show the same specialist four times.
		let activity = applyChildEvent(lead(), child());
		activity = applyChildEvent(activity, child({ status: 'done', result: 'March' }));
		expect(activity.children).toHaveLength(1);
		expect(activity.children[0].status).toBe('done');
		expect(activity.children[0].result).toBe('March');
	});

	it('keeps the order they were spawned in', () => {
		let activity = applyChildEvent(lead(), child({ id: 'a', agent: 'researcher' }));
		activity = applyChildEvent(activity, child({ id: 'b', agent: 'verifier' }));
		activity = applyChildEvent(activity, child({ id: 'a', status: 'done' }));
		expect(activity.children.map((c) => c.id)).toEqual(['a', 'b']);
	});
});
