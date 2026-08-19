import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
	METHODS,
	NAME_RE,
	blankToolForm,
	parseToolForm,
	dedupeByName,
	runnerOptions,
	runnerSelection,
	toolFormFromRow,
	toolReadOnlyNote,
	type ToolForm
} from './toolDraft';
import type { ToolRow } from './jarvisClient';

function form(overrides: Partial<ToolForm> = {}): ToolForm {
	return { ...blankToolForm(), name: 'paperless_search', description: 'Search docs', ...overrides };
}

describe('parseToolForm', () => {
	it('turns a filled-in form into a draft', () => {
		const result = parseToolForm(form());
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft.name).toBe('paperless_search');
		expect(result.draft.tier).toBe(1);
		expect(result.draft.service.method).toBe('GET');
		expect(result.draft.service.fields).toHaveProperty('query');
	});

	it('lowercases the name, because that is what the model will be told', () => {
		const result = parseToolForm(form({ name: '  Paperless_Search ' }));
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft.name).toBe('paperless_search');
	});

	it('omits an empty fields, headers and payload rather than sending them', () => {
		const result = parseToolForm(form({ fields: '{}', headers: '   ', payload: '' }));
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect('fields' in result.draft.service).toBe(false);
		expect('headers' in result.draft.service).toBe(false);
		expect('payload' in result.draft.service).toBe(false);
	});

	it.each([
		[{ name: '' }, 'name', 'name'],
		[{ name: 'Has Spaces' }, 'name', 'lowercase'],
		[{ name: 'ab' }, 'name', '3-48'],
		[{ description: '  ' }, 'description', 'describe'],
		[{ description: 'x'.repeat(500) }, 'description', 'under'],
		[{ tier: '9' }, 'tier', 'Tier'],
		[{ method: 'TRACE' }, 'method', 'Method'],
		[{ url: '' }, 'url', 'url'],
		// Only http(s): a file:// tool would read the disk of the box.
		[{ url: 'file:///etc/shadow' }, 'url', 'http'],
		[{ fields: '{oops' }, 'fields', 'fields:'],
		[{ fields: '[1,2]' }, 'fields', 'object'],
		[{ headers: '{"X":"a\\r\\nEvil: 1"}' }, 'headers', 'line breaks'],
		[{ payload: 'not json' }, 'payload', 'payload:']
	])('refuses %j and points at the field', (overrides, field, message) => {
		const result = parseToolForm(form(overrides));
		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.field).toBe(field);
		expect(result.error.toLowerCase()).toContain(message.toLowerCase());
	});
});

describe('toolFormFromRow', () => {
	const row: ToolRow = {
		name: 'paperless_search',
		description: 'Search docs',
		tier: 2,
		editable: true,
		service: {
			method: 'POST',
			url: 'http://paperless.lan/api',
			fields: { query: { required: true } },
			headers: { Authorization: 'Token abc' },
			payload: { q: '{{ query }}' }
		}
	};

	it('round-trips a tool through the form unchanged', () => {
		const result = parseToolForm(toolFormFromRow(row));
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.draft).toEqual({
			name: 'paperless_search',
			description: 'Search docs',
			tier: 2,
			service: {
				url: 'http://paperless.lan/api',
				method: 'POST',
				fields: { query: { required: true } },
				headers: { Authorization: 'Token abc' },
				payload: { q: '{{ query }}' }
			}
		});
	});

	it('survives a row the server sent without a service block', () => {
		const loaded = toolFormFromRow({ name: 'builtin', description: 'x', tier: 1, editable: false });
		expect(loaded.method).toBe('GET');
		expect(loaded.payload).toBe('');
	});
});

describe('the test runner\'s picker', () => {
	const catalogue = [
		{ name: 'lock_control' },
		{ name: 'paperless_search' },
		{ name: 'weather' }
	];

	it('offers what the filter shows', () => {
		const visible = [catalogue[1]];
		expect(runnerOptions(catalogue, visible, 'paperless_search')).toEqual(visible);
	});

	it('keeps the selected tool on the list even when the filter hides it', () => {
		// The bug: options came from the filtered list while the selection was
		// independent state, so filtering away the chosen tool left a `<select>`
		// whose value matched no option — an empty box, with RUN still lit.
		const options = runnerOptions(catalogue, [catalogue[1]], 'lock_control');
		expect(options.map((t) => t.name)).toEqual(['lock_control', 'paperless_search']);
	});

	it('does not offer a selection the catalogue has never heard of', () => {
		expect(runnerOptions(catalogue, [catalogue[0]], 'deleted_tool')).toEqual([catalogue[0]]);
	});

	it('holds a valid selection and rescues an invalid one', () => {
		expect(runnerSelection(catalogue, 'weather')).toBe('weather');
		// Deleted from another tab, or nothing chosen yet: fall to the first
		// option rather than leaving the box blank and the button enabled.
		expect(runnerSelection(catalogue, 'deleted_tool')).toBe('lock_control');
		expect(runnerSelection(catalogue, '')).toBe('lock_control');
		expect(runnerSelection([], 'anything')).toBe('');
	});
});

describe('toolReadOnlyNote', () => {
	it('names the tool and says why', () => {
		const note = toolReadOnlyNote({ name: 'lock_control' } as ToolRow);
		expect(note).toContain('lock_control');
		expect(note).toContain('*.tool.yaml');
	});
});

describe('the mirrored server rules', () => {
	function python(): string {
		return readFileSync(
			fileURLToPath(new URL('../../../jarvis-core/jarvis/llm/authored_tools.py', import.meta.url)),
			'utf-8'
		);
	}

	it('uses the same name pattern the store enforces', () => {
		const source = python().match(/NAME_RE = re\.compile\(r"([^"]+)"\)/)?.[1];
		expect(source, 'could not find NAME_RE in authored_tools.py').toBeTruthy();
		expect(NAME_RE.source).toBe(source);
	});

	it('offers exactly the methods the store accepts', () => {
		const block = python().match(/ALLOWED_METHODS = frozenset\(\{([^}]*)\}\)/)?.[1] ?? '';
		expect(block, 'could not find ALLOWED_METHODS').not.toBe('');
		const found = [...block.matchAll(/"([A-Z]+)"/g)].map((m) => m[1]);
		expect([...found].sort()).toEqual([...METHODS].sort());
	});
});


describe('dedupeByName', () => {
	it('keeps the first of a repeated name', () => {
		const rows = [
			{ name: 'turn_on', description: 'from the model' },
			{ name: 'lock_control', description: 'a' },
			{ name: 'turn_on', description: 'a projection of it' }
		];
		expect(dedupeByName(rows).map((r) => r.name)).toEqual(['turn_on', 'lock_control']);
		expect(dedupeByName(rows)[0].description).toBe('from the model');
	});

	it('drops a nameless row rather than keying on an empty string', () => {
		expect(dedupeByName([{ name: '' }, { name: 'a' }]).map((r) => r.name)).toEqual(['a']);
	});

	it('leaves a clean list alone', () => {
		const rows = [{ name: 'a' }, { name: 'b' }];
		expect(dedupeByName(rows)).toEqual(rows);
	});
});

describe('runnerOptions never yields a duplicate key', () => {
	// A keyed {#each} over a duplicate throws `each_key_duplicate` in Svelte 5,
	// which takes out the whole block: the observed symptom was a Test-run
	// control with no options, no error, and a disabled button — a page that
	// looked like a backend with no tools at all.
	const dup = [
		{ name: 'turn_on' },
		{ name: 'turn_on' },
		{ name: 'lock_control' }
	];

	it('with nothing selected', () => {
		const names = runnerOptions(dup, dup, '').map((t) => t.name);
		expect(names).toEqual([...new Set(names)]);
	});

	it('with a selection already in the visible list', () => {
		const names = runnerOptions(dup, dup, 'turn_on').map((t) => t.name);
		expect(names).toEqual([...new Set(names)]);
	});

	it('with a selection pulled in from the catalogue', () => {
		const visible = [{ name: 'turn_on' }, { name: 'turn_on' }];
		const names = runnerOptions(dup, visible, 'lock_control').map((t) => t.name);
		expect(names).toEqual([...new Set(names)]);
		expect(names).toContain('lock_control');
	});
});
