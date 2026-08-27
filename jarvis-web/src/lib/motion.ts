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

import { tokenMs } from './tokens';

/** Milliseconds between one staggered row starting and the next (`--jv-stagger-step`). */
export const STAGGER_STEP_MS = tokenMs('--jv-stagger-step');

/** The most any row will wait, however far down the list it is (`--jv-stagger-cap`). */
export const STAGGER_CAP_MS = tokenMs('--jv-stagger-cap');

/** Durations, read from `--jv-dur-*` so a token change is the only change. */
export const DURATION = {
	instant: tokenMs('--jv-dur-instant'),
	fast: tokenMs('--jv-dur-fast'),
	base: tokenMs('--jv-dur-base'),
	slow: tokenMs('--jv-dur-slow'),
	pulse: tokenMs('--jv-dur-pulse')
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

// --- the primitives (M44) ---------------------------------------------------
//
// Every animation in the console comes from one of these, and each is a token
// lookup rather than a number. That is what makes "consistent motion" a
// property the token lint can enforce instead of a thing everybody remembers
// differently: a hand-written `transition: all 0.3s ease` is a hard-coded
// value, and `scripts/verify/token_lint.py` fails on it.
//
// All six honour reduced motion by returning a zero-duration style. Not "a
// faster animation" — nothing. Somebody who has asked their operating system
// for less movement has told you what they want, and 80 ms of it is still
// movement.

/** The named curves. `standard` is the default; `spring` overshoots. */
export const EASE = {
	standard: 'var(--jv-ease-standard)',
	decelerate: 'var(--jv-ease-decelerate)',
	accelerate: 'var(--jv-ease-accelerate)',
	spring: 'var(--jv-ease-spring)'
} as const;

export type EaseName = keyof typeof EASE;
export type DurationName = keyof typeof DURATION;

export interface MotionOptions {
	duration?: DurationName;
	ease?: EaseName;
	delay?: number;
	/** Distance for a slide, in px. Ignored by the others. */
	distance?: number;
	/** Start scale for a scale-in. Ignored by the others. */
	from?: number;
	reduced?: boolean;
}

function inert(reduced: boolean | undefined): boolean {
	return reduced ?? prefersReducedMotion();
}

function base(options: MotionOptions): string {
	const ms = DURATION[options.duration ?? 'base'];
	const ease = EASE[options.ease ?? 'standard'];
	const delay = options.delay ? ` ${Math.round(options.delay)}ms` : '';
	return `${ms}ms ${ease}${delay}`;
}

/** Fade in. The one to reach for when nothing else is warranted. */
export function fade(options: MotionOptions = {}): string {
	if (inert(options.reduced)) return 'opacity:1;';
	return `animation: jv-fade ${base(options)} both;`;
}

/**
 * Slide in from below (or from `distance` px away).
 *
 * Paired with a fade by convention: a thing that slides without fading reads
 * as a thing that was already there and moved, which is a different sentence.
 */
export function slide(options: MotionOptions = {}): string {
	if (inert(options.reduced)) return 'opacity:1;transform:none;';
	const distance = options.distance ?? 8;
	return (
		`--jv-slide-from:${distance}px;` +
		`animation: jv-slide ${base({ ease: 'decelerate', ...options })} both;`
	);
}

/** Scale in. `spring` by default, because a thing that pops should overshoot. */
export function scale(options: MotionOptions = {}): string {
	if (inert(options.reduced)) return 'opacity:1;transform:none;';
	const from = options.from ?? 0.96;
	return (
		`--jv-scale-from:${from};` +
		`animation: jv-scale ${base({ ease: 'spring', ...options })} both;`
	);
}

/**
 * The shimmer a skeleton uses while something loads.
 *
 * Reduced motion gets a still placeholder rather than a slower shimmer: the
 * animation IS the content here, and a slow one is a distraction that never
 * ends.
 */
export function shimmer(options: MotionOptions = {}): string {
	if (inert(options.reduced)) return 'opacity:0.6;';
	return `animation: jv-shimmer var(--jv-dur-sweep) ${EASE.standard} infinite;`;
}

/** The slow glow that says something is alive — a running task, a live orb. */
export function glowPulse(options: MotionOptions = {}): string {
	if (inert(options.reduced)) return '';
	return `animation: jv-glow-pulse var(--jv-dur-pulse) ${EASE.standard} infinite alternate;`;
}

/**
 * A shared-element transition: the same thing in two places, moved.
 *
 * Returns the `view-transition-name` the browser needs on BOTH ends. Where the
 * View Transitions API is missing the name is inert and the change is a cut,
 * which is the correct fallback — a polyfill that animates a clone is how you
 * get two of something on screen at once.
 */
export function sharedElement(name: string, reduced?: boolean): string {
	if (inert(reduced) || !name) return '';
	return `view-transition-name: ${name.replace(/[^a-zA-Z0-9_-]/g, '-')};`;
}

/** True when the browser can do shared-element transitions at all. */
export function supportsSharedElement(host: Document | undefined = globalThis.document): boolean {
	return typeof (host as any)?.startViewTransition === 'function';
}
