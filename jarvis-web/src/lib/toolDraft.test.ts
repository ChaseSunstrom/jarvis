import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
	METHODS,
	NAME_RE,
	blankToolForm,
	parseToolForm,
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
