import { describe, it, expect } from 'vitest';
import {
	PAGE_ITEMS,
	actionFor,
	buildPaletteItems,
	clampIndex,
	filterPalette,
	fuzzyScore,
	hintFor,
	moveIndex,
	toggleFor,
	type PaletteItem
} from './commandPalette';
import type { AreaEntry, DeviceRegistryEntry, EntityRegistryEntry, EntityState } from './jarvisClient';

function state(entity_id: string, s: string, attributes: Record<string, any> = {}): EntityState {
	return { entity_id, state: s, attributes };
}

const areas: AreaEntry[] = [
	{ id: 'lab', name: 'Lab' },
	{ id: 'garage', name: 'Garage' }
];
const devices: DeviceRegistryEntry[] = [{ id: 'dev-1', name: 'Lab Controller', area_id: 'lab' }];
const entries: EntityRegistryEntry[] = [
	{ entity_id: 'light.lab_lights', area_id: 'lab', original_name: 'Lab Lights' },
	{ entity_id: 'sensor.lab_temperature', device_id: 'dev-1', original_name: 'Lab Temperature' },
	{ entity_id: 'cover.garage_door', area_id: 'garage', original_name: 'Garage Door' },
	{ entity_id: 'automation.night_mode', original_name: 'Night Mode' },
	{ entity_id: 'switch.hidden_relay', original_name: 'Hidden Relay', disabled: true }
];
const states: EntityState[] = [
	state('light.lab_lights', 'off', { friendly_name: 'Lab Lights' }),
	state('sensor.lab_temperature', '21.4', { friendly_name: 'Lab Temperature' }),
	state('cover.garage_door', 'closed', { friendly_name: 'Garage Door' }),
	state('automation.night_mode', 'on', { friendly_name: 'Night Mode' }),
	state('switch.hidden_relay', 'off', { friendly_name: 'Hidden Relay' })
];

const source = { states, areas, entries, devices };

describe('toggleFor', () => {
	it('offers the opposite of the current state for a flippable domain', () => {
		expect(toggleFor(state('light.a', 'off'))).toEqual({ domain: 'light', service: 'turn_on' });
		expect(toggleFor(state('light.a', 'on'))).toEqual({ domain: 'light', service: 'turn_off' });
		expect(toggleFor(state('switch.a', 'on'))).toEqual({ domain: 'switch', service: 'turn_off' });
		expect(toggleFor(state('automation.a', 'on'))).toEqual({
			domain: 'automation',
			service: 'turn_off'
		});
	});

	it('refuses domains a blind on/off would be wrong for', () => {
		expect(toggleFor(state('sensor.a', '21'))).toBeUndefined();
		expect(toggleFor(state('cover.a', 'open'))).toBeUndefined();
		expect(toggleFor(state('lock.front', 'locked'))).toBeUndefined();
		expect(toggleFor(state('climate.a', 'heat'))).toBeUndefined();
	});

	it('refuses an entity whose state is not known', () => {
		expect(toggleFor(state('light.a', 'unavailable'))).toBeUndefined();
		expect(toggleFor(state('light.a', 'unknown'))).toBeUndefined();
	});
});

describe('buildPaletteItems', () => {
	const items = buildPaletteItems(source);

	it('always offers every console route, even with no backend', () => {
		const empty = buildPaletteItems({});
		expect(empty).toHaveLength(PAGE_ITEMS.length);
		expect(empty.map((i) => i.href)).toContain('/house/devices');
	});

	it('indexes pages, areas, entities and automations', () => {
		const kinds = new Set(items.map((i) => i.kind));
		expect(kinds).toEqual(new Set(['page', 'area', 'entity', 'automation']));
	});

	it('skips registry entries the backend marked disabled', () => {
		expect(items.find((i) => i.entityId === 'switch.hidden_relay')).toBeUndefined();
	});

	it('resolves an area through the entity, and through its device when unset', () => {
		const light = items.find((i) => i.entityId === 'light.lab_lights')!;
		const sensor = items.find((i) => i.entityId === 'sensor.lab_temperature')!;
		expect(light.detail).toContain('Lab');
		expect(sensor.detail).toContain('Lab');
	});

	it('sends automations to their own page and entities to devices', () => {
		const auto = items.find((i) => i.entityId === 'automation.night_mode')!;
		expect(auto.kind).toBe('automation');
		expect(auto.href).toBe('/house/automations?focus=automation.night_mode');
		const light = items.find((i) => i.entityId === 'light.lab_lights')!;
		expect(light.href).toBe('/house/devices?focus=light.lab_lights');
	});

	it('gives every item a unique id safe to use as a test id', () => {
		const ids = items.map((i) => i.id);
		expect(new Set(ids).size).toBe(ids.length);
		expect(ids).toContain('entity:light.lab_lights');
	});
});

describe('fuzzyScore', () => {
	it('ranks exact above prefix above word-boundary above scattered', () => {
		const exact = fuzzyScore('lab', 'lab')!;
		const prefix = fuzzyScore('lab lights', 'lab')!;
		const boundary = fuzzyScore('the lab lights', 'lab')!;
		const scattered = fuzzyScore('lovely animal barn', 'lab')!;
		expect(exact).toBeGreaterThan(prefix);
		expect(prefix).toBeGreaterThan(boundary);
		expect(boundary).toBeGreaterThan(scattered);
	});

	it('is case-insensitive and ignores surrounding whitespace in the needle', () => {
		expect(fuzzyScore('Lab Lights', '  lab  ')).toBe(fuzzyScore('lab lights', 'lab'));
	});

	it('returns null when a character is missing', () => {
		expect(fuzzyScore('lab lights', 'zzz')).toBeNull();
		expect(fuzzyScore('lab', 'labs')).toBeNull();
	});

	it('matches everything on an empty needle', () => {
		expect(fuzzyScore('anything', '')).toBe(0);
	});
});

describe('filterPalette', () => {
	const items = buildPaletteItems(source);

	it('keeps source order and honours the limit on an empty query', () => {
		expect(filterPalette(items, '').slice(0, 2)).toEqual(items.slice(0, 2));
		expect(filterPalette(items, '', 3)).toHaveLength(3);
	});

	it('narrows to what was typed', () => {
		const hits = filterPalette(items, 'lab lights');
		expect(hits[0].entityId).toBe('light.lab_lights');
		expect(hits.some((i) => i.entityId === 'cover.garage_door')).toBe(false);
	});

	it('matches on the entity id as well as the friendly name', () => {
		const hits = filterPalette(items, 'garage_door');
		expect(hits[0].entityId).toBe('cover.garage_door');
	});

	it('prefers a label hit over a detail hit for the same word', () => {
		// "Garage" is a label (the area) and a detail (the cover's area column).
		const hits = filterPalette(items, 'garage');
		expect(hits[0].kind).toBe('area');
	});

	it('finds pages by keyword, not just by title', () => {
		expect(filterPalette(items, 'rooms')[0].href).toBe('/house/areas');
	});

	it('returns nothing rather than everything when nothing matches', () => {
		expect(filterPalette(items, 'qqqqzz')).toEqual([]);
	});
});

describe('selection', () => {
	it('wraps in both directions', () => {
		expect(moveIndex(0, 1, 3)).toBe(1);
		expect(moveIndex(2, 1, 3)).toBe(0);
		expect(moveIndex(0, -1, 3)).toBe(2);
		expect(moveIndex(1, -3, 3)).toBe(1);
	});

	it('stays at 0 on an empty list', () => {
		expect(moveIndex(0, 1, 0)).toBe(0);
		expect(clampIndex(5, 0)).toBe(0);
	});

	// The list shrinks as you type; the cursor must not point past the end.
	it('pulls a stale selection back into range', () => {
		expect(clampIndex(9, 3)).toBe(2);
		expect(clampIndex(-1, 3)).toBe(0);
		expect(clampIndex(Number.NaN, 3)).toBe(0);
	});
});

describe('actionFor', () => {
	const items = buildPaletteItems(source);
	const light = items.find((i) => i.entityId === 'light.lab_lights')!;
	const sensor = items.find((i) => i.entityId === 'sensor.lab_temperature')!;
	const areaItem = items.find((i) => i.kind === 'area')!;

	it('toggles a flippable entity on plain Enter', () => {
		expect(actionFor(light)).toEqual({
			type: 'call',
			entityId: 'light.lab_lights',
			domain: 'light',
			service: 'turn_on',
			label: 'Lab Lights'
		});
	});

	it('jumps instead when Shift is held', () => {
		expect(actionFor(light, true)).toEqual({
			type: 'navigate',
			href: '/house/devices?focus=light.lab_lights'
		});
	});

	it('jumps for anything that cannot be toggled', () => {
		expect(actionFor(sensor).type).toBe('navigate');
		expect(actionFor(areaItem).type).toBe('navigate');
	});

	it('does nothing for no selection', () => {
		expect(actionFor(undefined)).toEqual({ type: 'none' });
	});

	it('describes what Enter will do', () => {
		expect(hintFor(light)).toContain('turn on');
		expect(hintFor(sensor)).toBe('⏎ open');
		expect(hintFor(undefined)).toBe('');
		const on: PaletteItem = { ...light, toggle: { domain: 'light', service: 'turn_off' } };
		expect(hintFor(on)).toContain('turn off');
	});
});
