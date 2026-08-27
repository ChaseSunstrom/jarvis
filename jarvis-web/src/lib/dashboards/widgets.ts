/**
 * What the house widgets read, and the arithmetic of showing it (M63).
 *
 * Pure functions over the three commands' payloads (`jarvis/sensors/readings`,
 * `jarvis/sky/summary`, `jarvis/vision/still`) and the notifications list, so
 * grouping readings by room or deciding whether a tile gets a switch can be
 * tested in Node rather than inferred from a screenshot. The one rule shared
 * with the server: **say what is not there**. A missing reading, an unfetched
 * ephemeris or a refused still is a sentence, never a blank.
 */

import type { EntityState } from '$lib/jarvisClient';
import { DEFAULT_MOMENTS } from './chartTypes';

// Not `jarvisClient.domainOf`: the client imports this module for its
// parsers, and a value import back the other way is a cycle that resolves to
// `undefined` on whichever side loads second.
const domainOf = (entityId: string): string => entityId.split('.')[0] ?? '';

// --- readings ----------------------------------------------------------------

export interface Reading {
	entity_id: string;
	name: string;
	value: number | string;
	unit: string;
	device_class: string;
	area: string;
	/** Seconds since the reading last changed. */
	age_s: number;
	available: boolean;
}

export interface ReadingsPayload {
	readings: Reading[];
	/** Whether the sensors integration is there to bring new readings in. */
	configured: boolean;
}

export function toReadings(raw: unknown): ReadingsPayload {
	const source = (raw ?? {}) as Record<string, unknown>;
	const list = Array.isArray(source.readings) ? source.readings : [];
	return {
		readings: list.map((entry) => {
			const row = (entry ?? {}) as Record<string, unknown>;
			const value = row.value;
			return {
				entity_id: String(row.entity_id ?? ''),
				name: String(row.name ?? row.entity_id ?? ''),
				value: typeof value === 'number' && Number.isFinite(value) ? value : String(value ?? ''),
				unit: String(row.unit ?? ''),
				device_class: String(row.device_class ?? ''),
				area: String(row.area ?? ''),
				age_s: typeof row.age_s === 'number' ? Math.max(0, row.age_s) : 0,
				available: row.available !== false
			};
		}),
		configured: source.configured !== false
	};
}

export interface ReadingGroup {
	area: string;
	readings: Reading[];
}

/**
 * Rooms in the order their newest reading arrived, the unplaced readings last
 * under "elsewhere" — so the room something just happened in is at the top,
 * and a sensor nobody has assigned to a room is still on the page.
 */
export function groupReadings(readings: Reading[]): ReadingGroup[] {
	const byArea = new Map<string, Reading[]>();
	for (const reading of [...readings].sort((a, b) => a.age_s - b.age_s)) {
		const key = reading.area || '';
		const group = byArea.get(key) ?? [];
		group.push(reading);
		byArea.set(key, group);
	}
	const groups = [...byArea.entries()]
		.filter(([area]) => area)
		.map(([area, rows]) => ({ area, readings: rows }));
	const unplaced = byArea.get('');
	if (unplaced?.length) groups.push({ area: 'elsewhere', readings: unplaced });
	return groups;
}

/** A reading's value as a person reads it: numbers to three figures, words as they are. */
export function readingText(reading: Pick<Reading, 'value' | 'unit' | 'available'>): string {
	if (!reading.available) return 'unavailable';
	if (typeof reading.value === 'number') {
		const magnitude = Math.abs(reading.value);
		const text =
			magnitude >= 1000
				? Math.round(reading.value).toLocaleString()
				: Number.isInteger(reading.value)
					? String(reading.value)
					: magnitude >= 10
						? reading.value.toFixed(1)
						: reading.value.toFixed(2).replace(/0$/, '');
		return reading.unit ? `${text} ${reading.unit}` : text;
	}
	return reading.unit ? `${reading.value} ${reading.unit}` : String(reading.value);
}

/** Apply a `state_changed` to the rows in place, so a reading widget is live. */
export function applyReading(readings: Reading[], state: EntityState | undefined): Reading[] {
	if (!state) return readings;
	return readings.map((reading) => {
		if (reading.entity_id !== state.entity_id) return reading;
		const number = Number(state.state);
		return {
			...reading,
			value: state.state !== '' && Number.isFinite(number) ? number : state.state,
			age_s: 0,
			available: state.state !== 'unavailable' && state.state !== 'unknown'
		};
	});
}

// --- time ----------------------------------------------------------------------

/**
 * "just now", "4 min ago", "3 h ago", "2 d ago": the age of a reading or a
 * change, coarse on purpose — a dashboard is read across a room.
 */
export function ago(seconds: number): string {
	const s = Math.max(0, Math.round(seconds));
	if (s < 45) return 'just now';
	if (s < 3600) return `${Math.round(s / 60)} min ago`;
	if (s < 86400) return `${Math.round(s / 3600)} h ago`;
	return `${Math.round(s / 86400)} d ago`;
}

/** Seconds since an ISO string or an epoch (seconds), or null when there is none. */
export function secondsSince(value: unknown, now = Date.now()): number | null {
	if (value === null || value === undefined || value === '') return null;
	const at =
		typeof value === 'number'
			? value > 1e12
				? value
				: value * 1000
			: Date.parse(String(value));
	if (!Number.isFinite(at)) return null;
	return Math.max(0, (now - at) / 1000);
}

/** An ISO time as a clock reading in the house's own words: "01:35", "Tue 21:14". */
export function clock(value: unknown, now = new Date()): string {
	if (!value) return '—';
	const at = new Date(String(value));
	if (Number.isNaN(at.getTime())) return '—';
	const sameDay = at.toDateString() === now.toDateString();
	const time = at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
	if (sameDay) return time;
	return `${at.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`;
}

// --- an entity tile --------------------------------------------------------------

/** The domains an entity tile can switch — the same set HOUSE › Devices toggles. */
const SWITCHABLE = new Set(['light', 'switch', 'fan', 'siren', 'input_boolean', 'lock']);

export function canSwitch(entityId: string): boolean {
	return SWITCHABLE.has(domainOf(entityId));
}

/**
 * The one service a tile's control calls from where the entity is: the move it
 * can make, as the Devices rows offer it. A lock is LOCK or UNLOCK, never both.
 */
export function switchFor(state: EntityState): { service: string; label: string } | null {
	const domain = domainOf(state.entity_id);
	if (!SWITCHABLE.has(domain)) return null;
	if (domain === 'lock') {
		return state.state === 'locked'
			? { service: 'unlock', label: 'UNLOCK' }
			: { service: 'lock', label: 'LOCK' };
	}
	return state.state === 'on'
		? { service: 'turn_off', label: 'TURN OFF' }
		: { service: 'turn_on', label: 'TURN ON' };
}

/** A state as a person reads it: `above_horizon` → "above horizon", `21.4` + unit. */
export function stateText(state: EntityState | undefined): string {
	if (!state) return '—';
	const unit = state.attributes?.unit_of_measurement;
	const word = String(state.state).replace(/_/g, ' ');
	return unit ? `${word} ${unit}` : word;
}

// --- the sky ---------------------------------------------------------------------

export interface SkyPass {
	state: string;
	reason: string;
	max_alt: number | null;
	direction: string;
	visible: boolean | null;
	next_visible: string;
	tle_age_hours: number | null;
	elements_age_days: number | null;
}

export interface SkyMoon {
	state: string;
	reason: string;
	illumination: number | null;
	next_full: string;
	next_new: string;
}

export interface SkySummary {
	configured: boolean;
	satellite: string;
	now: string;
	pass: SkyPass | null;
	moon: SkyMoon | null;
}

const number = (value: unknown): number | null =>
	typeof value === 'number' && Number.isFinite(value) ? value : null;

export function toSky(raw: unknown): SkySummary {
	const source = (raw ?? {}) as Record<string, unknown>;
	const pass = source.pass as Record<string, unknown> | null | undefined;
	const moon = source.moon as Record<string, unknown> | null | undefined;
	return {
		configured: source.configured === true,
		satellite: String(source.satellite ?? ''),
		now: String(source.now ?? ''),
		pass: pass
			? {
					state: String(pass.state ?? 'unknown'),
					reason: String(pass.reason ?? ''),
					max_alt: number(pass.max_alt),
					direction: String(pass.direction ?? ''),
					visible: typeof pass.visible === 'boolean' ? pass.visible : null,
					next_visible: String(pass.next_visible ?? ''),
					tle_age_hours: number(pass.tle_age_hours),
					elements_age_days: number(pass.elements_age_days)
				}
			: null,
		moon: moon
			? {
					state: String(moon.state ?? 'unknown'),
					reason: String(moon.reason ?? ''),
					illumination: number(moon.illumination),
					next_full: String(moon.next_full ?? ''),
					next_new: String(moon.next_new ?? '')
				}
			: null
	};
}

/**
 * The one sentence a sky tile says when it cannot show a time: honest about
 * WHY — never "no pass" when the truth is "nothing downloaded yet".
 */
export function passSentence(sky: SkySummary): string {
	if (!sky.configured) return 'No sky integration: add sky: to configuration.yaml and the ISS appears here.';
	const pass = sky.pass;
	if (!pass || pass.state === 'unknown') {
		return `Not fetched yet${pass?.reason ? ` — ${pass.reason}` : ''}. The elements download on first start with the network up.`;
	}
	if (pass.state === 'none') return `No pass of ${sky.satellite || 'the ISS'} above the horizon in the next two days.`;
	return '';
}

export function moonSentence(sky: SkySummary): string {
	if (!sky.configured) return '';
	const moon = sky.moon;
	if (!moon || moon.state === 'unknown') {
		return `Not fetched yet${moon?.reason ? ` — ${moon.reason}` : ''}.`;
	}
	return '';
}

// --- a still ---------------------------------------------------------------------

export interface CameraStill {
	/** ok | denied | error | unconfigured */
	status: string;
	configured: boolean;
	camera: string;
	cameras: string[];
	/** The frame as a data: URL, on `ok` only. */
	image: string;
	takenAt: string;
	/** The decision on a refusal: policy_never, a human's no, rate_limited … */
	decision: string;
	message: string;
	auditId: string;
}

export function toStill(raw: unknown): CameraStill {
	const source = (raw ?? {}) as Record<string, unknown>;
	const frame = (source.frame ?? {}) as Record<string, unknown>;
	return {
		status: String(source.status ?? 'error'),
		configured: source.configured !== false,
		camera: String(source.camera ?? ''),
		cameras: Array.isArray(source.cameras) ? source.cameras.map(String) : [],
		image: typeof source.image === 'string' && source.image.startsWith('data:image/') ? source.image : '',
		takenAt: String(frame.taken_at ?? ''),
		decision: String(source.decision ?? ''),
		message: String(source.message ?? source.error ?? ''),
		auditId: String(source.audit_id ?? '')
	};
}

/** Why there is no picture, in one sentence a person can act on. */
export function stillSentence(still: CameraStill): string {
	if (!still.configured || still.status === 'unconfigured') {
		return 'No camera is configured. Add one under vision: cameras: in configuration.yaml and its still appears here.';
	}
	if (still.status === 'denied') {
		const why =
			still.decision === 'policy_never'
				? `${still.camera || 'This camera'} is set to consent: never, so Jarvis will not look — including for a dashboard.`
				: still.decision === 'rate_limited'
					? 'Asked too recently; the camera’s rate limit refused this look.'
					: still.decision === 'timeout' || still.decision === 'no_device'
						? 'Nobody answered the consent question, which counts as no.'
						: `Refused: ${still.message || still.decision || 'consent was not given'}.`;
		return `${why} Every refusal is in vision.audit.`;
	}
	if (still.status !== 'ok') return still.message || 'The camera did not answer.';
	return '';
}

// --- moments -----------------------------------------------------------------

export interface MomentRow {
	id: string;
	kind: string;
	title: string;
	body: string;
	at: number;
	read: boolean;
}

export function toMoments(raw: unknown, limit = DEFAULT_MOMENTS): MomentRow[] {
	const list = (raw as { notifications?: unknown })?.notifications;
	return (Array.isArray(list) ? list : [])
		.map(toMoment)
		.filter((row): row is MomentRow => row !== null)
		.sort((a, b) => b.at - a.at)
		.slice(0, limit);
}

export function toMoment(raw: unknown): MomentRow | null {
	if (!raw || typeof raw !== 'object') return null;
	const row = raw as Record<string, unknown>;
	const id = String(row.id ?? '');
	const title = String(row.title ?? '');
	if (!id || !title) return null;
	// The store says `at`; a live `jarvis_notification` from an older backend
	// said `created`. Either is the moment it landed.
	const at = typeof row.at === 'number' ? row.at : typeof row.created === 'number' ? row.created : 0;
	return {
		id,
		kind: String(row.kind ?? 'task'),
		title,
		body: String(row.body ?? ''),
		at,
		read: row.read === true
	};
}

/** A moment arriving live goes to the top; the same id is replaced, not doubled. */
export function addMoment(rows: MomentRow[], moment: MomentRow | null, limit: number): MomentRow[] {
	if (!moment) return rows;
	return [moment, ...rows.filter((row) => row.id !== moment.id)].slice(0, limit);
}
