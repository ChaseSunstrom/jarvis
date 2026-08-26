import { describe, expect, it } from 'vitest';
import { ACTIVITY_CAP, activityFrom, applyActivity, lookingCaption } from './activity.svelte';
import type { BusEvent } from './jarvisClient';

const at = () => 1_000;
const ev = (event_type: string, data: Record<string, unknown>): BusEvent =>
	({ event_type, data, time_fired: '', origin: 'LOCAL', context: { id: 'c' } }) as unknown as BusEvent;

describe('activityFrom', () => {
	it('a tool call is one row from start to finish, updated in place', () => {
		let rows = applyActivity([], ev('jarvis_tool_started', { name: 'get_state', arguments: { entity_id: 'light.hall' }, round: 1, index: 0 }), at);
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({ kind: 'tool', title: 'get_state', state: 'live', detail: 'entity_id: light.hall' });
		rows = applyActivity(rows, ev('jarvis_tool_finished', { name: 'get_state', round: 1, index: 0, ok: true, duration_ms: 84 }), at);
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({ state: 'done', detail: '84 ms' });
	});

	it('a failed tool says why', () => {
		const rows = applyActivity([], ev('jarvis_tool_finished', { name: 'turn_on', round: 1, index: 0, ok: false, error: 'no such entity' }), at);
		expect(rows[0]).toMatchObject({ state: 'failed', detail: 'no such entity' });
	});

	it('only readings make sensor rows; a light flipping is not activity', () => {
		expect(activityFrom(ev('state_changed', { entity_id: 'light.hall', new_state: { state: 'on' } }), at)).toBeNull();
		const row = activityFrom(
			ev('state_changed', {
				entity_id: 'sensor.lab_temperature',
				new_state: { state: '23.1', attributes: { friendly_name: 'Lab Temperature', unit_of_measurement: '°C' } }
			}),
			at
		);
		expect(row).toMatchObject({ kind: 'sensor', title: 'Lab Temperature', detail: '23.1 °C', state: 'done' });
	});

	it('a task row follows the task through its steps and its end', () => {
		const task = { id: 't1', title: 'Read twelve pages', status: 'running', steps: [{ status: 'done' }, { status: 'running' }, { status: 'queued' }] };
		const live = activityFrom(ev('jarvis_task_updated', { task }), at);
		expect(live).toMatchObject({ kind: 'task', state: 'live', detail: '1/3 steps · running' });
		const done = activityFrom(ev('jarvis_task_updated', { task: { ...task, status: 'done' } }), at);
		expect(done?.state).toBe('done');
		const failed = activityFrom(ev('jarvis_task_updated', { task: { ...task, status: 'error' } }), at);
		expect(failed?.state).toBe('failed');
	});

	it('a camera look is live while it lasts, and names the camera under the reactor', () => {
		let rows = applyActivity([], ev('vision_look_started', { id: 'l1', camera: 'Kitchen', question: 'anyone?' }), at);
		expect(lookingCaption(rows)).toBe('looking · Kitchen');
		rows = applyActivity(rows, ev('vision_look_finished', { id: 'l1', camera: 'Kitchen' }), at);
		expect(rows[0].state).toBe('done');
		expect(lookingCaption(rows)).toBe('');
		const denied = activityFrom(ev('vision_look_denied', { id: 'l2', camera: 'Bedroom', reason: 'consent: never' }), at);
		expect(denied).toMatchObject({ state: 'failed', detail: 'consent: never' });
	});

	it('memory, moments and approvals are rows; unrelated events are not', () => {
		expect(activityFrom(ev('memory_changed', { action: 'forgotten', entry: { id: 'm1', text: 'The shed key is under the flowerpot.' } }), at)).toMatchObject({ kind: 'memory', title: 'forgotten' });
		expect(activityFrom(ev('jarvis_notification', { notification: { id: 'n1', title: 'Check the oven', kind: 'reminder' } }), at)).toMatchObject({ kind: 'moment', title: 'Check the oven', detail: 'reminder' });
		expect(activityFrom(ev('jarvis_approval_required', { id: 'a1', tool: 'lock_control' }), at)).toMatchObject({ kind: 'approval', state: 'live' });
		expect(activityFrom(ev('area_registry_updated', {}), at)).toBeNull();
	});

	it('the strip keeps a dozen rows, newest first', () => {
		let rows: ReturnType<typeof applyActivity> = [];
		for (let i = 0; i < 15; i++) {
			rows = applyActivity(rows, ev('jarvis_tool_started', { name: `tool_${i}`, round: 1, index: i }), () => i);
		}
		expect(rows).toHaveLength(ACTIVITY_CAP);
		expect(rows[0].title).toBe('tool_14');
		expect(rows[ACTIVITY_CAP - 1].title).toBe('tool_3');
	});
});
