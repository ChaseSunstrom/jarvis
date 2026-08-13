// What a component may do while it is being rendered on the server.
//
// A Svelte component's instance body runs on both sides. On the client it runs
// once per mount and `onDestroy` undoes it; on the SERVER it runs once per
// request inside a process that never unmounts anything — so anything armed
// there is armed forever, against a component nobody will ever look at, in a
// node process that is expected to stay up for months.
//
// `Pairing.svelte` did exactly that: two `setInterval`s at the top level of the
// file, one ticking a countdown every second and one polling the token list
// every two, per render of /settings. The page rendered correctly, which is why
// it went unnoticed — the cost is invisible from the browser and cumulative on
// the server.
//
// So this renders the components that own timers and watches the clock
// functions themselves. Spies rather than a timer count: what is being pinned
// is that the component never ASKS for a timer during a render, which is a
// statement about this code and not about how far a fake clock's bookkeeping
// reaches into a compiled component.
import { describe, it, expect, vi } from 'vitest';
import { render } from 'svelte/server';
import Approvals from './Approvals.svelte';
import Pairing from './Pairing.svelte';
import ToolActivity from './ToolActivity.svelte';

/** Server-render `component` and report every timer it started while doing so. */
function timersArmedBy(component: any, props: Record<string, unknown> = {}): number {
	const interval = vi.spyOn(globalThis, 'setInterval');
	const timeout = vi.spyOn(globalThis, 'setTimeout');
	try {
		const { body } = render(component, { props });
		// A render that produced nothing would make the count trivially zero.
		expect(body.length).toBeGreaterThan(0);
		return interval.mock.calls.length + timeout.mock.calls.length;
	} finally {
		interval.mockRestore();
		timeout.mockRestore();
	}
}

describe('server rendering', () => {
	it('arms no timers in the pairing panel', () => {
		expect(timersArmedBy(Pairing)).toBe(0);
	});

	it('arms no timers in the two layout-level surfaces', () => {
		// Both of these are rendered by the layout on every route, HUD included,
		// so a timer in either would be armed by every page of the app.
		expect(timersArmedBy(Approvals, { conn: null })).toBe(0);
		expect(timersArmedBy(ToolActivity, { conn: null })).toBe(0);
	});

	it('renders the pairing panel’s markup, which is the point of doing it at all', () => {
		const { body } = render(Pairing);
		expect(body).toContain('data-testid="pairing"');
	});
});
