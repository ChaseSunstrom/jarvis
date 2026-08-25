import { describe, it, expect } from 'vitest';
import {
	CHORDS,
	CHORD_TIMEOUT_MS,
	ChordTracker,
	isBareKey,
	isPaletteShortcut,
	isTypingTarget
} from './shortcuts';

describe('isTypingTarget', () => {
	it('recognises the form controls a bare letter must not be stolen from', () => {
		expect(isTypingTarget({ tagName: 'INPUT' })).toBe(true);
		expect(isTypingTarget({ tagName: 'textarea' })).toBe(true);
		expect(isTypingTarget({ tagName: 'SELECT' })).toBe(true);
		expect(isTypingTarget({ tagName: 'DIV', isContentEditable: true })).toBe(true);
	});

	it('recognises ARIA text roles too', () => {
		expect(isTypingTarget({ tagName: 'DIV', getAttribute: () => 'textbox' })).toBe(true);
		expect(isTypingTarget({ tagName: 'DIV', getAttribute: () => 'combobox' })).toBe(true);
	});

	it('leaves ordinary elements alone', () => {
		expect(isTypingTarget({ tagName: 'BUTTON' })).toBe(false);
		expect(isTypingTarget({ tagName: 'DIV', getAttribute: () => null })).toBe(false);
		expect(isTypingTarget(null)).toBe(false);
		expect(isTypingTarget(undefined)).toBe(false);
	});
});

describe('isPaletteShortcut', () => {
	it('accepts Ctrl-K and Cmd-K, upper or lower case', () => {
		expect(isPaletteShortcut({ key: 'k', ctrlKey: true })).toBe(true);
		expect(isPaletteShortcut({ key: 'K', metaKey: true })).toBe(true);
	});

	it('rejects a bare k and other modifier combinations', () => {
		expect(isPaletteShortcut({ key: 'k' })).toBe(false);
		expect(isPaletteShortcut({ key: 'k', ctrlKey: true, altKey: true })).toBe(false);
		expect(isPaletteShortcut({ key: 'j', ctrlKey: true })).toBe(false);
	});
});

describe('isBareKey', () => {
	it('is true only with no modifiers', () => {
		expect(isBareKey({ key: 'g' })).toBe(true);
		expect(isBareKey({ key: 'g', shiftKey: true })).toBe(true);
		expect(isBareKey({ key: 'g', ctrlKey: true })).toBe(false);
		expect(isBareKey({ key: 'g', metaKey: true })).toBe(false);
		expect(isBareKey({ key: 'g', altKey: true })).toBe(false);
	});
});

describe('ChordTracker', () => {
	it('completes the documented chords', () => {
		const t = new ChordTracker();
		expect(t.press('g', 0).href).toBeUndefined();
		expect(t.press('d', 10).href).toBe('/house/devices');
		expect(t.press('g', 20).pending).toBe(true);
		expect(t.press('a', 30).href).toBe('/house/automations');
	});

	// `g a` is taken by automations, so areas gets `g r` — a collision here would
	// silently make one of the two unreachable.
	it('has no duplicate destinations and no duplicate keys', () => {
		const keys = Object.keys(CHORDS);
		expect(new Set(keys).size).toBe(keys.length);
		const targets = Object.values(CHORDS);
		expect(new Set(targets).size).toBe(targets.length);
		expect(CHORDS['g d']).toBe('/house/devices');
		expect(CHORDS['g a']).toBe('/house/automations');
		expect(CHORDS['g r']).toBe('/house/areas');
	});

	it('arms only on a key that can start a chord', () => {
		const t = new ChordTracker();
		expect(t.press('x', 0)).toEqual({ pending: false, prefix: '' });
		expect(t.pendingPrefix).toBe('');
	});

	it('forgets the prefix once the timeout has passed', () => {
		const t = new ChordTracker();
		t.press('g', 0);
		expect(t.press('d', CHORD_TIMEOUT_MS + 1).href).toBeUndefined();
		expect(t.pendingPrefix).toBe('');
	});

	it('a second `g` re-arms rather than being swallowed', () => {
		const t = new ChordTracker();
		t.press('g', 0);
		const again = t.press('g', 10);
		expect(again.pending).toBe(true);
		expect(t.press('s', 20).href).toBe('/settings/assistant');
	});

	it('an unknown second key clears the chord', () => {
		const t = new ChordTracker();
		t.press('g', 0);
		expect(t.press('q', 10)).toEqual({ pending: false, prefix: '' });
		expect(t.press('d', 20).href).toBeUndefined();
	});

	it('ignores non-character keys like Shift and ArrowDown', () => {
		const t = new ChordTracker();
		t.press('g', 0);
		expect(t.press('ArrowDown', 5).pending).toBe(false);
		expect(t.press('d', 10).href).toBeUndefined();
	});

	it('is case-insensitive', () => {
		const t = new ChordTracker();
		t.press('G', 0);
		expect(t.press('D', 10).href).toBe('/house/devices');
	});

	it('takes a custom chord table', () => {
		const t = new ChordTracker({ 'z z': '/zed' }, 500);
		t.press('z', 0);
		expect(t.press('z', 100).href).toBe('/zed');
	});

	it('reset() disarms', () => {
		const t = new ChordTracker();
		t.press('g', 0);
		t.reset();
		expect(t.pendingPrefix).toBe('');
		expect(t.press('d', 10).href).toBeUndefined();
	});
});
