/**
 * Turning what someone typed into an automation jarvis-core will accept.
 *
 * The trigger, condition and action lists are structured data, so the form
 * edits them as JSON. That is not a shortcut around a nicer builder — it is the
 * escape hatch a builder would still need, and it costs no dependency: a YAML
 * parser in the browser is a supply-chain decision, and `JSON.parse` is already
 * here and unambiguous.
 *
 * The checks below deliberately mirror `jarvis/automation/authored.py`. The
 * server remains the authority and re-runs all of them — this exists so the
 * common mistakes are caught with the cursor still in the field, rather than
 * after a round trip. `automationDraft.test.ts` pins the trigger platforms
 * against the engine's own table so the two cannot drift.
 */

import type { AutomationDraft, AutomationRow } from './jarvisClient';

/**
 * Trigger platforms jarvis-core can attach.
 *
 * Mirrors `TRIGGER_PLATFORMS` in `jarvis/automation/triggers.py`, which no
 * integration extends. An automation with a platform that is not here saves,
 * lists, looks correct and never fires — so it is worth refusing early.
 */
export const TRIGGER_PLATFORMS = [
	'event',
	'home_assistant_start',
	'homeassistant',
	'homeassistant_start',
	'jarvis',
	'jarvis_start',
	'mqtt',
	'numeric_state',
	'shutdown',
	'start',
	'state',
	'template',
	'time',
	'time_pattern',
	'webhook'
] as const;

export const MODES = ['single', 'restart', 'queued', 'parallel'] as const;

export const MAX_ALIAS = 120;
export const MAX_DESCRIPTION = 500;

/** The editable fields, as the form holds them: JSON lists stay text. */
export interface DraftForm {
	alias: string;
	description: string;
	mode: string;
	trigger: string;
	condition: string;
	action: string;
}

export type DraftResult =
	| { ok: true; draft: AutomationDraft }
	| { ok: false; error: string; field: keyof DraftForm };

const EXAMPLE_TRIGGER = [{ platform: 'time', at: '21:00:00' }];
const EXAMPLE_ACTION = [{ service: 'light.turn_on', target: { entity_id: 'light.porch' } }];

export function blankForm(): DraftForm {
	return {
		alias: '',
		description: '',
		mode: 'single',
		trigger: JSON.stringify(EXAMPLE_TRIGGER, null, 2),
		condition: '[]',
		action: JSON.stringify(EXAMPLE_ACTION, null, 2)
	};
}

/** Load an existing automation into the form. */
export function formFromRow(row: AutomationRow): DraftForm {
	return {
		alias: row.alias ?? '',
		description: row.description ?? '',
		mode: row.mode || 'single',
		trigger: JSON.stringify(row.trigger ?? [], null, 2),
		condition: JSON.stringify(row.condition ?? [], null, 2),
		action: JSON.stringify(row.action ?? [], null, 2)
	};
}

/** Parse one JSON list field. Returns the list or a message naming the field. */
function parseList(
	text: string,
	field: 'trigger' | 'condition' | 'action'
): { ok: true; value: unknown[] } | { ok: false; error: string } {
	const trimmed = text.trim();
	if (!trimmed) return { ok: true, value: [] };
	let parsed: unknown;
	try {
		parsed = JSON.parse(trimmed);
	} catch (err) {
		// The parser's own message names the character offset, which is the
		// only part of a JSON error anyone can act on.
		return { ok: false, error: `${field}: ${(err as Error).message}` };
	}
	// A single step, unwrapped, is what someone pasting one example writes.
	// The engine accepts it, so accepting it here too avoids a refusal whose
	// only fix is adding brackets.
	const list = Array.isArray(parsed) ? parsed : [parsed];
	for (const step of list) {
		if (step === null || typeof step !== 'object' || Array.isArray(step)) {
			return { ok: false, error: `Each ${field} must be an object.` };
		}
	}
	return { ok: true, value: list };
}

/**
 * Validate a filled-in form. On success the draft is ready to send.
 *
 * The `field` on a failure is what lets the page focus the offending input
 * rather than showing an error at the top and leaving the user to hunt.
 */
export function parseForm(form: DraftForm): DraftResult {
	const alias = form.alias.trim();
	if (!alias) return { ok: false, error: 'Give it a name.', field: 'alias' };
	if (alias.length > MAX_ALIAS) {
		return { ok: false, error: `The name must be under ${MAX_ALIAS} characters.`, field: 'alias' };
	}

	const description = form.description.trim();
	if (description.length > MAX_DESCRIPTION) {
		return {
			ok: false,
			error: `The description must be under ${MAX_DESCRIPTION} characters.`,
			field: 'description'
		};
	}

	const mode = (form.mode || 'single').trim().toLowerCase();
	if (!(MODES as readonly string[]).includes(mode)) {
		return { ok: false, error: `Mode must be one of ${MODES.join(', ')}.`, field: 'mode' };
	}

	const triggers = parseList(form.trigger, 'trigger');
	if (!triggers.ok) return { ok: false, error: triggers.error, field: 'trigger' };
	if (!triggers.value.length) {
		return {
			ok: false,
			error: 'Give it at least one trigger, or nothing will ever run it.',
			field: 'trigger'
		};
	}
	for (const step of triggers.value as Record<string, unknown>[]) {
		const platform = String(step.platform ?? step.trigger ?? '')
			.trim()
			.toLowerCase();
		if (!platform) return { ok: false, error: 'Every trigger needs a `platform`.', field: 'trigger' };
		if (!(TRIGGER_PLATFORMS as readonly string[]).includes(platform)) {
			return {
				ok: false,
				error: `There is no \`${platform}\` trigger. Available: ${TRIGGER_PLATFORMS.join(', ')}.`,
				field: 'trigger'
			};
		}
	}

	const conditions = parseList(form.condition, 'condition');
	if (!conditions.ok) return { ok: false, error: conditions.error, field: 'condition' };

	const actions = parseList(form.action, 'action');
	if (!actions.ok) return { ok: false, error: actions.error, field: 'action' };
	if (!actions.value.length) {
		return {
			ok: false,
			error: 'Give it at least one action, or it will run and do nothing.',
			field: 'action'
		};
	}

	const draft: AutomationDraft = {
		alias,
		mode,
		trigger: triggers.value,
		action: actions.value
	};
	// Omitted rather than sent empty: jarvis-core's allowlist accepts these
	// keys, but an empty description stored as `''` would come back and make
	// the form look edited when it was not.
	if (description) draft.description = description;
	if (conditions.value.length) draft.condition = conditions.value;
	return { ok: true, draft };
}

/** Why a row cannot be edited, in words that say what to do instead. */
export function readOnlyNote(row: AutomationRow): string {
	return (
		`“${row.alias}” comes from your automations.yaml, not from this console. ` +
		'Edit that file and reload automations to change it.'
	);
}
