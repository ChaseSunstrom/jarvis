// Motion policy in one place.
//
// Two rules the rest of the app leans on:
//
//  1. `prefers-reduced-motion: reduce` wins over everything. CSS handles the
//     declarative half (see `styles/base.css`); this module is the half that
//     JavaScript has to ask about — the boot sequence, which cannot be reduced
//     to a fast transition and has to be skipped outright.
//  2. A staggered list must have a *ceiling*. 200 rows at 26 ms each is a five
//     second cascade; capping the delay means the effect reads as one wave and
//     the last row is never left waiting.

/** Milliseconds between one staggered row starting and the next. */
export const STAGGER_STEP_MS = 26;

/** The most any row will wait, however far down the list it is. */
export const STAGGER_CAP_MS = 320;

/** Durations, mirroring `--jv-dur-*`. */
export const DURATION = {
	instant: 90,
	fast: 120,
	base: 180,
	slow: 320,
	pulse: 620
} as const;

/**
 * Entrance delay for row `index`, capped at [STAGGER_CAP_MS].
 *
 * Rows past the cap all start together, which is the intent: the eye reads the
 * first dozen as a cascade and the rest as "the list arrived".
 */
export function staggerDelay(
	index: number,
	step = STAGGER_STEP_MS,
	cap = STAGGER_CAP_MS
): number {
	if (!Number.isFinite(index) || index <= 0) return 0;
	return Math.min(Math.floor(index) * step, cap);
}

/** `staggerDelay` as a ready-made inline style, for use in `style=`. */
export function staggerStyle(index: number): string {
	return `--jv-delay:${staggerDelay(index)}ms`;
}

/** What `prefersReducedMotion` needs from a window. */
export interface MediaQueryHost {
	matchMedia?: (query: string) => { matches: boolean } | null;
}

/**
 * Whether the user asked for reduced motion.
 *
 * Defaults to `true` when there is no `matchMedia` to ask — during SSR, and in
 * any environment that cannot animate, "do not animate" is the safe answer.
 */
export function prefersReducedMotion(host: MediaQueryHost | undefined = globalThis as any): boolean {
	const mm = host?.matchMedia;
	if (typeof mm !== 'function') return true;
	try {
		return mm.call(host, '(prefers-reduced-motion: reduce)')?.matches === true;
	} catch {
		return true;
	}
}

/**
 * Subscribe to changes in the reduced-motion preference. Returns an unsubscribe
 * function; a host without `matchMedia` gets a no-op rather than a crash.
 */
export function watchReducedMotion(
	onChange: (reduced: boolean) => void,
	host: any = globalThis
): () => void {
	const mm = host?.matchMedia;
	if (typeof mm !== 'function') return () => {};
	let query: any;
	try {
		query = mm.call(host, '(prefers-reduced-motion: reduce)');
	} catch {
		return () => {};
	}
	if (!query) return () => {};
	const handler = (e: any) => onChange(e?.matches === true);
	if (typeof query.addEventListener === 'function') {
		query.addEventListener('change', handler);
		return () => query.removeEventListener('change', handler);
	}
	if (typeof query.addListener === 'function') {
		query.addListener(handler);
		return () => query.removeListener(handler);
	}
	return () => {};
}
