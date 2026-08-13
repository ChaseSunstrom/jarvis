// What stands between a half-written automation and the click that used to
// delete it without saying anything.
import { describe, it, expect, vi } from 'vitest';
import { DISCARD_WINDOW_MS, DiscardGuard, formsDiffer } from './unsaved';

describe('formsDiffer', () => {
	it('is false for the same values and true for any single change', () => {
		const a = { alias: 'Porch', mode: 'single', trigger: '[]' };
		expect(formsDiffer(a, { ...a })).toBe(false);
		expect(formsDiffer(a, { ...a, alias: 'Porch ' })).toBe(true);
		expect(formsDiffer(a, { ...a, trigger: '[ ]' })).toBe(true);
	});

	it('compares the text, not the meaning', () => {
		// `[ ]` and `[]` are the same JSON and different strings, and the string is
		// what somebody is in the middle of typing. Calling them equal would throw
		// away an edit on the grounds that it had not changed anything yet.
		expect(formsDiffer({ body: '{"a":1}' }, { body: '{ "a": 1 }' })).toBe(true);
	});

	it('notices a key that only one side has', () => {
		expect(formsDiffer({ a: '1' } as Record<string, string>, {} as Record<string, string>)).toBe(
			true
		);
	});

	it('treats a missing form as empty rather than throwing', () => {
		expect(formsDiffer(null as any, null as any)).toBe(false);
		expect(formsDiffer({ a: '1' }, null as any)).toBe(true);
	});
});

describe('DiscardGuard', () => {
	it('lets a clean editor through without a word', () => {
		const warn = vi.fn();
		const guard = new DiscardGuard(warn);
		expect(guard.allows('light.a', false)).toBe(true);
		expect(warn).not.toHaveBeenCalled();
	});

	it('refuses the first press on a dirty editor, and says so', () => {
		const warn = vi.fn();
		const guard = new DiscardGuard(warn);
		expect(guard.allows('light.b', true)).toBe(false);
		expect(warn).toHaveBeenCalledWith('light.b');
		expect(guard.pending).toBe('light.b');
	});

	it('lets the second press at the same target through', () => {
		const guard = new DiscardGuard(() => {});
		expect(guard.allows('light.b', true)).toBe(false);
		expect(guard.allows('light.b', true)).toBe(true);
		// ...and disarms, so a later press asks again rather than sailing through.
		expect(guard.pending).toBe('');
		expect(guard.allows('light.b', true)).toBe(false);
	});

	it('re-asks when the second press is somewhere else', () => {
		// "Close this" and "open that" are different intentions. A stray click on
		// another row must not spend the confirmation the first one armed.
		const warn = vi.fn();
		const guard = new DiscardGuard(warn);
		expect(guard.allows('light.b', true)).toBe(false);
		expect(guard.allows('light.c', true)).toBe(false);
		expect(warn).toHaveBeenLastCalledWith('light.c');
		expect(guard.allows('light.b', true)).toBe(false);
	});

	it('forgets the arming after the window, so a click minutes later still asks', () => {
		vi.useFakeTimers();
		const guard = new DiscardGuard(() => {});
		expect(guard.allows('light.b', true)).toBe(false);
		vi.advanceTimersByTime(DISCARD_WINDOW_MS + 1);
		expect(guard.pending).toBe('');
		expect(guard.allows('light.b', true)).toBe(false);
		vi.useRealTimers();
	});

	it('drops any arming once the form is clean again — a save, or a reset', () => {
		const guard = new DiscardGuard(() => {});
		expect(guard.allows('light.b', true)).toBe(false);
		expect(guard.allows('light.b', false)).toBe(true);
		expect(guard.pending).toBe('');
	});
});
