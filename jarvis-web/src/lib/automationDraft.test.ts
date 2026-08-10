import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
	MODES,
	TRIGGER_PLATFORMS,
	blankForm,
	formFromRow,
	parseForm,
	readOnlyNote,
	type DraftForm
} from './automationDraft';
import type { AutomationRow } from './jarvisClient';

function form(overrides: Partial<DraftForm> = {}): DraftForm {
	return { ...blankForm(), alias: 'Porch light', ...overrides };
}

describe('parseForm', () => {
	it('turns a filled-in form into a draft', () => {
		const result = parseForm(form());
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft.alias).toBe('Porch light');
		expect(result.draft.trigger).toEqual([{ platform: 'time', at: '21:00:00' }]);
		expect(result.draft.action).toHaveLength(1);
	});

	it('omits an empty description and condition rather than sending them', () => {
		// Sent as `''`/`[]` they come back from the server and make an untouched
		// form look edited.
		const result = parseForm(form({ description: '  ', condition: '[]' }));
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect('description' in result.draft).toBe(false);
		expect('condition' in result.draft).toBe(false);
	});

	it('keeps a condition that was actually written', () => {
		const result = parseForm(
			form({ condition: '[{"condition":"state","entity_id":"sun.sun","state":"below_horizon"}]' })
		);
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft.condition).toHaveLength(1);
	});

	it('accepts a single step that was pasted without brackets', () => {
		// The engine accepts one step or a list; refusing here would make the
		// only fix "add brackets", which is a rule about JSON, not automations.
		const result = parseForm(form({ trigger: '{"platform":"state","entity_id":"x.y"}' }));
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft.trigger).toEqual([{ platform: 'state', entity_id: 'x.y' }]);
	});

	it.each([
		[{ alias: '   ' }, 'alias', 'name'],
		[{ alias: 'x'.repeat(200) }, 'alias', 'under'],
		[{ description: 'x'.repeat(600) }, 'description', 'under'],
		[{ mode: 'sideways' }, 'mode', 'Mode must be'],
		[{ trigger: '[]' }, 'trigger', 'at least one trigger'],
		[{ trigger: '[{}]' }, 'trigger', 'platform'],
		[{ trigger: '[{"platform":"sun"}]' }, 'trigger', 'no `sun` trigger'],
		[{ trigger: '[1,2]' }, 'trigger', 'must be an object'],
		[{ trigger: '{oops' }, 'trigger', 'trigger:'],
		[{ action: '[]' }, 'action', 'at least one action'],
		[{ action: 'nope' }, 'action', 'action:']
	])('refuses %j and points at the field', (overrides, field, message) => {
		const result = parseForm(form(overrides));
		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.field).toBe(field);
		expect(result.error.toLowerCase()).toContain(message.toLowerCase());
	});
});

describe('formFromRow', () => {
	const row: AutomationRow = {
		id: 'ui_abc',
		entity_id: 'automation.porch',
		alias: 'Porch light',
		description: 'at dusk',
		mode: 'restart',
		trigger: [{ platform: 'time', at: '21:00:00' }],
		condition: [],
		action: [{ service: 'light.turn_on' }],
		editable: true
	};

	it('round-trips an automation through the form unchanged', () => {
		// The property that matters: opening an automation and saving it without
		// typing anything must not alter it.
		const result = parseForm(formFromRow(row));
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft).toEqual({
			alias: 'Porch light',
			description: 'at dusk',
			mode: 'restart',
			trigger: row.trigger,
			action: row.action
		});
	});

	it('survives a row with fields the server left off', () => {
		const sparse = { id: 'ui_x', entity_id: 'automation.x', alias: 'X', editable: true } as
			unknown as AutomationRow;
		const loaded = formFromRow(sparse);
		expect(loaded.mode).toBe('single');
		expect(loaded.trigger).toBe('[]');
	});
});

describe('readOnlyNote', () => {
	it('names the file and says what to do instead', () => {
		const note = readOnlyNote({ alias: 'Hallway motion' } as AutomationRow);
		expect(note).toContain('Hallway motion');
		expect(note).toContain('automations.yaml');
	});
});

describe('the mirrored engine tables', () => {
	// These lists exist twice: once in Python, once here. A drift test is what
	// makes the duplication safe — without it, a platform added to the engine
	// would be refused by this form for as long as nobody noticed.
	function pythonSource(relative: string): string {
		return readFileSync(
			fileURLToPath(new URL(`../../../jarvis-core/jarvis/${relative}`, import.meta.url)),
			'utf-8'
		);
	}

	it('lists exactly the trigger platforms the engine can attach', () => {
		const source = pythonSource('automation/triggers.py');
		const table = source.split('TRIGGER_PLATFORMS: dict')[1]?.split('}')[0] ?? '';
		expect(table, 'could not find TRIGGER_PLATFORMS in triggers.py').not.toBe('');
		const found = [...table.matchAll(/^\s*"([a-z_]+)":/gm)].map((m) => m[1]);
		expect(found.length).toBeGreaterThan(5);
		expect([...found].sort()).toEqual([...TRIGGER_PLATFORMS].sort());
	});

	it('lists exactly the modes the store accepts', () => {
		const source = pythonSource('automation/authored.py');
		const line = source.match(/if mode not in \(([^)]*)\)/)?.[1] ?? '';
		expect(line, 'could not find the mode check in authored.py').not.toBe('');
		const found = [...line.matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
		expect([...found].sort()).toEqual([...MODES].sort());
	});
});
