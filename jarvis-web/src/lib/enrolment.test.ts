import { describe, expect, it } from 'vitest';
import {
	ENROLMENT_RATE,
	MIN_SAMPLE_MS,
	beginSession,
	durationMs,
	joinChunks,
	meetsMinimum,
	pcmBytes,
	progress,
	rejectLocally,
	remaining,
	startRecording,
	startSending,
	withAccepted,
	withRejected
} from './enrolment';

const STATUS = {
	supported: true,
	enrolled: false,
	min_samples: 3,
	max_samples: 20,
	prompts: ['Phrase one.', 'Phrase two.', 'Phrase three.', 'Phrase four.', 'Phrase five.']
};

describe('an enrolment session', () => {
	it('takes its phrases and its target from the server, never from here', () => {
		const session = beginSession({ ...STATUS, min_samples: 7 });
		expect(session.slots.map((s) => s.prompt)).toEqual(STATUS.prompts);
		expect(session.minSamples).toBe(7);
	});

	it('survives a server that sent no prompts at all', () => {
		const session = beginSession({ supported: true });
		expect(session.slots).toEqual([]);
		expect(session.minSamples).toBeGreaterThan(0);
	});

	it('ignores blank phrases rather than offering an empty card', () => {
		const session = beginSession({ ...STATUS, prompts: ['Real.', '', '   '] });
		expect(session.slots).toHaveLength(1);
	});

	it('counts only what the server accepted', () => {
		let session = beginSession(STATUS);
		session = withAccepted(startSending(startRecording(session, 0), 0, 900), 0);
		session = withRejected(startSending(startRecording(session, 1), 1, 900), 1, 'too quiet');
		expect(session.accepted).toBe(1);
		expect(session.slots[1].state).toBe('rejected');
		expect(session.slots[1].detail).toBe('too quiet');
	});

	it('keeps the server’s own wording for a refusal', () => {
		// jarvis-core writes these for a person to act on — "that sample has no
		// measurable pitch, it is too quiet". Replacing them with a generic
		// message is throwing away the only actionable part.
		const session = withRejected(beginSession(STATUS), 0, 'no measurable pitch — too quiet');
		expect(session.slots[0].detail).toBe('no measurable pitch — too quiet');
	});

	it('moves to the next UNDONE phrase, not simply the next one', () => {
		// The bug this pins: retrying a failure in the middle sending you back
		// through phrases you already recorded.
		let session = beginSession(STATUS);
		session = withRejected(session, 0, 'too quiet');
		session = withAccepted(session, 1);
		session = withAccepted(session, 2);
		// Now retry the first one. `at` must go to slot 3, the first still pending.
		session = withAccepted(startRecording(session, 0), 0);
		expect(session.at).toBe(3);
		expect(session.accepted).toBe(3);
	});

	it('is not done until the server’s minimum is met', () => {
		let session = beginSession(STATUS);
		session = withAccepted(session, 0);
		session = withAccepted(session, 1);
		expect(meetsMinimum(session)).toBe(false);
		expect(remaining(session)).toBe(1);
		session = withAccepted(session, 2);
		expect(meetsMinimum(session)).toBe(true);
		expect(remaining(session)).toBe(0);
	});

	it('reports done once every phrase is settled and the minimum is met', () => {
		let session = beginSession({ ...STATUS, prompts: ['a', 'b', 'c'], min_samples: 3 });
		session = withAccepted(session, 0);
		session = withAccepted(session, 1);
		expect(session.done).toBe(false);
		session = withAccepted(session, 2);
		expect(session.done).toBe(true);
	});

	it('gives a progress fraction against the minimum, capped at one', () => {
		let session = beginSession(STATUS);
		expect(progress(session)).toBe(0);
		session = withAccepted(session, 0);
		expect(progress(session)).toBeCloseTo(1 / 3);
		session = withAccepted(session, 1);
		session = withAccepted(session, 2);
		session = withAccepted(session, 3);
		expect(progress(session)).toBe(1);
	});

	it('does not mutate the session it was given', () => {
		const session = beginSession(STATUS);
		const after = withAccepted(session, 0);
		expect(session.slots[0].state).toBe('pending');
		expect(after.slots[0].state).toBe('accepted');
	});

	it('ignores an index that is not a slot', () => {
		const session = beginSession(STATUS);
		expect(withAccepted(session, 99)).toEqual({ ...session, accepted: 0 });
	});
});

describe('the audio it sends', () => {
	it('joins chunks without losing or inventing samples', () => {
		const joined = joinChunks([
			new Int16Array([1, 2, 3]),
			new Int16Array([4, 5]),
			new Int16Array([6])
		]);
		expect(Array.from(joined)).toEqual([1, 2, 3, 4, 5, 6]);
	});

	it('joins nothing into nothing', () => {
		expect(joinChunks([]).length).toBe(0);
	});

	it('writes little-endian regardless of the machine it runs on', () => {
		// `new Uint8Array(int16.buffer)` is host-endian and would send
		// byte-swapped audio on a big-endian host — audio that embeds cleanly
		// and matches nobody, which is the worst kind of wrong.
		const bytes = pcmBytes(new Int16Array([1, -2, 258]));
		expect(Array.from(bytes)).toEqual([0x01, 0x00, 0xfe, 0xff, 0x02, 0x01]);
	});

	it('measures duration from the sample rate', () => {
		expect(durationMs(ENROLMENT_RATE)).toBe(1000);
		expect(durationMs(ENROLMENT_RATE / 2)).toBe(500);
	});

	it('refuses a tap before it reaches the server', () => {
		const tiny = new Int16Array(Math.floor((ENROLMENT_RATE * (MIN_SAMPLE_MS - 100)) / 1000));
		expect(rejectLocally(tiny)).toMatch(/say the whole phrase/);
	});

	it('says something useful when the microphone gave nothing', () => {
		expect(rejectLocally(new Int16Array(0))).toMatch(/microphone/);
	});

	it('lets a real utterance through', () => {
		const ok = new Int16Array(ENROLMENT_RATE * 2);
		expect(rejectLocally(ok)).toBeNull();
	});
});
