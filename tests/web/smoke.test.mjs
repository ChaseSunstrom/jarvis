// Protocol smoke test: mock HA <-> real client code path (src/lib/pipeline.ts,
// loaded via Node's native TypeScript type-stripping).
//
// Run:  node tests/web/smoke.test.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { startMockHA, TRANSCRIPT, DELTAS, RESPONSE, TTS_PATH, MOCK_TOKEN } from './mock-ha.mjs';
import { PipelineClient, frameAudio, endFrame } from '../../jarvis-web/src/lib/pipeline.ts';

const require = createRequire(new URL('../../jarvis-web/package.json', import.meta.url));
const WebSocket = require('ws');

const fail = (msg) => {
	console.error(`FAIL: ${msg}`);
	process.exit(1);
};
const timeout = setTimeout(() => fail('timed out after 15s'), 15000);

const mock = await startMockHA({ log: (m) => console.log(`  [${m}]`) });
console.log(`mock HA at ${mock.url}`);

// --- connect + auth handshake (what the node proxy does server-side) ---
const ws = new WebSocket(`${mock.url.replace('http', 'ws')}/api/websocket`);
ws.binaryType = 'arraybuffer';
await new Promise((resolve, reject) => {
	ws.on('error', reject);
	ws.on('message', (data, isBinary) => {
		if (isBinary) return;
		const msg = JSON.parse(data.toString());
		if (msg.type === 'auth_required') ws.send(JSON.stringify({ type: 'auth', access_token: MOCK_TOKEN }));
		else if (msg.type === 'auth_ok') resolve();
		else if (msg.type === 'auth_invalid') reject(new Error('auth_invalid'));
	});
});
console.log('auth handshake OK');

// --- drive the pipeline through the real PipelineClient ---
const events = [];
const deltas = [];
let transcript = null;
let responseText = null;
let ttsUrl = null;
let handlerId = null;
let tAudioEnd = 0;
const lat = {};

let resolveDone;
const done = new Promise((r) => (resolveDone = r));

const client = new PipelineClient(
	(data) => ws.send(data),
	{
		onEvent: (ev) => {
			events.push(ev.type);
			const dt = () => (performance.now() - tAudioEnd).toFixed(1);
			if (ev.type === 'stt-end') lat['audio-end -> stt-end'] = dt();
			if (ev.type === 'tts-start') lat['audio-end -> tts-start'] = dt();
		},
		onReady: (h) => (handlerId = h),
		onTranscript: (t) => (transcript = t),
		onDelta: (d) => {
			if (deltas.length === 0) lat['audio-end -> first delta'] = (performance.now() - tAudioEnd).toFixed(1);
			deltas.push(d);
		},
		onResponse: (t) => (responseText = t),
		onTtsUrl: (u) => (ttsUrl = u),
		onError: (code, message) => fail(`pipeline error ${code}: ${message}`),
		onRunEnd: () => resolveDone()
	}
);
ws.on('message', (data, isBinary) => {
	if (!isBinary) client.handleMessage(data.toString());
});

// list + resolve pipeline by name
const pipelineId = await client.resolvePipelineId('Jarvis');
assert.equal(pipelineId, 'pipe-jarvis', 'pipeline resolved by name, not preferred');
console.log(`pipeline resolved: ${pipelineId}`);

client.startRun({ pipeline: pipelineId });
await new Promise((r) => {
	const iv = setInterval(() => {
		if (handlerId !== null) {
			clearInterval(iv);
			r();
		}
	}, 5);
});
console.log(`run-start OK, stt_binary_handler_id=${handlerId}`);

// --- stream 1 s of 16 kHz sine as Int16, 1024-sample frames, then end frame ---
const RATE = 16000;
const sine = new Int16Array(RATE);
for (let i = 0; i < RATE; i++) sine[i] = Math.round(Math.sin((2 * Math.PI * 440 * i) / RATE) * 16000);
let framesSent = 0;
for (let off = 0; off < sine.length; off += 1024) {
	const chunk = sine.subarray(off, Math.min(off + 1024, sine.length));
	const frame = frameAudio(handlerId, chunk);
	assert.equal(frame[0], handlerId);
	assert.equal(frame.length, 1 + chunk.length * 2);
	ws.send(frame);
	framesSent++;
}
tAudioEnd = performance.now();
ws.send(endFrame(handlerId));
console.log(`streamed ${framesSent} audio frames (${sine.length * 2} bytes PCM) + end frame`);

await done;

// --- assertions ---
assert.equal(transcript, TRANSCRIPT, 'final transcript');
assert.deepEqual(deltas, DELTAS, 'exactly 3 streaming deltas in order');
assert.equal(responseText, RESPONSE, 'full response text');
assert.equal(ttsUrl, TTS_PATH, 'tts url from tts-end');
assert.equal(client.conversationId, 'conv-mock-1', 'conversation id kept for continuity');
for (const expected of ['run-start', 'stt-start', 'stt-end', 'intent-progress', 'intent-end', 'tts-end', 'run-end']) {
	assert.ok(events.includes(expected), `event seen: ${expected}`);
}

// tts file is fetchable with bearer auth (as the node proxy would)
const res = await fetch(`${mock.url}${ttsUrl}`, {
	headers: { Authorization: `Bearer ${MOCK_TOKEN}` }
});
assert.equal(res.status, 200, 'tts fetch 200');
const bytes = new Uint8Array(await res.arrayBuffer());
assert.equal(String.fromCharCode(...bytes.slice(0, 4)), 'RIFF', 'tts payload is a WAV');
console.log(`tts fetch OK (${bytes.length} bytes)`);

console.log('\nlatency measurements (ms):');
for (const [k, v] of Object.entries(lat)) console.log(`  ${k}: ${v}`);

ws.close();
await mock.close();
clearTimeout(timeout);
console.log('\nSMOKE TEST PASS');
process.exit(0);
