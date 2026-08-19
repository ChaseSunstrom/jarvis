import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
	NO_AREA,
	areaOptions,
	describeChanges,
	entityChanges,
	formFor,
	formatAliases,
	isUnchanged,
	parseAliases,
	platformNote,
	whyNotEntityId
} from './entityAdmin';
import type { EntityRegistryEntry } from './jarvisClient';

const entry = (over: Partial<EntityRegistryEntry> = {}): EntityRegistryEntry => ({
	entity_id: 'light.kitchen',
	platform: 'demo',
	...over
});

describe('formFor', () => {
	it('leaves the name empty when the entity has no override', () => {
		// Pre-filling with the platform's name invites you to save a value you
		// did not choose, which then stops tracking the platform.
		expect(formFor(entry({ original_name: 'Kitchen Lights' })).name).toBe('');
		expect(formFor(entry({ name: 'Over the sink' })).name).toBe('Over the sink');
	});

	it('treats an absent exposed flag as exposed', () => {
		// jarvis-core's default: an entity nobody has thought about is visible to
		// the assistant. A form that opened with the box unticked would propose
		// hiding it, and a careless SAVE would take it away.
		expect(formFor(entry()).exposed).toBe(true);
		expect(formFor(entry({ exposed: false })).exposed).toBe(false);
		expect(formFor(undefined).exposed).toBe(true);
	});

	it('survives an entry that is not there at all', () => {
		expect(formFor(undefined)).toEqual({
			entityId: '',
			name: '',
			areaId: NO_AREA,
			aliases: '',
			exposed: true,
			hidden: false,
			disabled: false
		});
	});
});

describe('aliases', () => {
	it('splits, trims and drops empties', () => {
		expect(parseAliases('  kitchen light ,, the big lamp,  ')).toEqual([
			'kitchen light',
			'the big lamp'
		]);
		expect(parseAliases('')).toEqual([]);
		expect(parseAliases('   ')).toEqual([]);
	});

	it('deduplicates case-insensitively, because the backend matches that way', () => {
		// Keeping both would look like two aliases and behave like one.
		expect(parseAliases('Kitchen, kitchen, KITCHEN')).toEqual(['Kitchen']);
	});

	it('round-trips through the text box', () => {
		const list = ['kitchen light', 'the big lamp'];
		expect(parseAliases(formatAliases(list))).toEqual(list);
	});
});

describe('entityChanges', () => {
	it('sends only what changed', () => {
		// Sending every field means a save from a stale form silently reverts
		// whatever someone else changed in between.
		const current = entry({ name: 'Sink', area_id: 'kitchen', exposed: true });
		const form = { ...formFor(current), name: 'Over the sink' };

		expect(entityChanges(current, form)).toEqual({ name: 'Over the sink' });
	});

	it('is empty when nothing was touched, so SAVE is a no-op', () => {
		const current = entry({ name: 'Sink', area_id: 'kitchen', aliases: ['big lamp'] });
		expect(isUnchanged(entityChanges(current, formFor(current)))).toBe(true);
	});

	it("clears an area with '' rather than null", () => {
		// jarvis-core ignores null-valued fields, so null would leave the old area
		// in place while the form showed none — an update that appears to fail at
		// random.
		const current = entry({ area_id: 'kitchen' });
		const changes = entityChanges(current, { ...formFor(current), areaId: NO_AREA });

		expect(changes.area_id).toBe('');
		expect(changes.area_id).not.toBeNull();
	});

	it('trims the name and notices a cleared one', () => {
		const current = entry({ name: 'Sink' });
		expect(entityChanges(current, { ...formFor(current), name: '  Sink  ' })).toEqual({});
		expect(entityChanges(current, { ...formFor(current), name: '' })).toEqual({ name: '' });
	});

	it('notices an exposure change in both directions', () => {
		const exposed = entry({ exposed: true });
		expect(entityChanges(exposed, { ...formFor(exposed), exposed: false })).toEqual({
			exposed: false
		});

		const hiddenFromModel = entry({ exposed: false });
		expect(entityChanges(hiddenFromModel, { ...formFor(hiddenFromModel), exposed: true })).toEqual({
			exposed: true
		});
	});

	it('does not resend aliases that only changed order of whitespace', () => {
		const current = entry({ aliases: ['big lamp', 'sink light'] });
		const form = { ...formFor(current), aliases: 'big lamp,   sink light' };
		expect(entityChanges(current, form)).toEqual({});
	});
});

describe('describeChanges', () => {
	it('names the two edits with consequences beyond this page', () => {
		expect(describeChanges({ exposed: false })).toContain('hide it from the assistant');
		expect(describeChanges({ disabled: true })).toContain('disable it entirely');
	});

	it('reads as a sentence when several things changed', () => {
		const text = describeChanges({ name: 'Sink', area_id: '', exposed: false });
		expect(text).toBe('rename to “Sink”, remove from its area, hide it from the assistant');
	});

	it('is empty when nothing changed', () => {
		expect(describeChanges({})).toBe('');
	});
});

describe('areaOptions', () => {
	it('puts the no-area choice first and sorts the rest', () => {
		const options = areaOptions([
			{ id: 'study', name: 'Study' },
			{ id: 'kitchen', name: 'Kitchen' }
		] as any);

		expect(options[0].id).toBe(NO_AREA);
		expect(options.map((o) => o.name)).toEqual(['— no area —', 'Kitchen', 'Study']);
	});
});

describe('platformNote', () => {
	it('says where the demo house is actually switched off', () => {
		// People try to delete the fake devices one at a time. They cannot be:
		// the integration recreates them every start.
		const note = platformNote(entry({ platform: 'demo' }));
		expect(note).toContain('demo:');
		expect(note).toContain('Settings');
		expect(note).toContain('comes back');
	});

	it('names the integration for anything else, and says nothing when unknown', () => {
		expect(platformNote(entry({ platform: 'mqtt' }))).toBe('Created by the mqtt integration.');
		expect(platformNote(entry({ platform: undefined }))).toBe('');
		expect(platformNote(undefined)).toBe('');
	});
});

describe('whyNotEntityId', () => {
	/**
	 * The console's rules are a copy of jarvis-core's `EntityRegistry.rename`,
	 * so the form can refuse before a round trip. Neither side owns the
	 * answers — `tests/contracts/entity_id_rename.json` is read by both suites,
	 * and jarvis-core asserts the real registry against the same rows.
	 *
	 * `taken` rows are skipped: a collision is something only the server knows
	 * about, and pretending otherwise would be a rule the console cannot keep.
	 */
	const table = JSON.parse(
		readFileSync(
			fileURLToPath(new URL('../../../tests/contracts/entity_id_rename.json', import.meta.url)),
			'utf-8'
		)
	) as { cases: { from: string; to: string; ok: boolean; taken?: string[] }[] };

	it('agrees with jarvis-core on every row of the shared table', () => {
		const rows = table.cases.filter((row) => !row.taken);
		expect(rows.length).toBeGreaterThanOrEqual(12);
		for (const row of rows) {
			const problem = whyNotEntityId(row.to, row.from);
			expect(
				problem === '',
				`${row.from} -> ${JSON.stringify(row.to)}: console said ` +
					`${problem || '<allowed>'}, table says ${row.ok}`
			).toBe(row.ok);
		}
	});

	it('says which domain it has to stay in, not just that it is wrong', () => {
		expect(whyNotEntityId('switch.kitchen', 'light.kitchen')).toContain('light');
	});

	it('shows the shape rather than restating the rule', () => {
		expect(whyNotEntityId('kitchen', 'light.kitchen')).toContain('light.kitchen');
	});
});
