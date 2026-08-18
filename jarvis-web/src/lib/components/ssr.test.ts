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
import ChatMessage from './ChatMessage.svelte';
import ChatPanel from './ChatPanel.svelte';
import Pairing from './Pairing.svelte';
import TaskCard from './TaskCard.svelte';
import TaskDock from './TaskDock.svelte';
import ToolActivity from './ToolActivity.svelte';
import { assistantPlaceholder, userMessage } from '$lib/chat';

const noop = () => {};

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

	it('arms no timers in the task dock', () => {
		// Third layout-level surface, and the one with a real timer on the
		// client: it schedules a single wake-up for when a finished task should
		// stop lingering. That lives in an `$effect`, which is exactly why it
		// must not fire here — on the server there is no unmount to clear it.
		expect(timersArmedBy(TaskDock, { conn: null })).toBe(0);
	});

	it('renders the pairing panel’s markup, which is the point of doing it at all', () => {
		const { body } = render(Pairing);
		expect(body).toContain('data-testid="pairing"');
	});

	// --- chat mode ----------------------------------------------------------
	// It server-renders because `/` does, and a mode remembered in localStorage
	// means the FIRST paint of a chat-mode reload is this markup. Anything that
	// touched `window` or armed a timer here would take the whole page down
	// during SSR, on the one route that has to work.
	it('arms no timers in the chat surfaces', () => {
		expect(
			timersArmedBy(ChatPanel, {
				messages: [],
				onSend: noop,
				onNew: noop,
				onOpen: noop,
				onDelete: noop,
				onVoice: noop,
				onToggleSpeak: noop,
				onToggleMode: noop
			})
		).toBe(0);
	});

	it('renders a transcript on the server, tool rows and reasoning included', () => {
		const answer = {
			...assistantPlaceholder(),
			content: 'Done, Sir.',
			thinking: 'the lab strip',
			pending: false,
			tools: [
				{
					key: 'k',
					name: 'turn_on',
					arguments: { name: 'lab' },
					state: 'ok' as const,
					durationMs: 12
				}
			]
		};

		const { body } = render(ChatMessage, { props: { message: answer } });

		expect(body).toContain('Done, Sir.');
		expect(body).toContain('turn_on');
		// Present in the markup but inside a closed <details>: reasoning is
		// available to read and never presented as the answer.
		expect(body).toContain('the lab strip');
		expect(body).not.toContain('<details open');
	});

	it('renders the empty state and the composer with no conversation', () => {
		const { body } = render(ChatPanel, {
			props: {
				messages: [userMessage('hello')],
				onSend: noop,
				onNew: noop,
				onOpen: noop,
				onDelete: noop,
				onVoice: noop,
				onToggleSpeak: noop,
				onToggleMode: noop
			}
		});

		expect(body).toContain('data-testid="chat-input"');
		expect(body).toContain('data-testid="chat-send"');
		expect(body).toContain('data-testid="chat-mic"');
		expect(body).toContain('hello');
	});

	// --- tasks ----------------------------------------------------------------
	it('renders a task card, steps and all', () => {
		const { body } = render(TaskCard, { props: { task: researchTask() } });
		expect(body).toContain('Read twelve pages');
		expect(body).toContain('RUNNING');
		// Open by default while the task is running: "which step" is the
		// question exactly then, and only then.
		expect(body).toContain('reading page 4');
	});

	it('gives a determinate bar a number a screen reader can announce', () => {
		const { body } = render(TaskCard, {
			props: { task: { ...researchTask(), fraction: 0.25 } }
		});
		expect(body).toContain('aria-valuenow="25"');
	});

	it('gives an indeterminate bar no number at all', () => {
		// ARIA's own rule, and the only way a reader says "busy" rather than
		// reading out a figure nobody computed. `aria-valuenow="0"` here would
		// announce a task that is working as one that has done nothing.
		const { body } = render(TaskCard, {
			props: { task: { ...researchTask(), fraction: null } }
		});
		expect(body).toContain('role="progressbar"');
		expect(body).not.toContain('aria-valuenow');
	});
});

function researchTask() {
	return {
		id: 't1',
		kind: 'research',
		title: 'Read twelve pages',
		status: 'running' as const,
		steps: [
			{ title: 'search', status: 'done' as const },
			{ title: 'read', status: 'running' as const, detail: 'reading page 4' }
		],
		detail: '',
		result: '',
		error: '',
		created: 1000,
		updated: 1000,
		source: '',
		open_ended: false,
		fraction: 0.5,
		done_steps: 1,
		total_steps: 2,
		finished: false
	};
}
