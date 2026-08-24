import { describe, it, expect, vi } from 'vitest';
import {
	DURATION,
	STAGGER_CAP_MS,
	STAGGER_STEP_MS,
	prefersReducedMotion,
	staggerDelay,
	staggerStyle,
	watchReducedMotion
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
