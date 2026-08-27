import { describe, it, expect, vi } from 'vitest';
import {
	DURATION,
	STAGGER_CAP_MS,
	STAGGER_STEP_MS,
	fade,
	glowPulse,
	prefersReducedMotion,
	scale,
	sharedElement,
	shimmer,
	slide,
	staggerDelay,
	staggerStyle,
	watchReducedMotion,
} from './motion';
import { tokenMs } from './tokens';

function host(matches: boolean, extra: Record<string, unknown> = {}) {
	return {
		matchMedia: (query: string) => ({ media: query, matches, ...extra })
	};
}

describe('staggerDelay', () => {
	it('steps evenly for the first rows', () => {
		expect(staggerDelay(0)).toBe(0);
		expect(staggerDelay(1)).toBe(STAGGER_STEP_MS);
		expect(staggerDelay(4)).toBe(4 * STAGGER_STEP_MS);
	});

	// The whole reason the cap exists: without it, a 200-row device list would
	// take 200 * 26 ms = 5.2 s to finish arriving.
	it('caps so a long list does not cascade for seconds', () => {
		expect(staggerDelay(200)).toBe(STAGGER_CAP_MS);
		expect(staggerDelay(2000)).toBe(STAGGER_CAP_MS);
		expect(staggerDelay(200)).toBeLessThan(400);
	});

	it('treats junk indices as "no delay"', () => {
		expect(staggerDelay(-5)).toBe(0);
		expect(staggerDelay(Number.NaN)).toBe(0);
		expect(staggerDelay(Infinity)).toBe(0);
	});

	it('honours a custom step and cap', () => {
		expect(staggerDelay(3, 10, 1000)).toBe(30);
		expect(staggerDelay(300, 10, 50)).toBe(50);
	});

	it('emits an inline custom property the CSS reads', () => {
		expect(staggerStyle(2)).toBe(`--jv-delay:${2 * STAGGER_STEP_MS}ms`);
		expect(staggerStyle(9999)).toBe(`--jv-delay:${STAGGER_CAP_MS}ms`);
	});

	it('mirrors the CSS duration tokens', () => {
		expect(DURATION.base).toBe(tokenMs('--jv-dur-base'));
		expect(DURATION.fast).toBeLessThan(DURATION.base);
		expect(DURATION.slow).toBeGreaterThan(DURATION.base);
	});
});

describe('prefersReducedMotion', () => {
	it('is true when the media query matches', () => {
		expect(prefersReducedMotion(host(true))).toBe(true);
	});

	it('is false when it does not', () => {
		expect(prefersReducedMotion(host(false))).toBe(false);
	});

	it('asks for the right media query', () => {
		const matchMedia = vi.fn(() => ({ matches: false }));
		prefersReducedMotion({ matchMedia });
		expect(matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)');
	});

	// SSR and any environment that cannot animate: assume reduced. Guessing
	// "animate" and being wrong is the failure that actually hurts someone.
	it('defaults to reduced when there is nothing to ask', () => {
		expect(prefersReducedMotion({} as any)).toBe(true);
		expect(prefersReducedMotion(undefined)).toBe(true);
	});

	it('defaults to reduced when matchMedia throws', () => {
		expect(
			prefersReducedMotion({
				matchMedia: () => {
					throw new Error('nope');
				}
			})
		).toBe(true);
	});
});

describe('watchReducedMotion', () => {
	it('subscribes and unsubscribes with addEventListener', () => {
		const add = vi.fn();
		const remove = vi.fn();
		const seen: boolean[] = [];
		const stop = watchReducedMotion(
			(v) => seen.push(v),
			host(false, { addEventListener: add, removeEventListener: remove })
		);
		expect(add).toHaveBeenCalledTimes(1);
		add.mock.calls[0][1]({ matches: true });
		expect(seen).toEqual([true]);
		stop();
		expect(remove).toHaveBeenCalledTimes(1);
	});

	it('falls back to the legacy addListener API', () => {
		const add = vi.fn();
		const remove = vi.fn();
		const stop = watchReducedMotion(
			() => {},
			host(false, { addListener: add, removeListener: remove })
		);
		expect(add).toHaveBeenCalledTimes(1);
		stop();
		expect(remove).toHaveBeenCalledTimes(1);
	});

	it('is a no-op without matchMedia', () => {
		expect(() => watchReducedMotion(() => {}, {})()).not.toThrow();
	});
});

// --- the primitives (M44) ---------------------------------------------------
describe('motion primitives', () => {
	const reduced = { reduced: true };

	it('every primitive returns nothing to animate under reduced motion', () => {
		// Not "a faster animation" — nothing. Somebody who asked their OS for
		// less movement has told you what they want, and 80ms of it is movement.
		for (const style of [
			fade(reduced),
			slide(reduced),
			scale(reduced),
			shimmer(reduced),
			glowPulse(reduced)
		]) {
			expect(style).not.toContain('animation:');
		}
		expect(sharedElement('orb', true)).toBe('');
	});

	it('a fade is a fade and nothing else', () => {
		const style = fade({ reduced: false });
		expect(style).toContain('jv-fade');
		expect(style).toContain('var(--jv-ease-standard)');
	});

	it('things that arrive decelerate, and things that pop overshoot', () => {
		expect(slide({ reduced: false })).toContain('var(--jv-ease-decelerate)');
		expect(scale({ reduced: false })).toContain('var(--jv-ease-spring)');
	});

	it('a caller can override the curve and the duration', () => {
		const style = slide({ reduced: false, ease: 'accelerate', duration: 'fast' });
		expect(style).toContain('var(--jv-ease-accelerate)');
		expect(style).toContain(`${DURATION.fast}ms`);
	});

	it('durations come from tokens rather than from numbers', () => {
		// The point of the whole module: a hand-written `0.3s` is a hard-coded
		// value and `token_lint.py` fails on it.
		expect(fade({ reduced: false })).toContain(`${DURATION.base}ms`);
	});

	it('a shared-element name is sanitised, because it comes from data', () => {
		expect(sharedElement('task/42 x', false)).toBe('view-transition-name: task-42-x;');
		expect(sharedElement('', false)).toBe('');
	});

	it('a stagger delay still reaches every primitive', () => {
		expect(slide({ reduced: false, delay: 120 })).toContain('120ms');
	});
});
