// Turning what somebody typed into a settings field back into a value of the
// setting's own type.
//
// A form holds strings. jarvis-core's settings are typed, and it validates
// against that type — so a `number` setting sent as `"30"` is refused, or worse,
// stored as a string that every later read has to guess about. The page used to
// say in a comment that it coerced numbers and then return `raw.trim()` for
// them, which is the string it started with.
//
// Pure, and here rather than in the component, so the table below is testable
// without a browser — the same arrangement as `entityAdmin.ts` and
// `automationDraft.ts`.

import type { SettingRow } from './jarvisClient';

/**
 * The value to send for `raw`, given the setting's declared type.
 *
 * What is deliberately NOT done here is refusing anything. `"twelve"` in a
 * number field is passed through as the string it is, so the server answers
 * with its own message about its own field — the console does not hold the
 * schema, and a client-side "that is not a number" would be a second, weaker
 * validator that has to be kept in step with the real one. Blank is passed
 * through for the same reason: whether a setting may be empty is the server's
 * question, and `Number('')` is 0, which would silently write a zero somebody
 * never typed.
 */
export function coerceSetting(type: SettingRow['type'], raw: string): unknown {
	if (type === 'boolean') return raw === 'true';
	if (type === 'number' || type === 'integer') {
		const trimmed = raw.trim();
		if (!trimmed) return trimmed;
		const value = Number(trimmed);
		if (!Number.isFinite(value)) return trimmed;
		// `Math.trunc`, not `Math.round`: an integer field given 2.9 has been
		// mistyped, and rounding it up hides that behind a value that looks
		// deliberate. Truncating leaves 2, which the server may still refuse.
		return type === 'integer' ? Math.trunc(value) : value;
	}
	return raw;
}
