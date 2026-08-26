/**
 * What Jarvis is doing right now, as rows — the voice tab's activity strip.
 *
 * One store, fed by the bus events the core already fires: a tool call as it
 * starts and finishes, a task as it steps, a sensor as it changes, a camera as
 * it is looked at, a fact remembered or forgotten, a moment landing, an
 * approval waiting, an error. The strip draws the rows; this file decides what
 * a row is. Kept apart from the component so the phone can mirror the same
 * mapping (M61) and so it can be unit-tested without a browser.
 *
 * Rows are newest first and capped: the strip is a glance at the present, not
 * a log — the logbook and the traces are the record.
 */
import type { BusEvent } from './jarvisClient';

export type ActivityKind =
	| 'tool'
	| 'task'
	| 'sensor'
	| 'camera'
	| 'memory'
	| 'moment'
	| 'approval'
	| 'error';

export type ActivityState = 'live' | 'done' | 'failed';

export interface ActivityRow {
	/** Stable across the start and the finish of one thing, so a row updates in place. */
	id: string;
	kind: ActivityKind;
	/** The one line to read: the tool's name, the sensor's name, the moment's title. */
	title: string;
	/** Data, in mono: an argument, a reading, a duration. */
	detail: string;
	state: ActivityState;
	/** Epoch ms of the last change, for ordering and for the strip's age. */
	at: number;
}

/** How many rows the strip keeps. Enough to see a turn's work, not a day's. */
export const ACTIVITY_CAP = 12;

/** Domains whose state changes are readings worth a row, not chatter. */
const SENSOR_DOMAINS = new Set(['sensor', 'binary_sensor', 'climate', 'weather', 'number', 'event', 'device_tracker']);

function friendly(state: Record<string, unknown> | undefined, entityId: string): string {
	const attrs = (state?.attributes ?? {}) as Record<string, unknown>;
	const name = attrs.friendly_name;
	return typeof name === 'string' && name ? name : entityId;
}

function reading(state: Record<string, unknown> | undefined): string {
	if (!state) return '';
	const attrs = (state.attributes ?? {}) as Record<string, unknown>;
	const unit = typeof attrs.unit_of_measurement === 'string' ? attrs.unit_of_measurement : '';
	const value = state.state;
	return value === undefined || value === null ? '' : `${value}${unit ? ' ' + unit : ''}`;
}

function shortArgs(args: unknown): string {
	if (!args || typeof args !== 'object') return '';
	const parts = Object.entries(args as Record<string, unknown>)
		.filter(([, v]) => v !== undefined && v !== null && v !== '')
		.slice(0, 2)
		.map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`);
	const line = parts.join(' · ');
	return line.length > 48 ? `${line.slice(0, 47)}…` : line;
}

/**
 * The row an event makes, or null when the event is not activity.
 *
 * `now` is injected so tests are deterministic; the component passes
 * `Date.now`.
 */
export function activityFrom(event: BusEvent, now: () => number = Date.now): ActivityRow | null {
	const data = (event.data ?? {}) as Record<string, any>;
	const at = now();
	switch (event.event_type) {
		case 'jarvis_tool_started':
			return {
				id: `tool:${data.round ?? 0}:${data.index ?? 0}:${data.name ?? ''}`,
				kind: 'tool',
				title: String(data.name ?? 'tool'),
				detail: shortArgs(data.arguments),
				state: 'live',
				at
			};
		case 'jarvis_tool_finished': {
			const failed = data.ok === false || data.status === 'error';
			const ms = typeof data.duration_ms === 'number' ? `${Math.round(data.duration_ms)} ms` : '';
			return {
				id: `tool:${data.round ?? 0}:${data.index ?? 0}:${data.name ?? ''}`,
				kind: 'tool',
				title: String(data.name ?? 'tool'),
				detail: failed ? String(data.error ?? 'failed') : ms || shortArgs(data.arguments),
				state: failed ? 'failed' : 'done',
				at
			};
		}
		case 'jarvis_task_added':
		case 'jarvis_task_updated': {
			const task = (data.task ?? data) as Record<string, any>;
			const status = String(task.status ?? '');
			const steps = Array.isArray(task.steps) ? task.steps : [];
			const done = steps.filter((s: any) => s?.status === 'done').length;
			const state: ActivityState =
				status === 'error' ? 'failed' : status === 'done' || status === 'cancelled' ? 'done' : 'live';
			return {
				id: `task:${task.id ?? ''}`,
				kind: 'task',
				title: String(task.title ?? 'task'),
				detail: steps.length ? `${done}/${steps.length} steps · ${status}` : status,
				state,
				at
			};
		}
		case 'state_changed': {
			const entityId = String(data.entity_id ?? '');
			const domain = entityId.split('.')[0];
			if (!SENSOR_DOMAINS.has(domain)) return null;
			const next = data.new_state as Record<string, unknown> | undefined;
			return {
				id: `sensor:${entityId}`,
				kind: 'sensor',
				title: friendly(next, entityId),
				detail: reading(next),
				state: 'done',
				at
			};
		}
		case 'jarvis_mqtt_event': {
			// A button press (M57): the same button pressed twice is two rows,
			// which is why the id carries the time — a state row would collapse
			// them into one and the second press would look like nothing.
			const entityId = String(data.entity_id ?? '');
			const pressed = String(data.event_type ?? '');
			return {
				id: `press:${entityId}:${String(data.at ?? at)}`,
				kind: 'sensor',
				title: entityId.split('.').slice(1).join('.').replace(/_/g, ' ') || entityId,
				detail: pressed ? `pressed · ${pressed}` : 'pressed',
				state: 'done',
				at
			};
		}
		case 'vision_look_started':
			return {
				id: `camera:${data.id ?? data.camera ?? ''}`,
				kind: 'camera',
				title: String(data.camera ?? 'camera'),
				detail: String(data.question ?? 'looking'),
				state: 'live',
				at
			};
		case 'vision_look_finished':
			return {
				id: `camera:${data.id ?? data.camera ?? ''}`,
				kind: 'camera',
				title: String(data.camera ?? 'camera'),
				detail: 'looked',
				state: 'done',
				at
			};
		case 'vision_look_denied':
			return {
				id: `camera:${data.id ?? data.camera ?? ''}`,
				kind: 'camera',
				title: String(data.camera ?? 'camera'),
				detail: String(data.reason ?? 'not allowed'),
				state: 'failed',
				at
			};
		case 'memory_changed': {
			const action = String(data.action ?? '');
			const entry = (data.entry ?? {}) as Record<string, any>;
			const text = String(entry.text ?? '');
			return {
				id: `memory:${entry.id ?? at}`,
				kind: 'memory',
				title: action === 'forgotten' ? 'forgotten' : action === 'cleared' ? 'memory cleared' : 'remembered',
				detail: text.length > 48 ? `${text.slice(0, 47)}…` : text,
				state: 'done',
				at
			};
		}
		case 'jarvis_notification': {
			const n = (data.notification ?? data) as Record<string, any>;
			return {
				id: `moment:${n.id ?? at}`,
				kind: 'moment',
				title: String(n.title ?? 'moment'),
				detail: String(n.kind ?? ''),
				state: 'done',
				at
			};
		}
		case 'jarvis_approval_required':
			return {
				id: `approval:${data.id ?? data.request_id ?? at}`,
				kind: 'approval',
				title: String(data.tool ?? data.name ?? 'approval'),
				detail: 'waiting on you',
				state: 'live',
				at
			};
		case 'jarvis_approval_resolved':
			return {
				id: `approval:${data.id ?? data.request_id ?? at}`,
				kind: 'approval',
				title: String(data.tool ?? data.name ?? 'approval'),
				detail: String(data.decision ?? 'answered'),
				state: data.decision === 'deny' ? 'failed' : 'done',
				at
			};
		default:
			return null;
	}
}

/** The rows after `event`: updated in place by id, newest first, capped. */
export function applyActivity(rows: ActivityRow[], event: BusEvent, now: () => number = Date.now): ActivityRow[] {
	const row = activityFrom(event, now);
	if (!row) return rows;
	const rest = rows.filter((r) => r.id !== row.id);
	return [row, ...rest].slice(0, ACTIVITY_CAP);
}

/** The bus events the strip subscribes to — the mock and the core both fire these. */
export const ACTIVITY_EVENTS = [
	'jarvis_tool_started',
	'jarvis_tool_finished',
	'jarvis_task_added',
	'jarvis_task_updated',
	'state_changed',
	'jarvis_mqtt_event',
	'vision_look_started',
	'vision_look_finished',
	'vision_look_denied',
	'memory_changed',
	'jarvis_notification',
	'jarvis_approval_required',
	'jarvis_approval_resolved'
] as const;

/** What the voice tab shows under the reactor while a camera is being looked at. */
export function lookingCaption(rows: ActivityRow[]): string {
	const live = rows.find((r) => r.kind === 'camera' && r.state === 'live');
	return live ? `looking · ${live.title}` : '';
}
