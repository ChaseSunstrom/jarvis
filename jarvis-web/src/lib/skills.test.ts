import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
	canForget,
	describeCatalogue,
	describeDisabled,
	describeSource,
	inReadingOrder,
	whyNotReference,
	type SkillListing,
	type SkillRow
} from './skills';

const row = (over: Partial<SkillRow> = {}): SkillRow => ({
	name: 'pdf',
	description: 'Use when the job involves reading or filling in a PDF.',
	source: 'installed',
	enabled: false,
	license: 'Apache-2.0',
	version: '',
	origin: 'anthropics/skills',
	chars: 2960,
	...over
});

describe('describeSource', () => {
	it('names the repository an installed skill came from', () => {
		// Without it, "installed" is a claim about a stranger's instructions
		// with no way to ask who the stranger was.
		expect(describeSource(row())).toBe('installed from anthropics/skills');
	});

	it('falls back when the origin was not recorded', () => {
		expect(describeSource(row({ origin: '' }))).toBe('installed');
	});

	it('distinguishes shipped, written-here and unloadable', () => {
		expect(describeSource(row({ source: 'builtin' }))).toBe('ships with Jarvis');
		expect(describeSource(row({ source: 'authored' }))).toBe('written here');
		expect(describeSource(row({ source: 'broken' }))).toBe('will not load');
	});
});

describe('describeDisabled', () => {
	it('says an installed skill being off is the design, not a fault', () => {
		expect(describeDisabled(row())).toContain('read it');
	});

	it('says nothing at all about a skill that is on', () => {
		expect(describeDisabled(row({ enabled: true }))).toBe('');
	});

	it('says nothing about a broken one — the problem line already did', () => {
		// Two explanations for one row is one too many, and the second is the
		// wrong one: a broken skill is not "off", it never loaded.
		expect(describeDisabled(row({ source: 'broken' }))).toBe('');
	});
});

describe('canForget', () => {
	it('refuses to offer removal for a skill that ships with Jarvis', () => {
		// The file is inside the package. A REMOVE button that cannot remove
		// is a lie, and switching it off achieves what the reader wanted.
		expect(canForget(row({ source: 'builtin' }))).toBe(false);
	});

	it('allows it for everything else, broken included', () => {
		expect(canForget(row())).toBe(true);
		expect(canForget(row({ source: 'authored' }))).toBe(true);
		// Especially broken: a file that will not load is exactly the one
		// somebody wants gone.
		expect(canForget(row({ source: 'broken' }))).toBe(true);
	});
});

describe('describeCatalogue', () => {
	const listing = (over: Partial<SkillListing> = {}): SkillListing => ({
		skills: [row({ enabled: true, chars: 100 }), row({ name: 'docx', chars: 900 })],
		catalogue_chars: 64,
		sources: [],
		install_enabled: false,
		...over
	});

	it('counts only what is on, in both numbers', () => {
		// The claim the feature rests on: the prompt carries descriptions, and
		// the bodies — the expensive part — are not loaded until Jarvis opens
		// one. A cost line that added the off skill's 900 characters would be
		// measuring something nobody pays for.
		const said = describeCatalogue(listing());
		expect(said).toContain('1 skill on');
		expect(said).toContain('64 ');
		expect(said).toContain('100 characters');
		expect(said).not.toContain('1000');
	});

	it('says plainly when nothing is on', () => {
		expect(describeCatalogue(listing({ skills: [row()] }))).toContain('No skills are on');
	});

	it('is empty before the listing arrives', () => {
		expect(describeCatalogue(null)).toBe('');
	});
});

describe('whyNotReference', () => {
	/**
	 * The console's check is a copy of jarvis-core's `parse_reference` +
	 * `permits`, so the install form can refuse a pasted URL without a round
	 * trip. The copy is for the message and never for the decision — but a
	 * copy that DRIFTS is worse than none, because the form accepts what the
	 * server rejects and the reader blames the form.
	 *
	 * So the answers are not written here. `tests/contracts/skill_reference.json`
	 * is read by BOTH suites: jarvis-core asserts its parser against it in
	 * `test_the_console_and_the_server_agree_about_references`, and this
	 * asserts the console against the same rows.
	 */
	const table = JSON.parse(
		readFileSync(
			fileURLToPath(new URL('../../../tests/contracts/skill_reference.json', import.meta.url)),
			'utf-8'
		)
	) as { cases: { sources: string[]; reference: string; ok: boolean }[] };

	it('agrees with jarvis-core on every row of the shared table', () => {
		expect(table.cases.length).toBeGreaterThanOrEqual(20);
		for (const c of table.cases) {
			const problem = whyNotReference(c.reference, c.sources);
			expect(
				problem === '',
				`reference=${JSON.stringify(c.reference)} sources=${JSON.stringify(c.sources)}: ` +
					`console said ${problem || '<permitted>'}, table says ${c.ok}`
			).toBe(c.ok);
		}
	});

	it('refuses a URL by explaining where the host comes from', () => {
		// The likely paste. "Invalid" would send somebody to fix the wrong
		// half of it; the host is the half that is not theirs to choose.
		const said = whyNotReference('https://github.com/anthropics/skills/tree/main/skills/pdf', [
			'anthropics/skills'
		]);
		expect(said).toContain('allow-list');
	});

	it('names the repository it refused and the ones it would take', () => {
		const said = whyNotReference('some-rando/evil/skills/rm-rf', ['anthropics/skills']);
		expect(said).toContain('some-rando/evil');
		expect(said).toContain('anthropics/skills');
	});

	it('says what to do when nothing is permitted yet', () => {
		expect(whyNotReference('anthropics/skills/skills/pdf', [])).toContain('nothing yet');
	});
});

describe('inReadingOrder', () => {
	it('puts what needs attention first and what never changes last', () => {
		// Broken wants fixing, installed wants reading and switching on,
		// yours you already know, and shipped is furniture.
		const ordered = inReadingOrder([
			row({ name: 'house-automations', source: 'builtin' }),
			row({ name: 'bin-night', source: 'authored' }),
			row({ name: 'half-written', source: 'broken' }),
			row({ name: 'pdf', source: 'installed' })
		]);
		expect(ordered.map((r) => r.name)).toEqual([
			'half-written',
			'pdf',
			'bin-night',
			'house-automations'
		]);
	});

	it('breaks ties by name, so the list does not shuffle between polls', () => {
		const ordered = inReadingOrder([
			row({ name: 'zed', source: 'builtin' }),
			row({ name: 'alpha', source: 'builtin' })
		]);
		expect(ordered.map((r) => r.name)).toEqual(['alpha', 'zed']);
	});

	it('leaves the array it was handed alone', () => {
		const rows = [row({ name: 'zed', source: 'builtin' }), row({ name: 'alpha' })];
		inReadingOrder(rows);
		expect(rows.map((r) => r.name)).toEqual(['zed', 'alpha']);
	});
});
