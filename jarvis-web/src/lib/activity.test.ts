import { describe, expect, it } from 'vitest';
import { ACTIVITY_CAP, activityFrom, applyActivity, lookingCaption } from './activity.svelte';
import type { BusEvent } from './jarvisClient';
import { readFileSync } from 'node:fs';
import { ACTIVITY_EVENTS } from './activity.svelte';

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

	it('a button press is a row every time, and a press entity is a sensor row', () => {
		let rows = applyActivity([], ev('jarvis_mqtt_event', { entity_id: 'event.hall_remote_action', event_type: 'on', at: 1 }), at);
		rows = applyActivity(rows, ev('jarvis_mqtt_event', { entity_id: 'event.hall_remote_action', event_type: 'on', at: 2 }), at);
		expect(rows).toHaveLength(2);
		expect(rows[0]).toMatchObject({ kind: 'sensor', title: 'hall remote action', detail: 'pressed · on', state: 'done' });
		const tracker = activityFrom(ev('state_changed', { entity_id: 'device_tracker.phone', new_state: { state: 'home', attributes: { friendly_name: 'Phone' } } }), at);
		expect(tracker).toMatchObject({ kind: 'sensor', title: 'Phone' });
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

describe('the contract the phone mirrors', () => {
	const contract = JSON.parse(readFileSync(new URL('../../../tests/contracts/activity_rows.json', import.meta.url), 'utf8'));
	it('names the same events, kinds and cap as the store', () => {
		expect([...ACTIVITY_EVENTS].sort()).toEqual(Object.keys(contract.events).sort());
		expect(contract.cap).toBe(ACTIVITY_CAP);
		for (const [event, kind] of Object.entries(contract.events)) {
			const row = activityFrom(ev(event, { entity_id: 'sensor.x', new_state: { state: '1' }, task: { id: 't' }, notification: { id: 'n' } }), at);
			expect(row?.kind, event).toBe(kind);
		}
	});
});

describe('who the voice gate heard (M71)', () => {
	const contract = JSON.parse(readFileSync(new URL('../../../tests/contracts/speaker_verdict.json', import.meta.url), 'utf8'));
	const verdict = (data: Record<string, unknown>) =>
		activityFrom(ev(contract.event, { run_id: 'r1', pipeline: 'jarvis', device_id: null, at: 1, mode: 'enforce', confidence: 0.9, ...data }), at);

	it('an accepted voice is a row named after the person, with the numbers', () => {
		const row = verdict({ accepted: true, label: 'Ted', nearest: 'Ted', score: 2.314, threshold: 8.831, reason: 'match', enforced: false });
		expect(row).toMatchObject({ kind: contract.row.kind, title: 'Ted', detail: '2.31 / 8.83', state: 'done', id: 'speaker:r1' });
	});

	it('a refusal names nobody as the speaker and says who they were nearest', () => {
		const row = verdict({ accepted: false, label: null, nearest: 'owner', score: 11.87, threshold: 9.0, reason: 'mismatch', enforced: true });
		expect(row).toMatchObject({ title: 'not recognised', detail: 'refused · nearest owner · 11.87 / 9.00', state: 'failed' });
		// Observed, not enforced: the same verdict is not a failure of anything.
		const seen = verdict({ accepted: false, label: null, nearest: 'owner', score: 11.87, threshold: 9.0, reason: 'mismatch', enforced: false });
		expect(seen).toMatchObject({ title: 'not recognised', detail: 'observed · nearest owner · 11.87 / 9.00', state: 'done' });
	});

	it('every reason the contract calls unverifiable is drawn as unverified, never as a stranger', () => {
		expect(contract.unverifiable_reasons.length).toBeGreaterThan(0);
		for (const reason of contract.unverifiable_reasons as string[]) {
			const row = verdict({ accepted: false, label: null, nearest: null, score: null, threshold: null, reason, enforced: false });
			expect(row, reason).toMatchObject({ title: 'unverified', detail: reason, state: 'done' });
		}
	});

	it('one turn is one row: the same run updates in place', () => {
		let rows = applyActivity([], ev(contract.event, { run_id: 'r1', accepted: true, label: 'owner', score: 1, threshold: 9 }), at);
		rows = applyActivity(rows, ev(contract.event, { run_id: 'r1', accepted: true, label: 'owner', score: 1.5, threshold: 9 }), at);
		expect(rows).toHaveLength(1);
		expect(rows[0].detail).toBe('1.50 / 9.00');
	});
});
