// The console's half of what a tier means.
//
// `tests/contracts/tool_tiers.json` is the definition; jarvis-core's
// `test_tool_tiers_contract.py` and the phone's `policy_truth_table_test.py`
// read the same file. The console draws the approval banner, so the thing it
// must not get wrong is WHICH tier asks — the MCP config comment claimed tier 2
// confirmed, and it never has.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { SUMMARY_FIELD, argumentsOf, headlineOf } from './approvals';

const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../tests/contracts/tool_tiers.json', import.meta.url)),
		'utf8'
	)
);

/** What the console does with a tier, in one place, from the contract. */
export function asksFirst(tier: number): boolean {
	const entry = CONTRACT.tiers[String(tier)];
	return Boolean(entry?.asks_first);
}

describe('the tier contract', () => {
	it('has exactly three tiers', () => {
		expect(Object.keys(CONTRACT.tiers).sort()).toEqual(['1', '2', '3']);
	});

	it('says only tier 3 asks first', () => {
		expect(asksFirst(1)).toBe(false);
		expect(asksFirst(2)).toBe(false);
		expect(asksFirst(3)).toBe(true);
	});

	it('gives every tier a meaning a person can read', () => {
		for (const tier of ['1', '2', '3']) {
			expect(CONTRACT.tiers[tier].means.length, tier).toBeGreaterThan(20);
		}
	});

	it('states the rules the console relies on', () => {
		// The console shows a taint warning on an approval that followed
		// untrusted content, and a raised tier is why that approval exists.
		expect(CONTRACT.rules.untrusted_raises).toContain('raised');
		// A server may only raise: the console must never show "no approval
		// needed" for something the device would hold.
		expect(CONTRACT.rules.a_server_may_only_raise).toContain('never lower');
	});

	it('records what an MCP server’s tools default to', () => {
		expect(CONTRACT.default_for_mcp.value).toBe(2);
		expect(asksFirst(CONTRACT.default_for_mcp.value)).toBe(false);
	});

	it('reads the held sentence off the field the contract names, and falls back to the name', () => {
		// M67: the server composes "Change Wake word (voice.wake_word) from
		// hey_jarvis to ok_nabu" from the pinned arguments. The console must
		// read it from the field the contract names — a rename on either side
		// would leave the card on raw JSON with nothing failing — and must
		// still draw a request that has none the way it always did.
		const rule = CONTRACT.rules.held_summary;
		expect(rule.field).toBe(SUMMARY_FIELD);
		expect(rule.means).toContain('PINNED');

		const sentence = 'Change Wake word (voice.wake_word) from hey_jarvis to ok_nabu';
		const held = {
			tool: 'change_setting',
			arguments: { key: 'voice.wake_word', value: 'ok_nabu', previous: 'hey_jarvis' },
			[rule.field]: sentence
		};
		expect(headlineOf(held)).toBe(sentence);

		const plain = { tool: 'lock_control', arguments: { entity_id: ['lock.front_door'] } };
		expect(headlineOf(plain)).toBe('lock_control');
		expect(argumentsOf(plain)).toBe('entity_id: lock.front_door');
		// Whitespace-only is "none": a surface must not draw an empty headline.
		expect(headlineOf({ ...plain, summary: '   ' })).toBe('lock_control');
	});
});
