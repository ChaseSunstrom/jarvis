// The text-size preference: what it stores, what it refuses, and what it puts
// on the root element. All three matter — a preference that does not survive a
// reload is a control that lies, and one that writes a pixel size overrides a
// browser setting the user may have chosen on purpose.
import { describe, it, expect } from 'vitest';
import {
	DEFAULT_TEXT_SIZE,
	TEXT_SIZES,
	TEXT_SIZE_KEY,
	applyTextSize,
	readTextSize,
	textSizeFor,
	writeTextSize
} from './textSize';

/** A `localStorage` that is only a Map, and one that has been switched off. */
function fakeStorage(initial: Record<string, string> = {}) {
	const map = new Map(Object.entries(initial));
	return {
		map,
		getItem: (k: string) => map.get(k) ?? null,
		setItem: (k: string, v: string) => void map.set(k, v)
	};
}

const refusingStorage = {
	getItem(): string {
		throw new Error('storage is disabled');
	},
	setItem(): void {
		throw new Error('storage is disabled');
	}
};

describe('the text size steps', () => {
	it('are distinct, ordered and named', () => {
		const scales = TEXT_SIZES.map((s) => s.scale);
		expect(new Set(TEXT_SIZES.map((s) => s.id)).size).toBe(TEXT_SIZES.length);
		expect([...scales].sort((a, b) => a - b)).toEqual(scales);
		for (const size of TEXT_SIZES) expect(size.label.trim()).not.toBe('');
	});

	it('include a step that is exactly the browser default, and it is the default', () => {
		// 1 is what makes this multiply the reader's own setting rather than
		// replace it. Without a 1 there is no way back to "leave it alone".
		expect(TEXT_SIZES.some((s) => s.scale === 1)).toBe(true);
		expect(textSizeFor(DEFAULT_TEXT_SIZE).scale).toBe(1);
	});

	it('never shrink the scale below nine tenths', () => {
		// The tokens are already at their readable floor; a step that undid the
		// legibility work would be a setting for reintroducing the bug.
		for (const size of TEXT_SIZES) expect(size.scale).toBeGreaterThanOrEqual(0.9);
	});
});

describe('readTextSize / writeTextSize', () => {
	it('round-trips a choice', () => {
		const storage = fakeStorage();
		expect(writeTextSize(storage, 'large').id).toBe('large');
		expect(storage.map.get(TEXT_SIZE_KEY)).toBe('large');
		expect(readTextSize(storage).id).toBe('large');
	});

	it('falls back to the default for an unknown or missing name', () => {
		expect(readTextSize(fakeStorage()).id).toBe(DEFAULT_TEXT_SIZE);
		expect(readTextSize(fakeStorage({ [TEXT_SIZE_KEY]: 'enormous' })).id).toBe(DEFAULT_TEXT_SIZE);
		expect(writeTextSize(fakeStorage(), 'enormous').id).toBe(DEFAULT_TEXT_SIZE);
	});

	it('survives storage being switched off, in both directions', () => {
		// Private mode, or cookies blocked: `localStorage` throws on access rather
		// than returning null, and a preference that cannot be read must not be
		// able to stop the page rendering.
		expect(readTextSize(refusingStorage).id).toBe(DEFAULT_TEXT_SIZE);
		expect(writeTextSize(refusingStorage, 'large').id).toBe('large');
		expect(readTextSize(undefined).id).toBe(DEFAULT_TEXT_SIZE);
	});
});

describe('applyTextSize', () => {
	it('writes a percentage, so it multiplies the browser default rather than replacing it', () => {
		const doc = { documentElement: { style: { fontSize: '' } } };
		applyTextSize(doc, textSizeFor('large'));
		expect(doc.documentElement.style.fontSize).toBe('115%');
		applyTextSize(doc, textSizeFor('standard'));
		expect(doc.documentElement.style.fontSize).toBe('100%');
		// Never a px value: 16px here would silently undo a reader who has already
		// set their browser to 20.
		expect(doc.documentElement.style.fontSize).not.toMatch(/px/);
	});

	it('does nothing at all without a document, which is every server render', () => {
		expect(() => applyTextSize(undefined, textSizeFor('large'))).not.toThrow();
		expect(() => applyTextSize({}, textSizeFor('large'))).not.toThrow();
	});
});
