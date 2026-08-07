// Mock Home Assistant server for jarvis-web tests.
//
// Implements just enough of the HA WebSocket contract:
//   - auth handshake (auth_required -> auth -> auth_ok / auth_invalid)
//   - assist_pipeline/pipeline/list
//   - assist_pipeline/run (stt -> tts) emitting the full event sequence
//   - binary stt frames: 1 prefix byte (handler id) + Int16LE PCM;
//     a 1-byte frame means end-of-audio
// and serves a real WAV file at /api/tts_proxy/test.mp3 over HTTP
// (Authorization: Bearer <token> required, like HA).
//
// Usage:  node mock-ha.mjs [port]         (standalone)
//         import { startMockHA } from './mock-ha.mjs'
import http from 'node:http';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../jarvis-web/package.json', import.meta.url));
const { WebSocketServer } = require('ws');

export const MOCK_TOKEN = 'test-token';
export const TRANSCRIPT = 'turn on the lab lights';
export const DELTAS = ['Turning ', 'on the ', 'lab lights.'];
export const RESPONSE = DELTAS.join('');
export const TTS_PATH = '/api/tts_proxy/test.mp3';

/** Minimal valid 16-bit mono WAV (decodable by decodeAudioData). */
export function makeWav(seconds = 0.25, rate = 16000, freq = 330) {
	const n = Math.floor(seconds * rate);
	const data = Buffer.alloc(44 + n * 2);
	data.write('RIFF', 0);
	data.writeUInt32LE(36 + n * 2, 4);
	data.write('WAVE', 8);
	data.write('fmt ', 12);
	data.writeUInt32LE(16, 16);
	data.writeUInt16LE(1, 20); // PCM
	data.writeUInt16LE(1, 22); // mono
	data.writeUInt32LE(rate, 24);
	data.writeUInt32LE(rate * 2, 28);
	data.writeUInt16LE(2, 32);
	data.writeUInt16LE(16, 34);
	data.write('data', 36);
	data.writeUInt32LE(n * 2, 40);
	for (let i = 0; i < n; i++) {
		const fade = Math.min(1, (n - i) / (n * 0.2));
		data.writeInt16LE(Math.round(Math.sin((2 * Math.PI * freq * i) / rate) * 12000 * fade), 44 + i * 2);
	}
	return data;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function startMockHA({ port = 0, token = MOCK_TOKEN, log = () => {} } = {}) {
	const wav = makeWav();

	const server = http.createServer((req, res) => {
		const url = new URL(req.url, 'http://internal');
		if (url.pathname === TTS_PATH) {
			if (req.headers.authorization !== `Bearer ${token}`) {
				res.writeHead(401);
				res.end('unauthorized');
				return;
			}
			res.writeHead(200, { 'content-type': 'audio/wav' });
			res.end(wav);
			return;
		}
		res.writeHead(404);
		res.end('not found');
	});

	const wss = new WebSocketServer({ server, path: '/api/websocket' });

	wss.on('connection', (socket) => {
		let authed = false;
		socket.send(JSON.stringify({ type: 'auth_required', ha_version: '2025.1.0' }));

		/** @type {null | {id:number, handlerId:number, audioBytes:number, done:boolean}} */
		let run = null;

		const event = (id, type, data = {}) =>
			socket.send(
				JSON.stringify({
					id,
					type: 'event',
					event: { type, data, timestamp: new Date().toISOString() }
				})
			);

		async function finishRun(r) {
			if (r.done) return;
			r.done = true;
			log(`mock-ha: end-of-audio after ${r.audioBytes} PCM bytes`);
			event(r.id, 'stt-vad-end', {});
			await sleep(20);
			event(r.id, 'stt-end', { stt_output: { text: TRANSCRIPT } });
			await sleep(15);
			event(r.id, 'intent-start', { engine: 'conversation' });
			for (const delta of DELTAS) {
				await sleep(25);
				event(r.id, 'intent-progress', { chat_log_delta: { content: delta } });
			}
			await sleep(15);
			event(r.id, 'intent-end', {
				intent_output: {
					conversation_id: 'conv-mock-1',
					response: { speech: { plain: { speech: RESPONSE } } }
				}
			});
			await sleep(10);
			event(r.id, 'tts-start', { engine: 'tts.piper' });
			await sleep(20);
			event(r.id, 'tts-end', { tts_output: { url: TTS_PATH, mime_type: 'audio/wav' } });
			await sleep(10);
			event(r.id, 'run-end', {});
			run = null;
		}

		socket.on('message', (data, isBinary) => {
			if (isBinary) {
				const buf = Buffer.from(data);
				if (!run || buf[0] !== run.handlerId) return;
				if (buf.length === 1) {
					void finishRun(run);
				} else {
					run.audioBytes += buf.length - 1;
					if (run.audioBytes === buf.length - 1) event(run.id, 'stt-vad-start', {});
				}
				return;
			}
			let msg;
			try {
				msg = JSON.parse(data.toString());
			} catch {
				return;
			}

			if (!authed) {
				if (msg.type === 'auth') {
					if (msg.access_token === token) {
						authed = true;
						socket.send(JSON.stringify({ type: 'auth_ok', ha_version: '2025.1.0' }));
					} else {
						socket.send(JSON.stringify({ type: 'auth_invalid', message: 'bad token' }));
						socket.close();
					}
				}
				return;
			}

			switch (msg.type) {
				case 'assist_pipeline/pipeline/list':
					socket.send(
						JSON.stringify({
							id: msg.id,
							type: 'result',
							success: true,
							result: {
								pipelines: [
									{ id: 'pipe-other', name: 'Home Assistant' },
									{ id: 'pipe-jarvis', name: 'Jarvis' }
								],
								preferred_pipeline: 'pipe-other'
							}
						})
					);
					break;
				case 'assist_pipeline/run': {
					run = { id: msg.id, handlerId: 1, audioBytes: 0, done: false };
					socket.send(JSON.stringify({ id: msg.id, type: 'result', success: true, result: null }));
					event(msg.id, 'run-start', {
						pipeline: msg.pipeline ?? 'pipe-other',
						runner_data: { stt_binary_handler_id: run.handlerId, timeout: 300 }
					});
					event(msg.id, 'stt-start', {
						engine: 'stt.whisper',
						metadata: { sample_rate: msg.input?.sample_rate ?? 16000 }
					});
					break;
				}
				default:
					socket.send(
						JSON.stringify({
							id: msg.id,
							type: 'result',
							success: false,
							error: { code: 'unknown_command', message: `unhandled: ${msg.type}` }
						})
					);
			}
		});
	});

	return new Promise((resolve) => {
		server.listen(port, '127.0.0.1', () => {
			const actualPort = server.address().port;
			log(`mock-ha listening on http://127.0.0.1:${actualPort}`);
			resolve({
				port: actualPort,
				url: `http://127.0.0.1:${actualPort}`,
				close: () =>
					new Promise((r) => {
						for (const c of wss.clients) c.terminate();
						server.close(r);
					})
			});
		});
	});
}

// Standalone mode
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
	const port = Number(process.argv[2] ?? process.env.MOCK_HA_PORT ?? 8123);
	startMockHA({ port, log: console.log });
}
