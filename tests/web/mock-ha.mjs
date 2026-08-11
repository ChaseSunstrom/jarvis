// Mock backend for jarvis-web tests.
//
// jarvis-core and Home Assistant expose the same websocket contract, so one
// mock covers both. Implements:
//   - auth handshake (auth_required -> auth -> auth_ok / auth_invalid)
//   - assist_pipeline/pipeline/list
//   - assist_pipeline/run (stt -> tts) emitting the full event sequence
//   - binary stt frames: 1 prefix byte (handler id) + Int16LE PCM;
//     a 1-byte frame means end-of-audio
//   - the management commands: get_states, get_config, get_services,
//     call_service (which really mutates state and pushes state_changed),
//     subscribe_events / unsubscribe_events and the three registries
// and serves a real WAV file at /api/tts_proxy/test.mp3 over HTTP
// (Authorization: Bearer <token> required).
//
// Commands it deliberately does NOT know (jarvis/tools/list) answer
// unknown_command, which is what the client's graceful-degradation path
// expects to see.
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

const nowIso = () => new Date().toISOString();

/** A state object in the wire shape both backends use. */
function mkState(entity_id, state, attributes = {}) {
	return {
		entity_id,
		state,
		attributes,
		last_changed: nowIso(),
		last_updated: nowIso(),
		context: { id: 'ctx-mock', parent_id: null, user_id: null }
	};
}

const slug = (text) =>
	String(text)
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '_')
		.replace(/^_|_$/g, '');

/**
 * The shape checks jarvis-core's `validate` applies, or null if it would pass.
 *
 * Only the ones the console can actually trip: the point is that a server-side
 * refusal reaches the form, not to re-implement the validator.
 */
function badAutomation(draft) {
	if (!draft || typeof draft !== 'object') return 'An automation must be an object.';
	if (!String(draft.alias ?? '').trim()) return 'Give it a name.';
	if (!Array.isArray(draft.trigger) || draft.trigger.length === 0) {
		return 'Give it at least one trigger, or nothing will ever run it.';
	}
	if (!Array.isArray(draft.action) || draft.action.length === 0) {
		return 'Give it at least one action, or it will run and do nothing.';
	}
	for (const step of draft.trigger) {
		if (!String(step?.platform ?? step?.trigger ?? '').trim()) {
			return 'Every trigger needs a `platform`.';
		}
	}
	return null;
}

/** The checks `authored_tools.validate` applies that the console can trip. */
function badTool(draft) {
	if (!draft || typeof draft !== 'object') return 'A tool must be an object.';
	if (!/^[a-z][a-z0-9_]{2,47}$/.test(String(draft.name ?? ''))) {
		return 'The name must be 3-48 characters, lowercase letters, digits and underscores.';
	}
	if (!String(draft.description ?? '').trim()) {
		return 'Describe what it does, or the model cannot use it.';
	}
	if (![1, 2, 3].includes(Number(draft.tier ?? 1))) return 'Tier must be 1, 2 or 3.';
	const url = String(draft.service?.url ?? '');
	if (!/^https?:\/\//i.test(url)) return 'The url must start with http:// or https://.';
	return null;
}

/** Areas, devices, entity registry entries and states, as a fresh world. */
export function makeWorld() {
	const areas = [
		{ id: 'lab', name: 'Lab', aliases: [] },
		{ id: 'living_room', name: 'Living Room', aliases: [] },
		{ id: 'garage', name: 'Garage', aliases: [] }
	];
	const devices = [
		{
			id: 'dev-lab-1',
			name: 'Lab Controller',
			manufacturer: 'Stark',
			model: 'MK1',
			area_id: 'lab',
			platform: 'demo',
			identifiers: ['demo:lab'],
			connections: [],
			disabled: false
		}
	];
	const states = new Map(
		[
			mkState('light.lab_lights', 'off', {
				friendly_name: 'Lab Lights',
				brightness: 0,
				supported_color_modes: ['brightness']
			}),
			mkState('switch.desk_fan', 'off', { friendly_name: 'Desk Fan' }),
			mkState('sensor.lab_temperature', '21.4', {
				friendly_name: 'Lab Temperature',
				unit_of_measurement: '°C',
				device_class: 'temperature'
			}),
			mkState('cover.garage_door', 'closed', {
				friendly_name: 'Garage Door',
				current_position: 0
			}),
			mkState('climate.thermostat', 'heat', {
				friendly_name: 'Thermostat',
				temperature: 20,
				current_temperature: 19.2,
				hvac_modes: ['off', 'heat', 'cool']
			}),
			mkState('media_player.speaker', 'idle', {
				friendly_name: 'Speaker',
				volume_level: 0.4,
				media_title: 'nothing'
			}),
			mkState('automation.night_mode', 'on', {
				friendly_name: 'Night Mode',
				last_triggered: '2026-01-02T03:04:05+00:00'
			}),
			mkState('automation.morning_lights', 'off', {
				friendly_name: 'Morning Lights',
				last_triggered: null
			}),
			// A lock with no `lock` domain in SERVICES below, and no entity
			// registry entry: it stands in for the very ordinary case of a UI that
			// offers a control the backend cannot actually perform. Pressing LOCK
			// answers `service_not_found`, which is what the e2e suite uses to prove
			// a rejected call_service is surfaced instead of swallowed.
			mkState('lock.front_door', 'locked', { friendly_name: 'Front Door' })
		].map((s) => [s.entity_id, s])
	);
	const entities = [
		['light.lab_lights', 'lab', 'dev-lab-1'],
		['switch.desk_fan', 'lab', 'dev-lab-1'],
		['sensor.lab_temperature', null, 'dev-lab-1'],
		['cover.garage_door', 'garage', null],
		['climate.thermostat', 'living_room', null],
		['media_player.speaker', 'living_room', null],
		['automation.night_mode', null, null],
		['automation.morning_lights', null, null]
	].map(([entity_id, area_id, device_id]) => ({
		entity_id,
		unique_id: `mock-${entity_id}`,
		platform: 'demo',
		name: null,
		original_name: states.get(entity_id).attributes.friendly_name,
		device_id,
		area_id,
		aliases: [],
		icon: null,
		disabled: false,
		hidden: false,
		exposed: true,
		capabilities: {}
	}));

	// Both seeded automations come "from YAML" — no `ui_` prefix — so the suite
	// starts with something the console must refuse to edit, and anything it
	// can edit had to be created through the API under test.
	const automations = [
		{
			id: 'night_mode',
			entity_id: 'automation.night_mode',
			alias: 'Night Mode',
			description: '',
			mode: 'single',
			enabled: true,
			trigger: [{ platform: 'time', at: '23:00:00' }],
			condition: [],
			action: [{ service: 'light.turn_off' }, { service: 'lock.lock' }],
			editable: false,
			// Running it can reach a lock, so jarvis-core holds it for a human.
			needs_approval: true,
			reach: 'can lock',
		},
		{
			id: 'morning_lights',
			entity_id: 'automation.morning_lights',
			alias: 'Morning Lights',
			description: '',
			mode: 'single',
			enabled: false,
			trigger: [{ platform: 'time', at: '07:00:00' }],
			condition: [],
			action: [{ service: 'light.turn_on' }],
			editable: false,
			needs_approval: false,
			reach: 'touches nothing that needs approval',
		}
	];

	// One of each interesting shape: a live choice, a restart-only number, a
	// plain string, and one a package owns so the locked path is exercised.
	const settings = [
		{
			key: 'llm.model',
			label: 'Model',
			group: 'Assistant',
			type: 'choice',
			apply: 'live',
			note: 'The Ollama model every conversation runs on.',
			value: 'qwen3:8b',
			yaml_value: 'qwen3:8b',
			source: 'yaml',
			unapplied_reason: null,
			package: null,
			choices: ['qwen3:8b', 'qwen3:14b', 'llama3.2:3b']
		},
		{
			key: 'llm.options.temperature',
			label: 'Temperature',
			group: 'Assistant',
			type: 'number',
			apply: 'live',
			note: 'Higher is more inventive.',
			value: 0.7,
			yaml_value: 0.7,
			source: 'yaml',
			unapplied_reason: null,
			package: null
		},
		{
			key: 'llm.timeout',
			label: 'Model timeout',
			group: 'Assistant',
			type: 'number',
			apply: 'restart',
			note: 'Baked into the shared HTTP client, so this one needs a restart.',
			value: 60,
			yaml_value: 60,
			source: 'yaml',
			unapplied_reason: null,
			package: null
		},
		{
			key: 'jarvis.name',
			label: 'Name',
			group: 'House',
			type: 'string',
			apply: 'live',
			note: 'What this instance calls itself.',
			value: 'Jarvis',
			yaml_value: 'Jarvis',
			source: 'yaml',
			unapplied_reason: null,
			package: null
		},
		{
			key: 'jarvis.time_zone',
			label: 'Time zone',
			group: 'House',
			type: 'string',
			apply: 'live',
			note: 'Used by every time trigger and by {{ now() }}.',
			value: 'Europe/London',
			yaml_value: null,
			source: 'package',
			unapplied_reason: null,
			package: 'house'
		},
		{
			// The one editable copy of the TTS voice. The settings page used to
			// carry a second, read-only one in a hand-rolled "Voice pipeline"
			// panel; this fixture is what proves the surviving one is the
			// editable one and that it lives in the group that can save it.
			key: 'voice.tts_voice',
			label: 'TTS voice',
			group: 'Voice',
			type: 'choice',
			apply: 'live',
			note: 'What Jarvis sounds like.',
			value: 'en_GB-alan-medium',
			yaml_value: 'en_GB-alan-medium',
			source: 'yaml',
			unapplied_reason: null,
			package: null,
			choices: ['en_GB-alan-medium', 'en_US-amy-medium', 'en_GB-northern_english_male-medium']
		}
	];

	// Two built-ins, so a console tool has something it must refuse to shadow
	// and something it must refuse to delete.
	const tools = [
		{
			name: 'lock_control',
			description: 'Lock or unlock a door',
			tier: 3,
			domain: 'lock',
			parameters: null,
			editable: false,
			service: null
		},
		{
			name: 'turn_on',
			description: 'Turn something on',
			tier: 1,
			domain: null,
			parameters: null,
			editable: false,
			service: null
		}
	];

	// One online and one not: "connected" is the fact the panel exists to show,
	// so a fixture where everything is online would not prove it is read.
	const companions = [
		{
			device_id: 'pixel-8',
			name: 'Pixel 8',
			platform: 'android',
			capabilities: ['notify', 'ui_automation'],
			connected: true,
			app_version: '1.0.32',
			action_count: 48,
			actions: []
		},
		{
			device_id: 'workshop-desktop',
			name: 'Workshop Desktop',
			platform: 'linux',
			capabilities: ['shell'],
			connected: false,
			app_version: '0.9.0',
			action_count: 12,
			actions: []
		}
	];

	return {
		areas, devices, entities, states, automations, settings, tools,
		companions, approvals: [], calls: [],
		// Pairing: a counter for readable code names and the live set, so the
		// single-use rule is exercised rather than assumed.
		pairingCodes: 0, livePairingCodes: new Set()
	};
}

const SERVICES = {
	light: {
		turn_on: { description: 'Turn on a light.', fields: { brightness: {} }, supports_response: false },
		turn_off: { description: 'Turn off a light.', fields: {}, supports_response: false },
		toggle: { description: 'Toggle a light.', fields: {}, supports_response: false }
	},
	switch: {
		turn_on: { description: 'Turn on a switch.', fields: {}, supports_response: false },
		turn_off: { description: 'Turn off a switch.', fields: {}, supports_response: false },
		toggle: { description: 'Toggle a switch.', fields: {}, supports_response: false }
	},
	cover: {
		open_cover: { description: 'Open a cover.', fields: {}, supports_response: false },
		close_cover: { description: 'Close a cover.', fields: {}, supports_response: false },
		stop_cover: { description: 'Stop a cover.', fields: {}, supports_response: false },
		set_cover_position: {
			description: 'Move a cover to a position.',
			fields: { position: {} },
			supports_response: false
		}
	},
	climate: {
		set_temperature: {
			description: "Set a thermostat's target temperature.",
			fields: { temperature: {} },
			supports_response: false
		},
		set_hvac_mode: {
			description: "Set a thermostat's HVAC mode.",
			fields: { hvac_mode: {} },
			supports_response: false
		}
	},
	media_player: {
		media_play: { description: 'Resume playback.', fields: {}, supports_response: false },
		media_pause: { description: 'Pause playback.', fields: {}, supports_response: false },
		media_stop: { description: 'Stop playback.', fields: {}, supports_response: false },
		media_next_track: { description: 'Next track.', fields: {}, supports_response: false },
		media_previous_track: { description: 'Previous track.', fields: {}, supports_response: false },
		volume_set: {
			description: 'Set the volume.',
			fields: { volume_level: {} },
			supports_response: false
		}
	},
	automation: {
		turn_on: { description: 'Enable an automation.', fields: {}, supports_response: false },
		turn_off: { description: 'Disable an automation.', fields: {}, supports_response: false },
		toggle: { description: 'Toggle an automation.', fields: {}, supports_response: false },
		trigger: { description: "Run an automation's actions.", fields: {}, supports_response: false }
	}
};

/**
 * Apply a service call to one entity, returning the new state (or null when
 * the call does not move it).
 */
function applyService(current, domain, service, data) {
	const attrs = { ...current.attributes };
	const on = ['on', 'open', 'playing', 'heat', 'cool'].includes(current.state);
	switch (service) {
		case 'turn_on':
			if (domain === 'light' && data.brightness !== undefined) {
				attrs.brightness = Number(data.brightness);
			} else if (domain === 'light') {
				attrs.brightness = 255;
			}
			return { state: 'on', attrs };
		case 'turn_off':
			if (domain === 'light') attrs.brightness = 0;
			return { state: 'off', attrs };
		case 'toggle':
			if (domain === 'light') attrs.brightness = on ? 0 : 255;
			return { state: on ? 'off' : 'on', attrs };
		case 'open_cover':
			attrs.current_position = 100;
			return { state: 'open', attrs };
		case 'close_cover':
			attrs.current_position = 0;
			return { state: 'closed', attrs };
		case 'stop_cover':
			return { state: current.state, attrs };
		case 'set_cover_position':
			attrs.current_position = Number(data.position ?? 0);
			return { state: attrs.current_position > 0 ? 'open' : 'closed', attrs };
		case 'set_temperature':
			attrs.temperature = Number(data.temperature);
			return { state: current.state, attrs };
		case 'set_hvac_mode':
			return { state: String(data.hvac_mode), attrs };
		case 'media_play':
			return { state: 'playing', attrs };
		case 'media_pause':
			return { state: 'paused', attrs };
		case 'media_stop':
			return { state: 'idle', attrs };
		case 'media_next_track':
			attrs.media_title = 'next track';
			return { state: current.state, attrs };
		case 'media_previous_track':
			attrs.media_title = 'previous track';
			return { state: current.state, attrs };
		case 'volume_set':
			attrs.volume_level = Number(data.volume_level ?? 0);
			return { state: current.state, attrs };
		case 'trigger':
			attrs.last_triggered = nowIso();
			return { state: current.state, attrs };
		default:
			return null;
	}
}

export function startMockHA({ port = 0, token = MOCK_TOKEN, log = () => {} } = {}) {
	const wav = makeWav();
	const world = makeWorld();
	/** @type {Set<{socket: any, id: number, eventType: string|null}>} */
	const subscriptions = new Set();

	const broadcast = (eventType, data) => {
		const event = {
			event_type: eventType,
			data,
			origin: 'LOCAL',
			time_fired: nowIso(),
			context: { id: 'ctx-mock', parent_id: null, user_id: null }
		};
		for (const sub of subscriptions) {
			if (sub.eventType && sub.eventType !== eventType) continue;
			if (sub.socket.readyState !== 1) continue;
			sub.socket.send(JSON.stringify({ id: sub.id, type: 'event', event }));
		}
	};

	/** Run a service against every targeted entity; returns changed states. */
	const callService = (domain, service, data) => {
		world.calls.push({ domain, service, data, at: nowIso() });
		const targets = []
			.concat(data.entity_id ?? [])
			.flatMap((id) => (world.states.has(id) ? [id] : []));
		const changed = [];
		for (const entityId of targets) {
			const current = world.states.get(entityId);
			const next = applyService(current, domain, service, data);
			if (!next) continue;
			const updated = {
				...current,
				state: next.state,
				attributes: next.attrs,
				last_changed: nowIso(),
				last_updated: nowIso()
			};
			world.states.set(entityId, updated);
			changed.push(updated);
			broadcast('state_changed', {
				entity_id: entityId,
				old_state: current,
				new_state: updated
			});
		}
		return changed;
	};

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
		// Test-only introspection: what service calls has the mock seen?
		if (url.pathname === '/_test/calls') {
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify(world.calls));
			return;
		}
		// Stands in for the token-protected REST surface a real backend has
		// (/api/states, /api/config, ...). Nothing but the /api/tts_proxy/ media
		// paths should ever be reachable through jarvis-web's /api/tts proxy, so
		// the e2e suite tries to reach this and asserts that it cannot.
		if (url.pathname === '/_test/protected') {
			if (req.headers.authorization !== `Bearer ${token}`) {
				res.writeHead(401);
				res.end('unauthorized');
				return;
			}
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ secret: 'admin-only-payload' }));
			return;
		}
		// Pairing. `/api/pair/new` is authenticated because inviting a device
		// onto the house is something only somebody already inside may do;
		// `/api/pair/claim` is not, because the phone has no credential yet.
		if (url.pathname === '/api/pair/new') {
			if (req.headers.authorization !== `Bearer ${token}`) {
				res.writeHead(401);
				res.end('unauthorized');
				return;
			}
			const code = `mock-code-${++world.pairingCodes}`;
			world.livePairingCodes.add(code);
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(JSON.stringify({ code, expires_at: Date.now() / 1000 + 300, ttl: 300 }));
			return;
		}
		if (url.pathname === '/api/pair/claim' && req.method === 'POST') {
			let body = '';
			req.on('data', (chunk) => (body += chunk));
			req.on('end', () => {
				let parsed = {};
				try {
					parsed = JSON.parse(body || '{}');
				} catch {
					parsed = {};
				}
				// Single use, exactly as the real one: spent before anything else.
				if (!world.livePairingCodes.delete(parsed.code)) {
					res.writeHead(403, { 'content-type': 'application/json' });
					res.end(JSON.stringify({ detail: 'That pairing code is not valid, or it has expired.' }));
					return;
				}
				res.writeHead(200, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ token: 'paired-token', name: parsed.name ?? 'Paired device' }));
			});
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

		const mySubs = new Map();
		socket.on('close', () => {
			for (const sub of mySubs.values()) subscriptions.delete(sub);
			mySubs.clear();
		});

		const ok = (id, result = null) =>
			socket.send(JSON.stringify({ id, type: 'result', success: true, result }));
		const fail = (id, code, message) =>
			socket.send(
				JSON.stringify({ id, type: 'result', success: false, error: { code, message } })
			);
		const findArea = (areaId) => world.areas.find((a) => a.id === areaId);

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
				case 'ping':
					socket.send(JSON.stringify({ id: msg.id, type: 'pong' }));
					break;

				case 'get_states':
					ok(msg.id, [...world.states.values()]);
					break;

				case 'get_services':
					ok(msg.id, SERVICES);
					break;

				case 'get_config':
					ok(msg.id, {
						location_name: 'Mock',
						version: '0.1.0',
						ha_version: 'jarvis-0.1.0',
						state: 'RUNNING',
						areas: world.areas
					});
					break;

				case 'call_service': {
					const domain = msg.domain;
					const service = msg.service;
					// The approvals banner asks for what is already waiting when it
					// mounts, so a reload does not lose a held action.
					if (domain === 'llm' && service === 'pending_requests') {
						ok(msg.id, { response: world.approvals });
						break;
					}
					if (!SERVICES[domain]?.[service]) {
						fail(msg.id, 'service_not_found', `unknown service ${domain}.${service}`);
						break;
					}
					const data = { ...(msg.service_data ?? {}), ...(msg.target ?? {}) };
					const changed = callService(domain, service, data);
					ok(msg.id, { context: { id: 'ctx-mock' }, changed_states: changed });
					break;
				}

				// Answering a held tier-3 action. Single use, like jarvis-core:
				// the request is removed before anything runs, so a second click
				// cannot execute it twice.
				case 'jarvis/approve': {
					const index = world.approvals.findIndex((a) => a.request_id === msg.request_id);
					if (index < 0) {
						ok(msg.id, {
							status: 'error',
							error: 'unknown, expired or already-used approval request'
						});
						break;
					}
					const [req] = world.approvals.splice(index, 1);
					broadcast('jarvis_approval_resolved', {
						...req,
						approved: Boolean(msg.approved)
					});
					ok(msg.id, {
						status: msg.approved ? 'executed' : 'denied',
						request_id: req.request_id,
						// Echoed so a test can prove the answer reached the
						// server rather than only leaving the input box. The real
						// jarvis-core returns it inside the tool's result.
						result: req.answerable && msg.approved
							? { question: req.arguments?.question, answer: msg.answer ?? null }
							: undefined
					});
					// The console can then read it back — again, only for
					// proving the round trip; nothing in the app uses this.
					world.lastAnswer = msg.answer ?? null;
					break;
				}

				// Test hook: raise one, so the e2e suite can drive the banner the
				// way the assistant would. Not a jarvis-core command — the real
				// event is fired by the tool registry when a gate holds an action.
				case 'test/raise_approval': {
					const req = {
						request_id: msg.request_id ?? `req-${world.approvals.length + 1}`,
						tool: msg.tool ?? 'lock_control',
						description: 'Lock or unlock a door.',
						arguments: msg.arguments ?? { action: 'unlock', entity_id: ['lock.front_door'] },
						tier: 3,
						created: Date.now() / 1000,
						expires_at: Date.now() / 1000 + 300
					};
					world.approvals.push(req);
					broadcast('jarvis_approval_required', req);
					ok(msg.id, { raised: req.request_id });
					break;
				}

				// Test hook: the assistant asking the user something. Same gate,
				// same event, same expiry — the only difference on the wire is
				// `answerable`, which names the one argument the reply may write.
				// Test hook: what the last answer actually was, server-side. The
				// point of the round trip is that the text left the browser, and
				// only the server can say whether it arrived.
				case 'jarvis/test/last_answer':
					ok(msg.id, { answer: world.lastAnswer ?? null });
					break;

				case 'jarvis/test/ask_user': {
					const req = {
						request_id: msg.request_id ?? `ask-${world.approvals.length + 1}`,
						tool: 'ask_user',
						description: 'Ask the user a question and wait for their answer.',
						arguments: { question: msg.question ?? 'Which lamp did you mean?' },
						choices: Array.isArray(msg.choices) ? msg.choices : [],
						answerable: 'answer',
						tier: 3,
						created: Date.now() / 1000,
						expires_at: Date.now() / 1000 + 300
					};
					world.approvals.push(req);
					broadcast('jarvis_approval_required', req);
					ok(msg.id, { raised: req.request_id });
					break;
				}

				case 'subscribe_events': {
					const sub = { socket, id: msg.id, eventType: msg.event_type ?? null };
					subscriptions.add(sub);
					mySubs.set(msg.id, sub);
					ok(msg.id, null);
					break;
				}

				case 'unsubscribe_events': {
					const sub = mySubs.get(msg.subscription);
					if (!sub) {
						fail(msg.id, 'not_found', `no subscription ${msg.subscription}`);
						break;
					}
					subscriptions.delete(sub);
					mySubs.delete(msg.subscription);
					ok(msg.id, null);
					break;
				}

				// A turn's worth of tool calls, on demand, so the console's
				// tool activity can be driven from a test rather than by
				// waiting for a model to decide to call something.
				case 'jarvis/test/tool_run': {
					const names = Array.isArray(msg.tools) && msg.tools.length
						? msg.tools
						: ['get_state', 'turn_on'];
					const failAt = Number.isInteger(msg.fail_at) ? msg.fail_at : -1;
					ok(msg.id, { started: names.length });
					names.forEach((name, index) => {
						setTimeout(() => {
							broadcast('jarvis_tool_started', {
								name,
								arguments: { name: 'kitchen lamp' },
								round: 1,
								index,
								total: names.length
							});
							setTimeout(() => {
								const failed = index === failAt;
								broadcast('jarvis_tool_finished', {
									name,
									round: 1,
									index,
									total: names.length,
									ok: !failed,
									status: failed ? 'error' : 'ok',
									error: failed ? 'no such entity' : null,
									duration_ms: 40 + index * 10
								});
							}, 60);
						}, index * 40);
					});
					break;
				}

				case 'config/area_registry/list':
					ok(msg.id, world.areas);
					break;

				case 'config/area_registry/create': {
					const name = String(msg.name ?? '').trim();
					if (!name) {
						fail(msg.id, 'invalid_format', 'name is required');
						break;
					}
					const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
					const existing = findArea(id);
					if (existing) {
						ok(msg.id, existing);
						break;
					}
					const area = { id, name, aliases: msg.aliases ?? [] };
					world.areas.push(area);
					broadcast('area_registry_updated', { action: 'create', area_id: id });
					ok(msg.id, area);
					break;
				}

				case 'config/area_registry/update': {
					const area = findArea(msg.area_id);
					if (!area) {
						fail(msg.id, 'not_found', `unknown area ${msg.area_id}`);
						break;
					}
					if (msg.name != null) area.name = msg.name;
					if (msg.aliases != null) area.aliases = msg.aliases;
					broadcast('area_registry_updated', { action: 'update', area_id: area.id });
					ok(msg.id, area);
					break;
				}

				case 'config/area_registry/delete': {
					const index = world.areas.findIndex((a) => a.id === msg.area_id);
					if (index < 0) {
						fail(msg.id, 'not_found', `unknown area ${msg.area_id}`);
						break;
					}
					world.areas.splice(index, 1);
					for (const entry of world.entities) {
						if (entry.area_id === msg.area_id) entry.area_id = null;
					}
					broadcast('area_registry_updated', { action: 'remove', area_id: msg.area_id });
					ok(msg.id, { area_id: msg.area_id, deleted: true });
					break;
				}

				// Tools. Identity is the name, so the interesting cases are a
				// console tool refusing to take a built-in's name and a delete
				// refusing to reach one.
				// The phones and desktops on the other end of the socket, as
				// opposed to `config/device_registry/list`, which is the house.
				// Test hook: a phone that registers while the console is open.
				// The console used to load this list once at mount, so a device
				// that arrived afterwards was invisible until a reload — which
				// is what "I registered my android device but the web app still
				// doesn't recognize it" looks like from the browser side.
				case 'jarvis/test/register_companion': {
					const device = {
						device_id: msg.device_id ?? 'late-phone',
						name: msg.name ?? 'Late Phone',
						platform: 'android',
						capabilities: ['notify'],
						connected: true,
						app_version: '1.0.40',
						action_count: 7,
						actions: []
					};
					world.companions.push(device);
					ok(msg.id, { registered: device.device_id });
					broadcast('jarvis_device_registered', device);
					break;
				}

				case 'config/companion/list':
					ok(msg.id, world.companions);
					break;

				case 'config/tool/list':
					ok(msg.id, world.tools);
					break;

				case 'config/tool/create': {
					const draft = msg.tool ?? {};
					const problem = badTool(draft);
					if (problem) {
						fail(msg.id, 'invalid_format', problem);
						break;
					}
					if (world.tools.some((t) => t.name === draft.name)) {
						fail(
							msg.id,
							'invalid_format',
							`${draft.name} is already a tool. Pick another name.`
						);
						break;
					}
					world.tools.push({
						name: draft.name,
						description: draft.description,
						tier: draft.tier ?? 1,
						domain: null,
						parameters: null,
						editable: true,
						service: draft.service
					});
					ok(msg.id, { tool: world.tools[world.tools.length - 1] });
					break;
				}

				case 'config/tool/update': {
					const row = world.tools.find((t) => t.name === msg.name);
					if (!row || !row.editable) {
						fail(msg.id, 'invalid_format', `${msg.name} is not a tool this console created.`);
						break;
					}
					const draft = msg.tool ?? {};
					if (draft.name !== row.name) {
						fail(msg.id, 'invalid_format', "A tool's name cannot be changed.");
						break;
					}
					const problem = badTool(draft);
					if (problem) {
						fail(msg.id, 'invalid_format', problem);
						break;
					}
					Object.assign(row, {
						description: draft.description,
						tier: draft.tier ?? 1,
						service: draft.service
					});
					ok(msg.id, { tool: row });
					break;
				}

				case 'config/tool/delete': {
					const index = world.tools.findIndex((t) => t.name === msg.name);
					if (index < 0 || !world.tools[index].editable) {
						fail(
							msg.id,
							'not_supported',
							`${msg.name} is not a tool this console created — it is built in ` +
								'or comes from your YAML, so it cannot be deleted here.'
						);
						break;
					}
					world.tools.splice(index, 1);
					ok(msg.id, { name: msg.name, deleted: true });
					break;
				}

				// Settings. The console's contract is that `set` answers with the
				// whole refreshed list plus whether the change is already in
				// effect, so the page never has to guess or re-fetch.
				case 'config/settings/list':
					ok(msg.id, { settings: world.settings, unapplied: [] });
					break;

				case 'config/settings/set': {
					const row = world.settings.find((s) => s.key === msg.key);
					if (!row) {
						fail(msg.id, 'not_found', `${msg.key} is not an editable setting`);
						break;
					}
					if (row.source === 'package') {
						fail(msg.id, 'invalid_format', `packages/${row.package}.yaml sets this`);
						break;
					}
					if (row.type === 'number' || row.type === 'integer') {
						const n = Number(msg.value);
						if (!Number.isFinite(n)) {
							fail(msg.id, 'invalid_format', 'Expected a number.');
							break;
						}
						if (row.key === 'llm.options.temperature' && (n < 0 || n > 2)) {
							fail(msg.id, 'invalid_format', 'Must be between 0.0 and 2.0.');
							break;
						}
						row.value = n;
					} else {
						if (!String(msg.value ?? '').trim()) {
							fail(msg.id, 'invalid_format', 'This cannot be empty.');
							break;
						}
						row.value = msg.value;
					}
					row.source = 'overlay';
					ok(msg.id, {
						key: row.key,
						value: row.value,
						applied: row.apply === 'live',
						apply: row.apply,
						restart_required: row.apply !== 'live',
						settings: world.settings
					});
					break;
				}

				case 'config/settings/reset': {
					const row = world.settings.find((s) => s.key === msg.key);
					if (!row) {
						fail(msg.id, 'not_found', `${msg.key} is not an editable setting`);
						break;
					}
					row.value = row.yaml_value;
					row.source = row.yaml_value == null ? 'default' : 'yaml';
					ok(msg.id, {
						key: row.key,
						value: row.value,
						applied: row.apply === 'live',
						apply: row.apply,
						restart_required: row.apply !== 'live',
						settings: world.settings
					});
					break;
				}

				// Automations. Mirrors jarvis-core closely enough to be worth
				// having: ids are namespaced `ui_`, only those are editable, and a
				// YAML id refuses rather than silently doing nothing — which is
				// the behaviour the console's read-only path is written against.
				case 'config/automation/list':
					ok(msg.id, world.automations);
					break;

				case 'config/automation/create': {
					const draft = msg.automation ?? {};
					const problem = badAutomation(draft);
					if (problem) {
						fail(msg.id, 'invalid_format', problem);
						break;
					}
					const autoId = `ui_${Math.random().toString(16).slice(2, 14)}`;
					const entityId = `automation.${slug(draft.alias)}`;
					const row = {
						id: autoId,
						entity_id: entityId,
						alias: draft.alias,
						description: draft.description ?? '',
						mode: draft.mode ?? 'single',
						enabled: true,
						trigger: draft.trigger ?? [],
						condition: draft.condition ?? [],
						action: draft.action ?? [],
						editable: true,
						needs_approval: false,
						reach: 'touches nothing that needs approval'
					};
					world.automations.push(row);
					world.states.set(
						entityId,
						mkState(entityId, 'on', { friendly_name: draft.alias, last_triggered: null })
					);
					broadcast('state_changed', {
						entity_id: entityId,
						old_state: null,
						new_state: world.states.get(entityId)
					});
					ok(msg.id, { automation: row });
					break;
				}

				case 'config/automation/update': {
					const row = world.automations.find((a) => a.id === msg.automation_id);
					if (!row || !row.editable) {
						fail(
							msg.id,
							'invalid_format',
							`${msg.automation_id} comes from your YAML, not from the console. ` +
								'Edit automations.yaml to change it.'
						);
						break;
					}
					const draft = msg.automation ?? {};
					const problem = badAutomation(draft);
					if (problem) {
						fail(msg.id, 'invalid_format', problem);
						break;
					}
					Object.assign(row, {
						alias: draft.alias,
						description: draft.description ?? '',
						mode: draft.mode ?? 'single',
						trigger: draft.trigger ?? [],
						condition: draft.condition ?? [],
						action: draft.action ?? []
					});
					const state = world.states.get(row.entity_id);
					if (state) state.attributes.friendly_name = draft.alias;
					broadcast('state_changed', {
						entity_id: row.entity_id,
						old_state: state,
						new_state: state
					});
					ok(msg.id, { automation: row });
					break;
				}

				case 'config/automation/delete': {
					if (!String(msg.automation_id ?? '').startsWith('ui_')) {
						fail(
							msg.id,
							'not_supported',
							`${msg.automation_id} comes from your YAML, not from the console. ` +
								'Edit automations.yaml to change it.'
						);
						break;
					}
					const index = world.automations.findIndex((a) => a.id === msg.automation_id);
					if (index < 0) {
						fail(msg.id, 'not_found', `unknown automation ${msg.automation_id}`);
						break;
					}
					const [gone] = world.automations.splice(index, 1);
					world.states.delete(gone.entity_id);
					broadcast('state_changed', {
						entity_id: gone.entity_id,
						old_state: null,
						new_state: null
					});
					ok(msg.id, { automation_id: gone.id, deleted: true });
					break;
				}

				case 'config/entity_registry/list':
					ok(msg.id, world.entities);
					break;

				case 'config/entity_registry/update': {
					const entry = world.entities.find((e) => e.entity_id === msg.entity_id);
					if (!entry) {
						fail(msg.id, 'not_found', `unknown entity ${msg.entity_id}`);
						break;
					}
					// Matches jarvis-core: null-valued fields are skipped, so '' is
					// how a client clears an assignment.
					for (const field of ['name', 'icon', 'area_id', 'device_id', 'aliases', 'disabled', 'hidden', 'exposed']) {
						if (msg[field] !== undefined && msg[field] !== null) entry[field] = msg[field];
					}
					broadcast('entity_registry_updated', { action: 'update', entity_id: entry.entity_id });
					ok(msg.id, { entity_entry: entry });
					break;
				}

				case 'config/device_registry/list':
					ok(msg.id, world.devices);
					break;

				case 'config/device_registry/update': {
					const device = world.devices.find((d) => d.id === msg.device_id);
					if (!device) {
						fail(msg.id, 'not_found', `unknown device ${msg.device_id}`);
						break;
					}
					for (const field of ['name', 'area_id', 'disabled', 'manufacturer', 'model']) {
						if (msg[field] !== undefined && msg[field] !== null) device[field] = msg[field];
					}
					ok(msg.id, device);
					break;
				}

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
				/** Live registries, states and the recorded service calls. */
				world,
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
