// Voice-pipeline numbers the browser needs and the operator may want to change.
//
// In `$lib/server` rather than beside the route that serves it: a `+server.ts`
// is a route module, and SvelteKit reserves its exports for the HTTP verbs and
// a short list of page options. A helper exported from one works today and is
// the kind of thing a framework upgrade is entitled to reject — and it cannot
// be unit-tested without pulling the route's `$env` import in with it.

/** The end-of-speech pause, in ms, when nothing has been configured. */
export const DEFAULT_HANGOVER_MS = 550;

/** The narrowest and widest pause that is still a usable setting. */
export const MIN_HANGOVER_MS = 200;
export const MAX_HANGOVER_MS = 3000;

/**
 * `JARVIS_VAD_HANGOVER_MS`, held to something usable.
 *
 * Clamped on the server rather than trusted in the browser because both ends of
 * the range are one slipped keystroke from a plausible value, and both are
 * indistinguishable from a broken microphone: `50` ends every turn on the first
 * comma, `50000` never ends one at all.
 */
export function clampHangover(
	raw: string | undefined,
	fallback = DEFAULT_HANGOVER_MS
): number {
	const value = Number(raw);
	if (!Number.isFinite(value) || value <= 0) return fallback;
	return Math.min(Math.max(Math.round(value), MIN_HANGOVER_MS), MAX_HANGOVER_MS);
}
