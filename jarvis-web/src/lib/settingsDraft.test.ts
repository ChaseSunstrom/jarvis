// A form holds strings; jarvis-core's settings are typed. What this pins is the
// gap that used to be closed by a comment: the page SAID it coerced numbers and
// returned `raw.trim()` for them, so every numeric setting the console saved was
// saved as a string.
import { describe, it, expect } from 'vitest';
import { coerceSetting } from './settingsDraft';

describe('coerceSetting', () => {
	it('sends a number as a number, not as the string that was typed', () => {
		expect(coerceSetting('number', '30')).toBe(30);
		expect(coerceSetting('number', ' 2.5 ')).toBe(2.5);
		expect(coerceSetting('number', '-1')).toBe(-1);
		// The whole bug, stated as a test: these must not be strings.
		expect(typeof coerceSetting('number', '30')).toBe('number');
		expect(typeof coerceSetting('integer', '30')).toBe('number');
	});

	it('truncates rather than rounds an integer field', () => {
		// 2.9 in an integer box is a typo, and rounding it to 3 hides the typo
		// behind a value that looks deliberate.
		expect(coerceSetting('integer', '2.9')).toBe(2);
		expect(coerceSetting('integer', '-2.9')).toBe(-2);
	});

	it('turns the boolean select back into a boolean', () => {
		expect(coerceSetting('boolean', 'true')).toBe(true);
		expect(coerceSetting('boolean', 'false')).toBe(false);
		expect(coerceSetting('boolean', 'anything else')).toBe(false);
	});

	it('leaves strings and choices exactly as typed, spaces included', () => {
		expect(coerceSetting('string', '  keep  ')).toBe('  keep  ');
		expect(coerceSetting('choice', 'en_GB-alan-medium')).toBe('en_GB-alan-medium');
	});

	it('passes nonsense through so the SERVER answers for its own field', () => {
		// Not `NaN`, which would be sent as null and stored as one, and not a
		// client-side refusal, which would be a second validator to keep in step.
		expect(coerceSetting('number', 'twelve')).toBe('twelve');
		expect(coerceSetting('integer', '')).toBe('');
		// Infinity is a number and is not finite; it must not reach the wire.
		expect(coerceSetting('number', 'Infinity')).toBe('Infinity');
	});
});
