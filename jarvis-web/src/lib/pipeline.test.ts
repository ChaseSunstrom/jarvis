import { describe, it, expect, vi } from 'vitest';
import { PipelineClient, frameAudio, endFrame } from './pipeline';

describe('frameAudio / endFrame byte layout', () => {
	it('prefixes the handler id byte and encodes Int16 little-endian', () => {
		const pcm = new Int16Array([0, 1, -1, 32767, -32768, 0x1234]);
		const frame = frameAudio(7, pcm);
		expect(frame.length).toBe(1 + pcm.length * 2);
		expect(frame[0]).toBe(7);
		const view = new DataView(frame.buffer, 1);
		for (let i = 0; i < pcm.length; i++) {
			expect(view.getInt16(i * 2, true)).toBe(pcm[i]);
		}
		// spot-check raw little-endian bytes of 0x1234
		expect(frame[1 + 5 * 2]).toBe(0x34);
		expect(frame[1 + 5 * 2 + 1]).toBe(0x12);
	});

	it('endFrame is exactly one byte: the handler id', () => {
		const f = endFrame(3);
		expect(f.length).toBe(1);
		expect(f[0]).toBe(3);
	});

	it('masks handler ids to one byte', () => {
		expect(endFrame(0x1ff)[0]).toBe(0xff);
		expect(frameAudio(0x102, new Int16Array(0))[0]).toBe(0x02);
	});
});

function event(id: number, type: string, data: any = {}) {
	return JSON.stringify({ id, type: 'event', event: { type, data, timestamp: 'x' } });
}

describe('PipelineClient', () => {
	function setup() {
		const sent: (string | Uint8Array)[] = [];
		const cb = {
			onState: vi.fn(),
			onTranscript: vi.fn(),
			onDelta: vi.fn(),
			onResponse: vi.fn(),
			onTtsUrl: vi.fn(),
			onError: vi.fn(),
			onReady: vi.fn(),
			onRunEnd: vi.fn(),
			onEvent: vi.fn()
		};
		const client = new PipelineClient((d) => sent.push(d), cb);
		return { client, cb, sent };
	}

	it('uses monotonically increasing ids and resolves command results', async () => {
		const { client, sent } = setup();
		const p1 = client.listPipelines();
		const p2 = client.command({ type: 'ping' });
		const m1 = JSON.parse(sent[0] as string);
		const m2 = JSON.parse(sent[1] as string);
		expect(m1.id).toBe(1);
		expect(m2.id).toBe(2);
		expect(m1.type).toBe('assist_pipeline/pipeline/list');
		client.handleMessage(
			JSON.stringify({
				id: 1,
				type: 'result',
				success: true,
				result: { pipelines: [{ id: 'p1', name: 'Jarvis' }], preferred_pipeline: 'p1' }
			})
		);
		client.handleMessage(JSON.stringify({ id: 2, type: 'result', success: true, result: null }));
		const list = await p1;
		expect(list.pipelines[0].name).toBe('Jarvis');
		await p2;
	});

	it('resolvePipelineId matches by name, falls back to preferred', async () => {
		const { client, sent } = setup();
		const p = client.resolvePipelineId('Jarvis');
		client.handleMessage(
			JSON.stringify({
				id: JSON.parse(sent[0] as string).id,
				type: 'result',
				success: true,
				result: {
					pipelines: [
						{ id: 'a', name: 'Home' },
						{ id: 'b', name: 'Jarvis' }
					],
					preferred_pipeline: 'a'
				}
			})
		);
		expect(await p).toBe('b');

		const p2 = client.resolvePipelineId('Nope');
		client.handleMessage(
			JSON.stringify({
				id: JSON.parse(sent[1] as string).id,
				type: 'result',
				success: true,
				result: { pipelines: [{ id: 'a', name: 'Home' }], preferred_pipeline: 'a' }
			})
		);
		expect(await p2).toBe('a');
	});

	it('dispatches the full event sequence to callbacks', () => {
		const { client, cb, sent } = setup();
		const runId = client.startRun({ pipeline: 'p1' });
		const runMsg = JSON.parse(sent[0] as string);
		expect(runMsg).toMatchObject({
			id: runId,
			type: 'assist_pipeline/run',
			start_stage: 'stt',
			end_stage: 'tts',
			input: { sample_rate: 16000 },
			pipeline: 'p1',
			conversation_id: null
		});

		client.handleMessage(event(runId, 'run-start', { runner_data: { stt_binary_handler_id: 1 } }));
		expect(cb.onReady).toHaveBeenCalledWith(1);
		expect(cb.onState).toHaveBeenCalledWith('listening');
		expect(client.sttBinaryHandlerId).toBe(1);

		// audio framing goes through the socket once ready
		client.sendAudio(new Int16Array([1, 2]));
		client.endAudio();
		expect((sent[1] as Uint8Array)[0]).toBe(1);
		expect((sent[1] as Uint8Array).length).toBe(5);
		expect((sent[2] as Uint8Array).length).toBe(1);

		client.handleMessage(event(runId, 'stt-start'));
		client.handleMessage(event(runId, 'stt-vad-start'));
		client.handleMessage(event(runId, 'stt-vad-end'));
		client.handleMessage(event(runId, 'stt-end', { stt_output: { text: 'turn on the lab lights' } }));
		expect(cb.onTranscript).toHaveBeenCalledWith('turn on the lab lights');
		expect(cb.onState).toHaveBeenCalledWith('thinking');

		client.handleMessage(event(runId, 'intent-start'));
		for (const d of ['Turning ', 'on the ', 'lab lights.']) {
			client.handleMessage(event(runId, 'intent-progress', { chat_log_delta: { content: d } }));
		}
		expect(cb.onDelta.mock.calls.map((c) => c[0]).join('')).toBe('Turning on the lab lights.');

		client.handleMessage(
			event(runId, 'intent-end', {
				intent_output: {
					conversation_id: 'conv-42',
					response: { speech: { plain: { speech: 'Turning on the lab lights.' } } }
				}
			})
		);
		expect(cb.onResponse).toHaveBeenCalledWith('Turning on the lab lights.');
		expect(client.conversationId).toBe('conv-42');

		client.handleMessage(event(runId, 'tts-start'));
		client.handleMessage(event(runId, 'tts-end', { tts_output: { url: '/api/tts_proxy/x.mp3' } }));
		expect(cb.onTtsUrl).toHaveBeenCalledWith('/api/tts_proxy/x.mp3');
		expect(cb.onState).toHaveBeenCalledWith('speaking');

		client.handleMessage(event(runId, 'run-end'));
		expect(cb.onRunEnd).toHaveBeenCalled();
		expect(cb.onError).not.toHaveBeenCalled();

		// next run reuses the stored conversation id
		client.startRun({});
		const secondRun = JSON.parse(sent[sent.length - 1] as string);
		expect(secondRun.conversation_id).toBe('conv-42');
	});

	it('runs a typed turn from the intent stage, with no audio stream at all', () => {
		const { client, cb, sent } = setup();
		const runId = client.startTextRun('turn on the lab lights', { pipeline: 'p1' });
		expect(JSON.parse(sent[0] as string)).toMatchObject({
			id: runId,
			type: 'assist_pipeline/run',
			// The whole point: no stt stage, so nothing is waiting for a microphone
			// that the browser has refused. It still ends at tts, so a typed
			// question is answered out loud like a spoken one.
			start_stage: 'intent',
			end_stage: 'tts',
			input: { text: 'turn on the lab lights' },
			pipeline: 'p1',
			conversation_id: null
		});
		expect(JSON.parse(sent[0] as string).input.sample_rate).toBeUndefined();

		// A text run has no binary handler, so the audio calls stay no-ops for its
		// whole life — there is no half-open stream to close.
		client.handleMessage(event(runId, 'run-start', { runner_data: {} }));
		expect(client.sttBinaryHandlerId).toBe(null);
		client.sendAudio(new Int16Array([1, 2]));
		client.endAudio();
		expect(sent).toHaveLength(1);

		// ...and everything after the stt stage is the ordinary path.
		client.handleMessage(
			event(runId, 'intent-end', {
				intent_output: {
					conversation_id: 'conv-typed',
					response: { speech: { plain: { speech: 'Turning on the lab lights.' } } }
				}
			})
		);
		expect(cb.onResponse).toHaveBeenCalledWith('Turning on the lab lights.');
		expect(client.conversationId).toBe('conv-typed');

		// The conversation carries on into the next turn, spoken or typed.
		client.startTextRun('and the kitchen');
		expect(JSON.parse(sent[1] as string).conversation_id).toBe('conv-typed');
	});

	it('ignores events for other message ids', () => {
		const { client, cb } = setup();
		const runId = client.startRun({});
		client.handleMessage(event(runId + 999, 'stt-end', { stt_output: { text: 'nope' } }));
		expect(cb.onTranscript).not.toHaveBeenCalled();
	});

	it('surfaces pipeline errors', () => {
		const { client, cb } = setup();
		const runId = client.startRun({});
		client.handleMessage(event(runId, 'run-start', { runner_data: { stt_binary_handler_id: 1 } }));
		client.handleMessage(
			event(runId, 'error', { code: 'stt-no-text-recognized', message: 'no speech' })
		);
		expect(cb.onError).toHaveBeenCalledWith('stt-no-text-recognized', 'no speech');
		expect(cb.onState).toHaveBeenCalledWith('idle');
	});
});
