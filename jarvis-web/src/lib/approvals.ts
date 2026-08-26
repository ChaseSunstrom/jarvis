/**
 * How a held request is put into words, in one place.
 *
 * The banner used to render every held action as `key: value` pairs, which
 * reads well for `entity_id: lock.front_door` and not at all for a setting:
 * `key: llm.options.temperature · value: 0.2` does not say what it was
 * before, and "from what" is the whole decision. jarvis-core now composes a
 * sentence for a tool that has one — from the PINNED arguments, never from
 * the model's words — and carries it on the request as `summary` (M67).
 *
 * The field's name is the contract's (`tests/contracts/tool_tiers.json`,
 * `rules.held_summary.field`), read by `tierContract.test.ts`, so a rename on
 * one side cannot leave the console drawing raw JSON while the server thinks
 * it has said something readable.
 */
import type { PendingApproval } from './jarvisClient';

/** The request field that carries the server's sentence. */
export const SUMMARY_FIELD = 'summary';

/** The server's sentence for this request, or '' when it has none. */
export function summaryOf(req: PendingApproval): string {
	// Indexed by the constant, not written as `req.summary`: if the interface's
	// field is ever renamed away from what the contract says, this stops
	// compiling rather than quietly reading `undefined`.
	const value = req[SUMMARY_FIELD];
	return typeof value === 'string' ? value.trim() : '';
}

/**
 * What the human reads first: the sentence when there is one, else the tool.
 *
 * Never the model's `description` and never anything composed here from the
 * arguments — a sentence a hostile page could have shaped must not be the
 * headline of a consent card, and a sentence the console made up would be a
 * second place to get "from what" wrong.
 */
export function headlineOf(req: PendingApproval): string {
	return summaryOf(req) || req.tool;
}

/**
 * The arguments, rendered compactly — what the human is agreeing to when the
 * server gave no sentence. Empty values are dropped so a card does not read
 * `answer: null · choices: ` for the fields a tool did not use.
 */
export function argumentsOf(req: PendingApproval): string {
	const args = req.arguments ?? {};
	const parts = Object.entries(args)
		.filter(([, v]) => v !== null && v !== undefined && v !== '')
		.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : JSON.stringify(v)}`);
	return parts.join(' · ') || 'no arguments';
}
