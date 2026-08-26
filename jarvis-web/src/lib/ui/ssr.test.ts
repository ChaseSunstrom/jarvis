// Every component renders on the server.
//
// SvelteKit renders the first paint on the server, where there is no `window`,
// no `document` and no timer worth arming. A component that reads one at module
// scope, or starts an interval in an effect that SSR also runs, takes the whole
// page down with it — and it does so only in production, because `vite dev`
// hydrates in a browser.
//
// So: render each one, assert it produced markup, and assert nothing armed a
// timer while doing it.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from 'svelte/server';
import * as ui from './index';

const props: Record<string, Record<string, unknown>> = {
	Button: { children: undefined },
	IconButton: { label: 'Dismiss', glyph: '×' },
	Input: {},
	Select: { options: [{ value: 'a', label: 'A' }] },
	Toggle: { label: 'Exposed' },
	Field: { label: 'Name', children: undefined },
	Panel: { title: 'Plan', children: undefined },
	Row: { label: 'first token', value: '640 ms' },
	Pill: { children: undefined },
	Toolbar: { children: undefined },
	Tabs: { tabs: [{ id: 'all', label: 'All' }] },
	Dialog: { open: true, title: 'Forget this?', children: undefined },
	SkeletonRows: { rows: 2 },
	EmptyState: { title: 'Nothing running' },
	ErrorState: { title: "Couldn't load" },
	OfflineState: {},
	ScreenState: { status: 'loading', children: undefined },
	Reactor: { size: 120 },
	TopBar: { tabs: [{ href: '/', label: 'VOICE', testid: 'nav-voice' }], isCurrent: () => false },
	StatusReadout: { items: [{ label: 'link', tone: 'live' }] },
	SectionStrip: { sections: [] },
	ScreenTitle: { title: 'House', lede: 'What is on.' },
	StagesBar: { stages: [{ key: 'stt', label: 'transcribe', ms: 412 }] },
	CallLine: { name: 'light.turn_on', args: 'kitchen_lamp', state: 'ok', ms: 84 },
	DayStrip: { nodes: [{ at: '07:00', label: 'briefing', state: 'done' }] },
	ProgressRing: { size: 200, done: 1, running: 1, total: 3, percent: 40, title: 'A task' },
	Figure: { value: 31.4, unit: 'tok/s', decimals: 1 },
	Graph: { nodes: [{ id: 'note:a', label: 'A', kind: 'note' }], edges: [] }
};

/** A snippet that renders a word, for the components that take children. */
const word = (() => {
	const snippet = () => ({ out: 'ok' });
	return snippet as unknown as never;
})();

afterEach(() => vi.useRealTimers());

describe('every component', () => {
	const names = Object.keys(ui).filter((n) => n[0] === n[0].toUpperCase());

	it('is exported from the barrel with props recorded here', () => {
		expect(names.length).toBeGreaterThanOrEqual(12);
		for (const name of names) expect(props[name], `${name} has no SSR props`).toBeDefined();
	});

	for (const name of Object.keys(props)) {
		it(`${name} renders on the server without arming a timer`, () => {
			vi.useFakeTimers();
			const component = (ui as Record<string, unknown>)[name];
			expect(component, `${name} is not exported`).toBeTruthy();
			const given = { ...props[name] };
			for (const key of Object.keys(given)) if (given[key] === undefined) given[key] = word;
			const result = render(component as never, { props: given as never });
			expect(typeof result.body).toBe('string');
			expect(vi.getTimerCount(), `${name} armed a timer during SSR`).toBe(0);
		});
	}
});
