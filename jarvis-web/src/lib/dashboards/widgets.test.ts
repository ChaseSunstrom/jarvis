// The arithmetic behind the house widgets (M63): grouping readings by room,
// deciding what a tile's one control is, reading the three commands' payloads,
// and — the part that matters most — the sentence each says when the thing
// is not there. A widget that drew a blank in those cases is the failure the
// four-states rule exists to prevent, so the sentences are pinned here.
import { describe, it, expect } from 'vitest';
import {
	addMoment,
	ago,
	applyReading,
	canSwitch,
	clock,
	groupReadings,
	moonSentence,
	passSentence,
	readingText,
	secondsSince,
	stateText,
	stillSentence,
	switchFor,
	toMoments,
	toReadings,
	toSky,
	toStill,
	type Reading
} from './widgets';

const reading = (over: Partial<Reading> = {}): Reading => ({
	entity_id: 'sensor.x',
	name: 'X',
	value: 1,
	unit: '',
	device_class: '',
	area: '',
	age_s: 0,
	available: true,
	...over
});

describe('readings', () => {
	it('reads the payload, keeping a dead sensor flagged rather than dropped', () => {
		const payload = toReadings({
			configured: false,
			readings: [
				{ entity_id: 'sensor.a', name: 'A', value: 12.5, unit: '°C', area: 'Garage', age_s: 30, available: true },
				{ entity_id: 'sensor.dead', name: 'Dead', value: 'unavailable', available: false }
			]
		});
		expect(payload.configured).toBe(false);
		expect(payload.readings).toHaveLength(2);
		expect(payload.readings[0]).toMatchObject({ value: 12.5, unit: '°C', area: 'Garage' });
		expect(payload.readings[1].available).toBe(false);
		expect(toReadings(undefined)).toEqual({ readings: [], configured: true });
	});

	it('groups by room, newest room first, the unplaced last under "elsewhere"', () => {
		const groups = groupReadings([
			reading({ entity_id: 'sensor.hall', area: '', age_s: 5 }),
			reading({ entity_id: 'sensor.garage', area: 'Garage', age_s: 600 }),
			reading({ entity_id: 'sensor.kitchen', area: 'Kitchen', age_s: 10 }),
			reading({ entity_id: 'sensor.fridge', area: 'Kitchen', age_s: 900 })
		]);
		expect(groups.map((g) => g.area)).toEqual(['Kitchen', 'Garage', 'elsewhere']);
		expect(groups[0].readings.map((r) => r.entity_id)).toEqual(['sensor.kitchen', 'sensor.fridge']);
	});

	it('writes a value a person reads', () => {
		expect(readingText(reading({ value: 12.5, unit: '°C' }))).toBe('12.5 °C');
		expect(readingText(reading({ value: 1234.6, unit: 'W' }))).toBe('1,235 W');
		expect(readingText(reading({ value: 91 }))).toBe('91');
		expect(readingText(reading({ value: 0.456, unit: 'bar' }))).toBe('0.46 bar');
		expect(readingText(reading({ value: 'open', unit: '' }))).toBe('open');
		expect(readingText(reading({ available: false }))).toBe('unavailable');
	});

	it('applies a state change in place, so the widget is live', () => {
		const rows = [reading({ entity_id: 'sensor.a', value: 1, age_s: 500 }), reading({ entity_id: 'sensor.b' })];
		const next = applyReading(rows, {
			entity_id: 'sensor.a',
			state: '2.5',
			attributes: {}
		});
		expect(next[0]).toMatchObject({ value: 2.5, age_s: 0, available: true });
		expect(next[1]).toBe(rows[1]);
		expect(applyReading(rows, { entity_id: 'sensor.a', state: 'unavailable', attributes: {} })[0].available).toBe(false);
		expect(applyReading(rows, undefined)).toBe(rows);
	});
});

describe('time', () => {
	it('is coarse on purpose', () => {
		expect(ago(10)).toBe('just now');
		expect(ago(240)).toBe('4 min ago');
		expect(ago(3 * 3600 + 100)).toBe('3 h ago');
		expect(ago(2 * 86400)).toBe('2 d ago');
	});

	it('takes an ISO string or an epoch in seconds or milliseconds', () => {
		const now = Date.parse('2026-08-26T18:00:00Z');
		expect(secondsSince('2026-08-26T17:59:00Z', now)).toBe(60);
		expect(secondsSince(now / 1000 - 120, now)).toBe(120);
		expect(secondsSince(now - 5000, now)).toBe(5);
		expect(secondsSince(undefined, now)).toBeNull();
		expect(secondsSince('not a time', now)).toBeNull();
	});

	it('reads a clock, with the day only when it is not today', () => {
		const now = new Date('2026-08-26T18:00:00');
		expect(clock('2026-08-26T21:14:00', now)).toMatch(/^21:14$/);
		expect(clock('2026-08-27T01:35:00', now)).toMatch(/^\w{3} 01:35$/);
		expect(clock('', now)).toBe('—');
		expect(clock('nope', now)).toBe('—');
	});
});

describe('an entity tile', () => {
	it('switches the domains the Devices rows switch, and nothing else', () => {
		expect(canSwitch('light.x')).toBe(true);
		expect(canSwitch('lock.front')).toBe(true);
		expect(canSwitch('sensor.x')).toBe(false);
		expect(canSwitch('sun.sun')).toBe(false);
	});

	it('offers the one move the entity can make from where it is', () => {
		expect(switchFor({ entity_id: 'light.x', state: 'on', attributes: {} })).toEqual({ service: 'turn_off', label: 'TURN OFF' });
		expect(switchFor({ entity_id: 'switch.x', state: 'off', attributes: {} })).toEqual({ service: 'turn_on', label: 'TURN ON' });
		expect(switchFor({ entity_id: 'lock.x', state: 'locked', attributes: {} })).toEqual({ service: 'unlock', label: 'UNLOCK' });
		expect(switchFor({ entity_id: 'lock.x', state: 'unlocked', attributes: {} })).toEqual({ service: 'lock', label: 'LOCK' });
		expect(switchFor({ entity_id: 'sensor.x', state: '21', attributes: {} })).toBeNull();
	});

	it('writes a state a person reads', () => {
		expect(stateText({ entity_id: 'sun.sun', state: 'above_horizon', attributes: {} })).toBe('above horizon');
		expect(stateText({ entity_id: 'sensor.t', state: '21.4', attributes: { unit_of_measurement: '°C' } })).toBe('21.4 °C');
		expect(stateText(undefined)).toBe('—');
	});
});

describe('the sky', () => {
	it('reads the summary and says "not fetched yet" with the reason, never a guess', () => {
		const sky = toSky({
			configured: true,
			satellite: 'ISS (ZARYA)',
			pass: { state: 'unknown', reason: 'no orbital elements are cached' },
			moon: { state: 'unknown', reason: 'the ephemeris has not been downloaded' }
		});
		expect(passSentence(sky)).toMatch(/^Not fetched yet — no orbital elements are cached/);
		expect(moonSentence(sky)).toBe('Not fetched yet — the ephemeris has not been downloaded.');
	});

	it('says how the sky gets here when there is no integration', () => {
		const sky = toSky({ configured: false });
		expect(sky.pass).toBeNull();
		expect(passSentence(sky)).toMatch(/add sky: to configuration.yaml/);
		expect(moonSentence(sky)).toBe('');
	});

	it('has nothing to explain when there is a pass', () => {
		const sky = toSky({
			configured: true,
			satellite: 'ISS (ZARYA)',
			pass: { state: '2026-08-27T01:35:00+01:00', max_alt: 11, direction: 'south-east', visible: false, next_visible: '2026-08-27T04:45:00+01:00', tle_age_hours: 12 },
			moon: { state: 'waxing gibbous', illumination: 98.1, next_full: '2026-08-28T05:18:00+01:00' }
		});
		expect(passSentence(sky)).toBe('');
		expect(sky.pass?.max_alt).toBe(11);
		expect(sky.moon?.illumination).toBe(98.1);
		expect(passSentence(toSky({ configured: true, satellite: 'ISS (ZARYA)', pass: { state: 'none' } }))).toMatch(/No pass of ISS/);
	});
});

describe('a still', () => {
	it('keeps the frame only when it is a data URL, and says why there is none', () => {
		const ok = toStill({ status: 'ok', configured: true, camera: 'Garden', image: 'data:image/jpeg;base64,AAAA', frame: { taken_at: '2026-08-26T18:00:00Z' } });
		expect(ok.image).toBe('data:image/jpeg;base64,AAAA');
		expect(ok.takenAt).toBe('2026-08-26T18:00:00Z');
		expect(stillSentence(ok)).toBe('');
		expect(toStill({ status: 'ok', image: 'javascript:alert(1)' }).image).toBe('');
	});

	it('explains a refusal in the camera’s own terms', () => {
		const never = toStill({ status: 'denied', configured: true, camera: 'Front Door', decision: 'policy_never', consent: 'never', audit_id: 'a1' });
		expect(stillSentence(never)).toMatch(/consent: never/);
		expect(stillSentence(never)).toMatch(/vision\.audit/);
		expect(stillSentence(toStill({ status: 'denied', decision: 'rate_limited' }))).toMatch(/rate limit/);
		expect(stillSentence(toStill({ status: 'denied', decision: 'timeout' }))).toMatch(/Nobody answered/);
		expect(stillSentence(toStill({ status: 'denied', decision: 'declined', message: 'the user said no' }))).toMatch(/the user said no/);
	});

	it('says how a camera is added when there is none', () => {
		expect(stillSentence(toStill({ status: 'unconfigured', configured: false }))).toMatch(/vision: cameras:/);
		expect(stillSentence(toStill({ status: 'error', error: 'Front Door timed out' }))).toBe('Front Door timed out');
	});
});

describe('moments', () => {
	it('reads the list newest first and to the limit', () => {
		const rows = toMoments(
			{
				notifications: [
					{ id: 'a', kind: 'task', title: 'Older', at: 100 },
					{ id: 'b', kind: 'briefing', title: 'Newer', at: 200, read: true },
					{ id: '', title: 'no id' },
					{ id: 'c', kind: 'watch', title: 'Newest', at: 300 }
				]
			},
			2
		);
		expect(rows.map((r) => r.id)).toEqual(['c', 'b']);
		expect(rows[1].read).toBe(true);
	});

	it('puts a moment arriving live at the top without doubling it', () => {
		const rows = toMoments({ notifications: [{ id: 'a', title: 'A', at: 1 }, { id: 'b', title: 'B', at: 2 }] });
		const next = addMoment(rows, { id: 'b', kind: 'task', title: 'B again', body: '', at: 3, read: false }, 6);
		expect(next.map((r) => r.id)).toEqual(['b', 'a']);
		expect(addMoment(rows, null, 6)).toBe(rows);
		expect(addMoment(rows, { id: 'c', kind: 'task', title: 'C', body: '', at: 4, read: false }, 2)).toHaveLength(2);
	});

	it('takes `created` from an older live event as the moment it landed', () => {
		expect(toMoments({ notifications: [{ id: 'a', title: 'A', created: 42 }] })[0].at).toBe(42);
	});
});
