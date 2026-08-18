// The one value in /api/config that a bad number can make unusable.
import { describe, it, expect } from 'vitest';
import { clampHangover } from './voiceTuning';

describe('the end-of-speech pause', () => {
	it('takes a sensible number as given', () => {
		expect(clampHangover('400')).toBe(400);
		expect(clampHangover('1200')).toBe(1200);
	});

	it('falls back when the variable is unset or nonsense', () => {
		// Unset is the ordinary case: the default has to survive it.
		expect(clampHangover(undefined)).toBe(550);
		expect(clampHangover('')).toBe(550);
		expect(clampHangover('soon')).toBe(550);
		expect(clampHangover('-1')).toBe(550);
	});

	it('holds a typo to something usable', () => {
		// `50` would end every turn on the first comma; `50000` would never end
		// one at all. Both are a slipped keystroke away from a plausible value.
		expect(clampHangover('50')).toBe(200);
		expect(clampHangover('50000')).toBe(3000);
	});

	it('rounds rather than passing a fraction to a timer', () => {
		expect(clampHangover('550.7')).toBe(551);
	});
});
