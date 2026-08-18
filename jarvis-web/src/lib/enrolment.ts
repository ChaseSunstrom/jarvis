/**
 * Voice enrolment, as state rather than as a screen.
 *
 * Enrolment is a small state machine — which phrase you are on, what has been
 * recorded, what the server said about each sample — and every interesting bug
 * in it is a state bug: a sample counted twice, a phrase skipped after a
 * failure, a "done" that arrives before the server has enough samples. None of
 * those need a microphone to reproduce, so none of them live in the component.
 *
 * The component owns getUserMedia, the worklet and the buttons. This file owns
 * the answer to "what happens next", and is tested in Node.
 *
 * ## Why the phrases are not in here
 *
 * They come from the server, in the `prompts` field of `GET /api/voice/speaker`.
 * That is the whole reason a second enrolment surface is safe to build: the
 * console and the phone read the same list from the same place, so they cannot
 * drift. `jarvis-core/jarvis/voice/speaker.py` explains why the phrases are
 * chosen as they are — they move pitch and length on purpose, and a profile
 * built from five similar sentences is a profile that rejects its owner for
 * having a cold.
 */

/** 16 kHz mono, which is what `MicCapture` emits and what jarvis-core expects. */
export const ENROLMENT_RATE = 16000;
export const ENROLMENT_WIDTH = 2;

/**
 * Shortest utterance worth sending.
 *
 * jarvis-core refuses a sample with too little speech in it and says so, which
 * is the authority. This is only here to stop an accidental tap sending 40 ms
 * of room tone and coming back with an error the person did not earn. Well
 * under the shortest real phrase ("Stop.") and well over a tap.
 */
export const MIN_SAMPLE_MS = 400;

/** Longer than any prompt, and a hard stop so a stuck recorder cannot run on. */
export const MAX_SAMPLE_MS = 20000;

export interface SpeakerStatus {
	supported?: boolean;
	enrolled?: boolean;
	samples?: number;
	anchor_samples?: number;
	adapted_samples?: number;
	min_samples?: number;
	measure_samples?: number;
	max_samples?: number;
	prompts?: string[];
	threshold?: number;
	self_score?: number | null;
	worst_self_score?: number | null;
	suggested_threshold?: number | null;
	threshold_measured?: boolean;
	mode?: string;
}

export type SampleState = 'pending' | 'recording' | 'sending' | 'accepted' | 'rejected';

export interface SampleSlot {
	prompt: string;
	state: SampleState;
	/** The server's own words when it refused. Written for a person to act on. */
	detail?: string;
	/** Milliseconds of audio actually captured, for the "too short" case. */
	ms?: number;
}

export interface EnrolmentSession {
	slots: SampleSlot[];
	/** Index of the phrase being worked on. */
	at: number;
	/** Samples the server has accepted during THIS session. */
	accepted: number;
	/** What the server said the profile needs, so the UI never invents a target. */
	minSamples: number;
	done: boolean;
}

/** A session over the server's phrase list. */
export function beginSession(status: SpeakerStatus | null): EnrolmentSession {
	const prompts = (status?.prompts ?? []).filter((p) => typeof p === 'string' && p.trim());
	return {
		slots: prompts.map((prompt) => ({ prompt, state: 'pending' })),
		at: 0,
		accepted: 0,
		// Never a hard-coded 5. The server owns the number and has changed it
		// before; a screen that says "3 of 5" while the server wants 3 is a
		// screen that tells you to keep going after you have finished.
		minSamples: Math.max(1, Number(status?.min_samples ?? 3)),
		done: false
	};
}

function patch(session: EnrolmentSession, index: number, slot: Partial<SampleSlot>): EnrolmentSession {
	if (index < 0 || index >= session.slots.length) return session;
	const slots = session.slots.slice();
	slots[index] = { ...slots[index], ...slot };
	return { ...session, slots };
}

export function startRecording(session: EnrolmentSession, index = session.at): EnrolmentSession {
	return { ...patch(session, index, { state: 'recording', detail: undefined }), at: index };
}

export function startSending(session: EnrolmentSession, index: number, ms: number): EnrolmentSession {
	return patch(session, index, { state: 'sending', ms });
}

/**
 * The server accepted a sample.
 *
 * `at` advances to the next PENDING slot rather than to `index + 1`, so a
 * retried failure in the middle does not send the person back through phrases
 * they already did.
 */
export function withAccepted(session: EnrolmentSession, index: number): EnrolmentSession {
	const next = patch(session, index, { state: 'accepted', detail: undefined });
	const accepted = next.slots.filter((s) => s.state === 'accepted').length;
	const pending = next.slots.findIndex((s) => s.state === 'pending');
	return {
		...next,
		accepted,
		at: pending === -1 ? index : pending,
		// The server's minimum, not the length of the list: the extra phrases
		// past the minimum are there to widen the profile, and somebody who
		// stops early has still enrolled.
		done: accepted >= next.minSamples && pending === -1
	};
}

/** The server refused a sample, in its own words. The slot stays retryable. */
export function withRejected(
	session: EnrolmentSession,
	index: number,
	detail: string
): EnrolmentSession {
	return patch(session, index, { state: 'rejected', detail: detail || 'that sample was refused' });
}

/** Whether enough has been accepted for the profile to exist at all. */
export function meetsMinimum(session: EnrolmentSession): boolean {
	return session.accepted >= session.minSamples;
}

/** 0..1, for a progress bar. Against the MINIMUM, which is what "done" means. */
export function progress(session: EnrolmentSession): number {
	if (session.minSamples <= 0) return 1;
	return Math.min(1, session.accepted / session.minSamples);
}

export function remaining(session: EnrolmentSession): number {
	return Math.max(0, session.minSamples - session.accepted);
}

/**
 * Join 16 kHz Int16 chunks into one buffer.
 *
 * `MicCapture` hands over ~1024-sample batches and the server wants one
 * utterance, so something has to concatenate. Doing it here rather than in the
 * component means the length arithmetic — the part that silently truncates —
 * is under test.
 */
export function joinChunks(chunks: Int16Array[]): Int16Array {
	let total = 0;
	for (const chunk of chunks) total += chunk.length;
	const out = new Int16Array(total);
	let offset = 0;
	for (const chunk of chunks) {
		out.set(chunk, offset);
		offset += chunk.length;
	}
	return out;
}

export function durationMs(samples: number, rate = ENROLMENT_RATE): number {
	return rate > 0 ? (samples / rate) * 1000 : 0;
}

/**
 * Little-endian bytes for the wire.
 *
 * Sent as raw PCM rather than a WAV, because jarvis-core takes raw PCM with
 * `rate` and `width` in the query string — its own comment calls wrapping it in
 * a container "ceremony" — and because the phone already sends this exact
 * shape. One format on the wire, two clients.
 *
 * Explicit DataView rather than `new Uint8Array(int16.buffer)`: the latter is
 * host-endian, and on a big-endian machine it would send byte-swapped audio
 * that embeds cleanly and matches nobody.
 */
export function pcmBytes(samples: Int16Array): Uint8Array {
	const out = new Uint8Array(samples.length * 2);
	const view = new DataView(out.buffer);
	for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
	return out;
}

/** Why a sample must not be sent, or null when it may be. */
export function rejectLocally(samples: Int16Array): string | null {
	const ms = durationMs(samples.length);
	if (samples.length === 0) return 'no audio was captured — is the microphone allowed?';
	if (ms < MIN_SAMPLE_MS) {
		return `that was ${Math.round(ms)} ms of audio — say the whole phrase`;
	}
	return null;
}
