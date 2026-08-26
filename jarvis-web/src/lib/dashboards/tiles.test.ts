// Each house widget, rendered (M63).
//
// Server-rendered with fixture data, the way `components/ssr.test.ts` renders
// the console's components: what is pinned is the markup a kind produces for
// its data — and, more than that, the sentence each says when its data is not
// there. A widget's empty state is a promise about how the thing gets here,
// and a promise is worth a test.
import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import EntityTile from './EntityTile.svelte';
import Readings from './Readings.svelte';
import CameraStill from './CameraStill.svelte';
import SkyTonight from './SkyTonight.svelte';
import Moments from './Moments.svelte';
import { groupReadings, toSky, toStill } from './widgets';

const html = (component: any, props: Record<string, unknown>): string =>
	render(component, { props }).body.replace(/<!--.*?-->/g, '');

const NOW = Date.parse('2026-08-26T18:00:00Z');

describe('an entity tile', () => {
	it('shows the name, the state with its unit, when it changed, and the one switch', () => {
		const out = html(EntityTile, {
			entityId: 'light.hall_lamp',
			state: {
				entity_id: 'light.hall_lamp',
				state: 'on',
				attributes: { friendly_name: 'Hall Lamp' },
				last_changed: new Date(NOW - 240_000).toISOString()
			},
			now: NOW
		});
		expect(out).toContain('Hall Lamp');
		expect(out).toMatch(/data-testid="tile-state-light.hall_lamp"[^>]*>\s*on\s*</);
		expect(out).toContain('changed 4 min ago');
		expect(out).toContain('data-testid="toggle-light.hall_lamp"');
		expect(out).toContain('TURN OFF');
		expect(out).toContain('aria-pressed="true"');
	});

	it('gives a lock the move it can make, and a sensor no control at all', () => {
		const lock = html(EntityTile, {
			entityId: 'lock.front_door',
			state: { entity_id: 'lock.front_door', state: 'locked', attributes: {} },
			now: NOW
		});
		expect(lock).toContain('data-testid="unlock-lock.front_door"');
		expect(lock).toContain('UNLOCK');
		expect(lock).not.toContain('toggle-');
		const sensor = html(EntityTile, {
			entityId: 'sensor.lab_temperature',
			state: { entity_id: 'sensor.lab_temperature', state: '21.4', attributes: { unit_of_measurement: '°C' } },
			now: NOW
		});
		expect(sensor).toContain('21.4 °C');
		expect(sensor).not.toContain('<button');
	});

	it('says so when the entity is not on this Jarvis', () => {
		const out = html(EntityTile, { entityId: 'light.nowhere', state: undefined });
		expect(out).toContain('data-testid="tile-why"');
		expect(out).toContain('No entity called');
		expect(out).toContain('light.nowhere');
		expect(out).not.toContain('<button');
	});
});

describe('readings', () => {
	it('draws rooms with their readings, values tabular', () => {
		const groups = groupReadings([
			{ entity_id: 'sensor.a', name: 'Garage temperature', value: 12.5, unit: '°C', device_class: 'temperature', area: 'Garage', age_s: 60, available: true },
			{ entity_id: 'sensor.b', name: 'Fridge power', value: 91, unit: 'W', device_class: 'power', area: 'Kitchen', age_s: 5, available: true }
		]);
		const out = html(Readings, { groups, configured: true });
		expect(out.indexOf('Kitchen')).toBeLessThan(out.indexOf('Garage'));
		expect(out).toContain('data-testid="reading-sensor.b"');
		expect(out).toContain('91 W');
		expect(out).toContain('12.5 °C');
		expect(out).toContain('1 min ago');
	});

	it('says what would be here and how it gets here, in the words that fit', () => {
		expect(html(Readings, { groups: [], configured: false })).toContain('sensors:');
		expect(html(Readings, { groups: [], configured: true })).toContain('MQTT discovery');
		expect(html(Readings, { groups: [], configured: true, area: 'Attic' })).toContain('No readings in Attic');
	});
});

describe('a still', () => {
	it('shows the frame with its caption', () => {
		const still = toStill({ status: 'ok', configured: true, camera: 'Garden', image: 'data:image/jpeg;base64,AAAA', frame: { taken_at: '2026-08-26T17:58:00Z' } });
		const out = html(CameraStill, { still });
		expect(out).toContain('data-testid="camera-still"');
		expect(out).toContain('src="data:image/jpeg;base64,AAAA"');
		expect(out).toContain('alt="Still from Garden"');
	});

	it('says why there is no picture: consent, or no camera at all', () => {
		const denied = html(CameraStill, { still: toStill({ status: 'denied', configured: true, camera: 'Front Door', decision: 'policy_never' }) });
		expect(denied).toContain('data-testid="camera-why"');
		expect(denied).toContain('data-decision="policy_never"');
		expect(denied).toContain('consent: never');
		expect(denied).not.toContain('<img');
		const none = html(CameraStill, { still: toStill({ status: 'unconfigured', configured: false }) });
		expect(none).toContain('No camera is configured');
		expect(html(CameraStill, { still: null })).toContain('Asking the camera');
	});
});

describe('the sky', () => {
	it('shows the rise time as the figure, where and how high, and the moon', () => {
		const sky = toSky({
			configured: true,
			satellite: 'ISS (ZARYA)',
			pass: { state: '2026-08-27T01:35:00+01:00', max_alt: 11, direction: 'south-east', visible: false, next_visible: '2026-08-27T04:45:00+01:00', tle_age_hours: 12 },
			moon: { state: 'waxing gibbous', illumination: 98.1, next_full: '2026-08-28T05:18:00+01:00' }
		});
		const out = html(SkyTonight, { sky });
		expect(out).toContain('data-testid="sky-pass"');
		expect(out).toContain('ISS next rises');
		expect(out).toContain('01:35');
		expect(out).toContain('south-east');
		expect(out).toContain('up to 11°');
		expect(out).toContain('elements 12 h old');
		expect(out).toContain('waxing gibbous');
		expect(out).toContain('98% lit');
	});

	it('says "not fetched yet" and why, rather than guessing a time', () => {
		const sky = toSky({ configured: true, satellite: 'ISS (ZARYA)', pass: { state: 'unknown', reason: 'no elements cached' }, moon: { state: 'unknown', reason: 'no ephemeris' } });
		const out = html(SkyTonight, { sky });
		expect(out).toContain('data-state="unknown"');
		expect(out).toContain('Not fetched yet — no elements cached');
		expect(out).toContain('Not fetched yet — no ephemeris');
		expect(html(SkyTonight, { sky: toSky({ configured: false }) })).toContain('add sky:');
	});
});

describe('moments', () => {
	it('lists the newest first with kind, title and age', () => {
		const out = html(Moments, {
			moments: [
				{ id: 'n2', kind: 'briefing', title: 'Morning briefing', body: '', at: NOW / 1000 - 7200, read: true },
				{ id: 'n1', kind: 'task', title: 'Finished: research', body: '', at: NOW / 1000 - 600, read: false }
			],
			now: NOW
		});
		expect(out).toContain('data-testid="moment-n2"');
		expect(out).toContain('data-kind="briefing"');
		expect(out).toContain('Morning briefing');
		expect(out).toContain('2 h ago');
		expect(out).toContain('10 min ago');
		expect(out).not.toContain('<button');
	});

	it('says how a moment gets here when there is none', () => {
		const out = html(Moments, { moments: [] });
		expect(out).toContain('data-testid="moments-empty"');
		expect(out).toContain('No moments yet');
	});
});
