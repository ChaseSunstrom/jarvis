// The chat transcript's rules, which are all about ORDER and OWNERSHIP.
//
// A turn is not one paragraph. It is reasoning, then tool calls, then text,
// and every piece has to land inside the message it belongs to — including
// when two turns overlap, when a run is abandoned, and when a conversation is
// reopened days later from the archive.

import { describe, expect, it, beforeEach } from 'vitest';

import {
	assistantPlaceholder,
	fromArchive,
	relativeTime,
	resetIds,
	settled,
	summariseArgs,
	toolKey,
	userMessage,
	withDelta,
	withError,
	withFinal,
	withThinking,
	withToolEnd,
	withToolStart,
	type ChatMessage
} from './chat';

beforeEach(() => resetIds());

/** A transcript with one question asked and an answer under way. */
function turnInFlight(): ChatMessage[] {
	return [userMessage('lights on'), assistantPlaceholder()];
}

describe('streaming into the pending message', () => {
	it('appends deltas to the assistant message, not the user one', () => {
		let messages = turnInFlight();
		messages = withDelta(messages, 'Done, ');
		messages = withDelta(messages, 'Sir.');

		expect(messages[0].content).toBe('lights on');
		expect(messages[1].content).toBe('Done, Sir.');
	});

	it('keeps reasoning out of the answer', () => {
		// The claim the whole reasoning feature rests on: it is never spoken and
		// never rendered as the reply.
		let messages = turnInFlight();
		messages = withThinking(messages, 'which lamp did they mean');
		messages = withDelta(messages, 'Done.');

		expect(messages[1].content).toBe('Done.');
		expect(messages[1].thinking).toBe('which lamp did they mean');
	});

	it('ignores an empty delta rather than churning the array', () => {
		const messages = turnInFlight();
		expect(withDelta(messages, '')).toBe(messages);
		expect(withThinking(messages, '')).toBe(messages);
	});

	it('marks the message settled and stops accepting deltas', () => {
		let messages = settled(withDelta(turnInFlight(), 'Done.'));
		expect(messages[1].pending).toBe(false);

		// A late delta from a run the user walked away from must not be glued
		// onto the last answer.
		messages = withDelta(messages, ' ...and another thing');
		expect(messages[1].content).toBe('Done.');
	});

	it('settling twice is harmless', () => {
		const once = settled(turnInFlight());
		expect(settled(once)[1].pending).toBe(false);
	});

	it('streams into the newest pending message when two turns overlap', () => {
		let messages = settled(withDelta(turnInFlight(), 'First.'));
		messages = [...messages, userMessage('and again'), assistantPlaceholder()];
		messages = withDelta(messages, 'Second.');

		expect(messages[1].content).toBe('First.');
		expect(messages[3].content).toBe('Second.');
	});
});

describe('tool rows', () => {
	it('pairs a start with its end by round and index', () => {
		let messages = turnInFlight();
		messages = withToolStart(messages, { name: 'turn_on', round: 1, index: 0, total: 1 });
		expect(messages[1].tools[0].state).toBe('running');

		messages = withToolEnd(messages, {
			name: 'turn_on',
			round: 1,
			index: 0,
			total: 1,
			ok: true,
			duration_ms: 42
		});
		expect(messages[1].tools).toHaveLength(1);
		expect(messages[1].tools[0].state).toBe('ok');
		expect(messages[1].tools[0].durationMs).toBe(42);
	});

	it('keeps three calls to the same tool as three rows', () => {
		// Keying on the name alone would collapse them into one row that
		// finishes three times, and a turn that read three sensors would look
		// like a turn that read one.
		let messages = turnInFlight();
		for (let index = 0; index < 3; index++) {
			messages = withToolStart(messages, { name: 'get_state', round: 1, index, total: 3 });
		}
		expect(messages[1].tools).toHaveLength(3);
	});

	it('marks a tool that answered with an error as failed', () => {
		// Not just "did it throw": a tool returning `{"status": "error"}` did
		// not work, and a tick beside it would be a lie about the house.
		let messages = withToolStart(turnInFlight(), { name: 'unlock', round: 1, index: 0 });
		messages = withToolEnd(messages, {
			name: 'unlock',
			round: 1,
			index: 0,
			ok: false,
			error: 'approval required'
		});

		expect(messages[1].tools[0].state).toBe('failed');
		expect(messages[1].tools[0].error).toBe('approval required');
	});

	it('an end with no start still produces a row', () => {
		// A socket that reconnected mid-turn. Losing the row would show a turn
		// that touched the house as one that did not.
		const messages = withToolEnd(turnInFlight(), {
			name: 'turn_on',
			round: 1,
			index: 0,
			ok: true
		});

		expect(messages[1].tools).toHaveLength(1);
		expect(messages[1].tools[0].state).toBe('ok');
	});

	it('drops tool events once the turn has settled', () => {
		const messages = settled(turnInFlight());
		expect(withToolStart(messages, { name: 'late', round: 9, index: 0 })).toBe(messages);
	});

	it('builds a stable key from round and index', () => {
		expect(toolKey({ name: 'a', round: 2, index: 1 })).toBe('2:1:a');
		// Missing fields default rather than producing "undefined:undefined:a".
		expect(toolKey({ name: 'a' })).toBe('0:0:a');
	});
});

describe('the final answer', () => {
	it('fills in the text when nothing streamed', () => {
		const messages = withFinal(turnInFlight(), 'All done.');
		expect(messages[1].content).toBe('All done.');
	});

	it('does not overwrite what already streamed', () => {
		// Otherwise the whole paragraph flashes at the end of every turn.
		let messages = withDelta(turnInFlight(), 'All done.');
		messages = withFinal(messages, 'All done.');
		expect(messages[1].content).toBe('All done.');
	});
});

describe('errors', () => {
	it('records the failure on the turn that failed', () => {
		const messages = withError(turnInFlight(), 'intent-failed', 'no model');
		expect(messages[1].error).toBe('intent-failed: no model');
		expect(messages[1].pending).toBe(false);
	});

	it('adds a message when the run died before one existed', () => {
		const messages = withError([userMessage('hi')], 'offline', 'no socket');
		expect(messages).toHaveLength(2);
		expect(messages[1].role).toBe('assistant');
		expect(messages[1].error).toBe('offline: no socket');
	});
});

describe('reopening a stored conversation', () => {
	it('rebuilds the turns with their tool calls and reasoning', () => {
		const messages = fromArchive({
			id: 'c1',
			title: 'lights',
			created: 1,
			last_active: 2,
			turns: [
				{ role: 'user', content: 'lights on', timestamp: 100 },
				{
					role: 'assistant',
					content: 'Done, Sir.',
					timestamp: 101,
					thinking: 'the lab strip',
					tool_calls: [{ name: 'turn_on', arguments: { name: 'lab' }, ok: true }]
				}
			]
		});

		expect(messages.map((m) => m.role)).toEqual(['user', 'assistant']);
		expect(messages[1].thinking).toBe('the lab strip');
		expect(messages[1].tools[0].name).toBe('turn_on');
		// A stored call has already happened, so it is never left spinning.
		expect(messages[1].tools[0].state).toBe('ok');
		expect(messages.every((m) => !m.pending)).toBe(true);
	});

	it('renders a stored failure as a failure', () => {
		const messages = fromArchive({
			id: 'c1',
			title: '',
			created: 1,
			last_active: 2,
			turns: [
				{
					role: 'assistant',
					content: 'I could not.',
					tool_calls: [{ name: 'unlock', ok: false, error: 'denied' }]
				}
			]
		});
		expect(messages[0].tools[0].state).toBe('failed');
	});

	it('is empty for a missing or empty conversation', () => {
		expect(fromArchive(null)).toEqual([]);
		expect(
			fromArchive({ id: 'c', title: '', created: 0, last_active: 0, turns: [] })
		).toEqual([]);
	});

	it('gives every rebuilt message a distinct key', () => {
		const messages = fromArchive({
			id: 'c1',
			title: '',
			created: 0,
			last_active: 0,
			turns: [
				{ role: 'user', content: 'a' },
				{ role: 'assistant', content: 'b' },
				{ role: 'user', content: 'a' }
			]
		});
		expect(new Set(messages.map((m) => m.id)).size).toBe(3);
	});
});

describe('presentation helpers', () => {
	it('summarises arguments to one line and caps them', () => {
		expect(summariseArgs({ name: 'kitchen', brightness: 90 })).toBe(
			'name: kitchen · brightness: 90'
		);
		expect(summariseArgs({ q: 'z'.repeat(200) })).toContain('…');
		// Three at most: the row is one line.
		expect(summariseArgs({ a: 1, b: 2, c: 3, d: 4 }).split(' · ')).toHaveLength(3);
	});

	it('skips empty argument values rather than printing "key: "', () => {
		expect(summariseArgs({ a: '', b: null, c: 'kept' })).toBe('c: kept');
		expect(summariseArgs(undefined)).toBe('');
	});

	it('says how long ago in the fewest characters that are true', () => {
		const now = Date.UTC(2026, 7, 17, 12, 0, 0);
		const at = (ms: number) => (now - ms) / 1000;

		expect(relativeTime(at(30_000), now)).toBe('now');
		expect(relativeTime(at(5 * 60_000), now)).toBe('5m');
		expect(relativeTime(at(3 * 3_600_000), now)).toBe('3h');
		expect(relativeTime(at(2 * 86_400_000), now)).toBe('2d');
		// Past a week, relative stops meaning anything and a date is shorter.
		expect(relativeTime(at(30 * 86_400_000), now)).toBe('2026-07-18');
	});

	it('does not report a clock-skewed future timestamp as negative', () => {
		const now = Date.UTC(2026, 7, 17, 12, 0, 0);
		expect(relativeTime(now / 1000 + 600, now)).toBe('now');
	});
});
