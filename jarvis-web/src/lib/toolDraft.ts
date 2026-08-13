/**
 * Turning what someone typed into a tool jarvis-core will accept.
 *
 * Same shape as `automationDraft.ts` and for the same reasons: the service
 * block is structured data so it is edited as JSON, the checks mirror
 * `jarvis/llm/authored_tools.py` so a mistake is caught with the cursor still
 * in the field, and the server re-runs every one of them regardless.
 *
 * `toolDraft.test.ts` pins the name pattern and the method list against the
 * Python so the two cannot drift.
 */

import type { ToolDraft, ToolRow } from './jarvisClient';

/** Mirrors `NAME_RE` in authored_tools.py. */
export const NAME_RE = /^[a-z][a-z0-9_]{2,47}$/;

export const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'] as const;

export const MAX_DESCRIPTION = 400;

export interface ToolForm {
	name: string;
	description: string;
	tier: string;
	method: string;
	url: string;
	/** JSON object: field name -> {description, required}. */
	fields: string;
	/** JSON object: header name -> value template. */
	headers: string;
	/** JSON body for the non-GET methods. Blank means none. */
	payload: string;
}

export type ToolResult =
	| { ok: true; draft: ToolDraft }
	| { ok: false; error: string; field: keyof ToolForm };

export function blankToolForm(): ToolForm {
	return {
		name: '',
		description: '',
		tier: '1',
		method: 'GET',
		url: 'https://example.test/api?q={{ query }}',
		fields: JSON.stringify({ query: { description: 'search text', required: true } }, null, 2),
		headers: '{}',
		payload: ''
	};
}

export function toolFormFromRow(row: ToolRow): ToolForm {
	const service = row.service ?? {};
	const payload = service.payload ?? service.json ?? service.body;
	return {
		name: row.name ?? '',
		description: row.description ?? '',
		tier: String(row.tier ?? 1),
		method: String(service.method ?? 'GET'),
		url: String(service.url ?? ''),
		fields: JSON.stringify(service.fields ?? {}, null, 2),
		headers: JSON.stringify(service.headers ?? {}, null, 2),
		payload: payload === undefined ? '' : JSON.stringify(payload, null, 2)
	};
}

function parseObject(
	text: string,
	field: 'fields' | 'headers' | 'payload'
): { ok: true; value: unknown } | { ok: false; error: string } {
	const trimmed = text.trim();
	if (!trimmed) return { ok: true, value: undefined };
	try {
		return { ok: true, value: JSON.parse(trimmed) };
	} catch (err) {
		return { ok: false, error: `${field}: ${(err as Error).message}` };
	}
}

export function parseToolForm(form: ToolForm): ToolResult {
	const name = form.name.trim().toLowerCase();
	if (!name) return { ok: false, error: 'Give it a name.', field: 'name' };
	if (!NAME_RE.test(name)) {
		return {
			ok: false,
			error:
				'The name must be 3-48 characters, lowercase letters, digits and ' +
				'underscores, starting with a letter.',
			field: 'name'
		};
	}

	const description = form.description.trim();
	if (!description) {
		// Not a nicety: without one the model has nothing to decide from, and
		// will either never call the tool or call it for the wrong thing.
		return { ok: false, error: 'Describe what it does, or the model cannot use it.', field: 'description' };
	}
	if (description.length > MAX_DESCRIPTION) {
		return {
			ok: false,
			error: `The description must be under ${MAX_DESCRIPTION} characters.`,
			field: 'description'
		};
	}

	const tier = Number(form.tier);
	if (![1, 2, 3].includes(tier)) {
		return { ok: false, error: 'Tier must be 1, 2 or 3.', field: 'tier' };
	}

	const method = (form.method || 'GET').trim().toUpperCase();
	if (!(METHODS as readonly string[]).includes(method)) {
		return { ok: false, error: `Method must be one of ${METHODS.join(', ')}.`, field: 'method' };
	}

	const url = form.url.trim();
	if (!url) return { ok: false, error: 'The service needs a url.', field: 'url' };
	if (!/^https?:\/\//i.test(url)) {
		return { ok: false, error: 'The url must start with http:// or https://.', field: 'url' };
	}

	const fields = parseObject(form.fields, 'fields');
	if (!fields.ok) return { ok: false, error: fields.error, field: 'fields' };
	if (fields.value !== undefined && (typeof fields.value !== 'object' || Array.isArray(fields.value))) {
		return { ok: false, error: 'fields must be an object keyed by field name.', field: 'fields' };
	}

	const headers = parseObject(form.headers, 'headers');
	if (!headers.ok) return { ok: false, error: headers.error, field: 'headers' };
	if (
		headers.value !== undefined &&
		(typeof headers.value !== 'object' || Array.isArray(headers.value))
	) {
		return { ok: false, error: 'headers must be an object.', field: 'headers' };
	}
	for (const [key, value] of Object.entries((headers.value ?? {}) as Record<string, unknown>)) {
		// Written straight onto the wire, so a line break is header injection.
		if (/[\r\n]/.test(String(key)) || /[\r\n]/.test(String(value))) {
			return { ok: false, error: 'Headers cannot contain line breaks.', field: 'headers' };
		}
	}

	const payload = parseObject(form.payload, 'payload');
	if (!payload.ok) return { ok: false, error: payload.error, field: 'payload' };

	const service: Record<string, unknown> = { url, method };
	if (fields.value && Object.keys(fields.value as object).length) service.fields = fields.value;
	if (headers.value && Object.keys(headers.value as object).length) service.headers = headers.value;
	if (payload.value !== undefined) service.payload = payload.value;

	return { ok: true, draft: { name, description, tier, service } };
}

/** The least a thing needs to be offered by the test runner's picker. */
export interface Named {
	name: string;
}

/**
 * What the test runner's `<select>` may offer.
 *
 * The picker used to be filled straight from the filtered catalogue while the
 * selection was independent state, so typing a filter that excluded the chosen
 * tool left a `<select>` whose value matched no option — which browsers render
 * as an empty box, with RUN still enabled and still pointing at the tool you
 * can no longer see. So whatever is selected is always among the options, even
 * when the filter says otherwise, and it goes first because it is the one the
 * button is about to run.
 */
export function runnerOptions<T extends Named>(
	catalogue: readonly T[],
	visible: readonly T[],
	selected: string
): T[] {
	if (!selected || visible.some((tool) => tool.name === selected)) return [...visible];
	const chosen = catalogue.find((tool) => tool.name === selected);
	return chosen ? [chosen, ...visible] : [...visible];
}

/**
 * The name the picker should be showing, given its options.
 *
 * Falls to the first option when the selection names nothing on offer — a tool
 * that was deleted from another tab, or the empty string on first load.
 */
export function runnerSelection(options: readonly Named[], selected: string): string {
	if (selected && options.some((tool) => tool.name === selected)) return selected;
	return options[0]?.name ?? '';
}

/** Why a row cannot be edited, in words that say what to do instead. */
export function toolReadOnlyNote(row: ToolRow): string {
	return (
		`“${row.name}” is built in or comes from a *.tool.yaml manifest, not from ` +
		'this console, so it cannot be changed here.'
	);
}
