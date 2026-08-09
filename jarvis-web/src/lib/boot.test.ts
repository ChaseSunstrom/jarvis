// The boot timeline is a pile of easing arithmetic with no visible failure
// mode: get a window wrong and the sequence simply looks slightly off, forever.
// So the invariants get asserted instead — stages tile the whole run, every
// element is 0 before its cue and settled at the end, and skipping lands on the
// same frame the animation would have reached on its own.
import { describe, it, expect } from 'vitest';
import * as boot from './boot';

describe('stage partition', () => {
	it('tiles [0, TOTAL_MS] with no gap and no overlap', () => {
		expect(boot.STAGES[0].startMs).toBe(0);
		expect(boot.STAGES[boot.STAGES.length - 1].endMs).toBe(boot.TOTAL_MS);
		for (let i = 1; i < boot.STAGES.length; i += 1) {
			expect(boot.STAGES[i].startMs).toBe(boot.STAGES[i - 1].endMs);
		}
	});

	it('finishes in about 1.2 s', () => {
		expect(boot.TOTAL_MS).toBeGreaterThanOrEqual(1000);
		expect(boot.TOTAL_MS).toBeLessThanOrEqual(1400);
	});

	it('names one stage for every instant, including out of range', () => {
		expect(boot.stageAt(-50)).toBe('scan');
		expect(boot.stageAt(0)).toBe('scan');
		expect(boot.stageAt(boot.IGNITE_START_MS)).toBe('ignite');
		expect(boot.stageAt(boot.RINGS_START_MS)).toBe('rings');
		expect(boot.stageAt(boot.WORDMARK_START_MS)).toBe('wordmark');
		expect(boot.stageAt(boot.CHECKS_START_MS)).toBe('checks');
		expect(boot.stageAt(boot.HANDOFF_START_MS)).toBe('handoff');
		expect(boot.stageAt(boot.TOTAL_MS + 10_000)).toBe('handoff');
	});

	it('reports progress through the stage it is in', () => {
		expect(boot.stageProgress(boot.RINGS_START_MS, 'rings')).toBe(0);
		expect(boot.stageProgress(boot.WORDMARK_START_MS, 'rings')).toBe(1);
		expect(boot.stageProgress(0, 'rings')).toBe(0);
	});
});

describe('easings', () => {
	it('clamps out-of-range input', () => {
		expect(boot.clamp01(-1)).toBe(0);
		expect(boot.clamp01(2)).toBe(1);
		expect(boot.clamp01(Number.NaN)).toBe(0);
	});

	it('decelerate and accelerate both run 0 -> 1', () => {
		expect(boot.decelerate(0)).toBe(0);
		expect(boot.decelerate(1)).toBe(1);
		expect(boot.accelerate(0)).toBe(0);
		expect(boot.accelerate(1)).toBe(1);
		// Decelerate is ahead of linear at the midpoint, accelerate is behind.
		expect(boot.decelerate(0.5)).toBeGreaterThan(0.5);
		expect(boot.accelerate(0.5)).toBeLessThan(0.5);
	});

	it('overshoot exceeds 1 before settling exactly on it', () => {
		expect(boot.overshoot(0)).toBeCloseTo(0, 5);
		expect(boot.overshoot(1)).toBeCloseTo(1, 5);
		expect(Math.max(...[0.6, 0.7, 0.8, 0.9].map((t) => boot.overshoot(t)))).toBeGreaterThan(1);
	});

	it('treats a zero-length window as instantly complete', () => {
		expect(boot.progress(10, 10, 0)).toBe(1);
		expect(boot.progress(9, 10, 0)).toBe(0);
	});
});

describe('elements', () => {
	it('sweeps the scan line top to bottom and fades it after ignition', () => {
		expect(boot.scanY(0)).toBe(0);
		expect(boot.scanY(boot.IGNITE_START_MS)).toBeCloseTo(1, 5);
		expect(boot.scanAlpha(0)).toBe(1);
		expect(boot.scanAlpha(boot.IGNITE_START_MS)).toBe(1);
		expect(boot.scanAlpha(boot.IGNITE_START_MS + boot.SCAN_FADE_MS)).toBe(0);
	});

	it('keeps the core dark until its cue, then ignites', () => {
		expect(boot.coreScale(boot.IGNITE_START_MS - 1)).toBe(0);
		expect(boot.coreAlpha(boot.IGNITE_START_MS - 1)).toBe(0);
		expect(boot.coreScale(boot.TOTAL_MS)).toBeCloseTo(1, 5);
		// Opacity leads the scale, which is what makes it read as a spark.
		const mid = boot.IGNITE_START_MS + 60;
		expect(boot.coreAlpha(mid)).toBeGreaterThan(boot.coreScale(mid));
	});

	it('blooms the flare once and leaves nothing behind', () => {
		expect(boot.flareAlpha(0)).toBe(0);
		expect(boot.flareAlpha(boot.IGNITE_START_MS + boot.FLARE_MS / 2)).toBeCloseTo(1, 5);
		expect(boot.flareAlpha(boot.IGNITE_START_MS + boot.FLARE_MS)).toBe(0);
		expect(boot.flareAlpha(boot.TOTAL_MS)).toBe(0);
	});

	it('staggers the rings and settles every one of them', () => {
		for (let i = 1; i < boot.RING_COUNT; i += 1) {
			expect(boot.ringStartMs(i)).toBeGreaterThan(boot.ringStartMs(i - 1));
		}
		for (let i = 0; i < boot.RING_COUNT; i += 1) {
			expect(boot.ringReveal(boot.ringStartMs(i) - 1, i)).toBe(0);
			expect(boot.ringReveal(boot.TOTAL_MS, i)).toBe(1);
			expect(boot.ringAlpha(boot.TOTAL_MS, i)).toBeCloseTo(1, 5);
		}
	});

	it('resolves the wordmark letter by letter and closes the spacing up', () => {
		expect(boot.LETTER_COUNT).toBe(6);
		expect(boot.letterAlpha(boot.WORDMARK_START_MS, 5)).toBe(0);
		expect(boot.letterAlpha(boot.TOTAL_MS, 5)).toBeCloseTo(1, 5);
		expect(boot.letterBlur(boot.WORDMARK_START_MS, 0)).toBeCloseTo(boot.LETTER_BLUR_PX, 5);
		expect(boot.letterBlur(boot.TOTAL_MS, 0)).toBeCloseTo(0, 5);
		expect(boot.letterSpacing(boot.WORDMARK_START_MS)).toBeCloseTo(boot.LETTER_SPACING_START, 5);
		expect(boot.letterSpacing(boot.TOTAL_MS)).toBeCloseTo(boot.LETTER_SPACING_END, 5);
	});

	it('types each check line to completion', () => {
		expect(boot.typedLine(0, 0)).toBe('');
		expect(boot.typedChars(boot.checkStartMs(0), 0, 10)).toBe(0);
		for (let i = 0; i < boot.CHECK_LINE_COUNT; i += 1) {
			expect(boot.typedLine(boot.TOTAL_MS, i)).toBe(boot.CHECK_LINES[i]);
		}
		// Half-typed is genuinely half a line, not a jump.
		const half = boot.checkStartMs(0) + boot.CHECK_TYPE_MS / 2;
		expect(boot.typedLine(half, 0).length).toBeGreaterThan(0);
		expect(boot.typedLine(half, 0).length).toBeLessThan(boot.CHECK_LINES[0].length);
	});

	it('hands off: chrome leaves as the app arrives', () => {
		expect(boot.chromeAlpha(0)).toBe(1);
		expect(boot.chromeAlpha(boot.HANDOFF_START_MS)).toBe(1);
		expect(boot.chromeAlpha(boot.TOTAL_MS)).toBeCloseTo(0, 5);
		expect(boot.appAlpha(0)).toBe(0);
		expect(boot.appAlpha(boot.TOTAL_MS)).toBeCloseTo(1, 5);
		// The app starts arriving only after the chrome has begun to leave.
		expect(boot.appAlpha(boot.HANDOFF_START_MS)).toBe(0);
	});
});

describe('frames', () => {
	it('never produces an opacity outside 0..1 at any millisecond', () => {
		for (let t = -100; t <= boot.TOTAL_MS + 100; t += 7) {
			const f = boot.frameAt(t);
			for (const v of [
				f.scanAlpha,
				f.coreAlpha,
				f.flareAlpha,
				f.chromeAlpha,
				f.appAlpha,
				...f.ringAlpha,
				...f.letterAlpha
			]) {
				expect(v, `t=${t}`).toBeGreaterThanOrEqual(0);
				expect(v, `t=${t}`).toBeLessThanOrEqual(1);
			}
		}
	});

	// Skipping has to be indistinguishable from waiting, or the skip is a
	// second code path that can rot.
	it('endFrame is exactly the frame at TOTAL_MS', () => {
		expect(boot.endFrame()).toEqual(boot.frameAt(boot.TOTAL_MS));
	});

	it('is settled at the end: no chrome, full app, every letter landed', () => {
		const end = boot.endFrame();
		expect(end.stage).toBe('handoff');
		expect(end.chromeAlpha).toBeCloseTo(0, 5);
		expect(end.appAlpha).toBeCloseTo(1, 5);
		expect(end.checkLines).toEqual([...boot.CHECK_LINES]);
		expect(end.letterAlpha.every((a) => a > 0.99)).toBe(true);
	});
});

describe('skip policy', () => {
	it('skips for reduced motion, whatever the session says', () => {
		expect(boot.shouldSkipBoot({ reducedMotion: true, alreadyPlayed: false })).toBe(true);
		expect(boot.shouldSkipBoot({ reducedMotion: true, alreadyPlayed: true })).toBe(true);
	});

	it('skips once the session has already seen it', () => {
		expect(boot.shouldSkipBoot({ reducedMotion: false, alreadyPlayed: true })).toBe(true);
	});

	it('plays on a fresh session with motion allowed', () => {
		expect(boot.shouldSkipBoot({ reducedMotion: false, alreadyPlayed: false })).toBe(false);
	});

	it('round-trips the session flag and survives a storage that throws', () => {
		const store = new Map<string, string>();
		const ok = {
			getItem: (k: string) => store.get(k) ?? null,
			setItem: (k: string, v: string) => void store.set(k, v)
		};
		expect(boot.bootAlreadyPlayed(ok)).toBe(false);
		boot.markBootPlayed(ok);
		expect(boot.bootAlreadyPlayed(ok)).toBe(true);

		const hostile = {
			getItem: () => {
				throw new Error('blocked');
			},
			setItem: () => {
				throw new Error('blocked');
			}
		};
		expect(boot.bootAlreadyPlayed(hostile)).toBe(false);
		expect(() => boot.markBootPlayed(hostile)).not.toThrow();
		expect(boot.bootAlreadyPlayed(null)).toBe(false);
	});
});
