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
//   - the task registry: jarvis/tasks/{list,get,cancel,delete,clear_finished},
//   - scheduled jobs: jarvis/schedule/{list,add,remove,enabled}, with the
//     `describes` sentence written server-side as the real one writes it
//   - MCP servers: jarvis/mcp/{list,add,remove,reconnect}, including the two
//     refusals the console depends on (no stdio without the file's say-so,
//     and config-authored servers are read-only)
//     with the three jarvis_task_* events every move fires
// and serves a real WAV file at /api/tts_proxy/test.mp3 over HTTP
// (Authorization: Bearer <token> required).
//
// It knows jarvis/tools/list and jarvis/tools/call, because jarvis-core does.
// `jarvis/test/tools_unsupported` makes it forget them again, so the console's
// graceful-degradation path stays covered — a real deployment may be an older
// backend. That fallback used to be the ONLY thing tested here, because
// neither the mock nor the server had ever implemented the command.
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
		pairingCodes: 0, livePairingCodes: new Set(),
		// Flipped by DELETE /api/voice/speaker, so the panel's "FORGET" can be
		// asserted on its effect rather than on the request having been sent.
		voiceprintDeleted: false,
		// One connected and one not: "connected now" is the fact the panel
		// exists to show before somebody revokes the wrong row.
		tokens: [
			{ id: 'tok-console', name: 'console', connected: true, created_at: 1700000000 },
			{ id: 'tok-oldphone', name: 'Old Pixel', connected: false, created_at: 1700000100 }
		]
	};
}

// The enrolment phrases jarvis-core serves, kept here so the console panel has
// a real list to render. Chosen upstream to move pitch and length — see
// `jarvis-core/jarvis/voice/speaker.py` for why a profile built from five
// similar-sounding sentences rejects its own owner.
const PROMPTS = [
	'Good evening, Jarvis. Bring the house up, would you?',
	'What is on my calendar tomorrow morning?',
	'Lock the front door and turn everything off.',
	'One, two, three, four, five, six, seven, eight, nine, ten.',
	'It has been a long day and I would like the lights low, please.'
];

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

/** Mirrors JARVIS_PAIRING_SECRET on a real jarvis-core. */
const PAIRING_SECRET = 'e2e-pairing-secret';

export function startMockHA({ port = 0, token = MOCK_TOKEN, log = () => {} } = {}) {
	const wav = makeWav();
	const world = makeWorld();
	/** @type {Set<{socket: any, id: number, eventType: string|null}>} */
	const subscriptions = new Set();

	/**
	 * The conversation archive, as jarvis-core keeps one.
	 *
	 * Shared across sockets and for the life of the process, which is the
	 * property the chat sidebar depends on: a conversation had on one page load
	 * has to still be listed on the next. Seeded with one so the history list is
	 * never empty on first paint — an empty sidebar and a broken sidebar look
	 * identical, and only one of them is worth failing a test over.
	 * @type {Map<string, {id: string, title: string, created: number, last_active: number, turns: any[]}>}
	 */
	const conversationStore = new Map([
		[
			'conv-mock-earlier',
			{
				id: 'conv-mock-earlier',
				title: 'is the back door shut?',
				created: 1_750_000_000,
				last_active: 1_750_000_100,
				turns: [
					{ role: 'user', content: 'is the back door shut?', timestamp: 1_750_000_000 },
					{
						role: 'assistant',
						content: 'It is, Sir.',
						timestamp: 1_750_000_100,
						thinking: 'binary_sensor.back_door reads off',
						tool_calls: [
							{ name: 'get_state', arguments: { name: 'back door' }, ok: true, status: 'ok' }
						]
					}
				]
			}
		]
	]);

	/** Append one finished exchange, exactly as `ConversationArchive.record` does. */
	function recordTurn(conversationId, userText, assistantText) {
		const now = Math.floor(Date.now() / 1000);
		let stored = conversationStore.get(conversationId);
		if (!stored) {
			stored = {
				id: conversationId,
				title: String(userText || 'New conversation').slice(0, 80),
				created: now,
				last_active: now,
				turns: []
			};
			conversationStore.set(conversationId, stored);
		}
		stored.last_active = now;
		stored.turns.push({ role: 'user', content: String(userText), timestamp: now });
		stored.turns.push({
			role: 'assistant',
			content: String(assistantText),
			timestamp: now,
			thinking: 'the lab strip, then confirm',
			tool_calls: [{ name: 'turn_on', arguments: { name: 'lab lights' }, ok: true, status: 'ok' }]
		});
	}

	/** Summary rows, newest first — no message bodies, as the real one does. */
	function conversationList() {
		return [...conversationStore.values()]
			.sort((a, b) => b.last_active - a.last_active)
			.map((c) => ({
				id: c.id,
				title: c.title || 'New conversation',
				created: c.created,
				last_active: c.last_active,
				turns: c.turns.length,
				preview: String(c.turns[c.turns.length - 1]?.content ?? '').slice(0, 160)
			}));
	}

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

	/**
	 * The task registry, as jarvis-core keeps one.
	 *
	 * Modelled rather than stubbed, because the console's whole progress story
	 * rides on two properties this has to reproduce faithfully:
	 *
	 *   1. `fraction` is `null` — not 0 — whenever a number would be a guess.
	 *      A mock that always sent a number would make the indeterminate bar
	 *      untestable and hide the exact bug it exists to prevent.
	 *   2. Every move fires `jarvis_task_updated`. That is what makes a live bar
	 *      possible without polling, and it is what an e2e test watches.
	 *
	 * @type {Map<string, any>}
	 */
	const taskStore = new Map();
	let taskSeq = 0;

	const TASK_TERMINAL = ['done', 'error', 'cancelled'];

	const taskDict = (task) => {
		const doneSteps = task.steps.filter((s) => TASK_TERMINAL.includes(s.status)).length;
		const finished = TASK_TERMINAL.includes(task.status);
		let fraction = null;
		if (task.status === 'done') fraction = 1;
		else if (task.steps.length && !task.open_ended) fraction = doneSteps / task.steps.length;
		return {
			...task,
			steps: task.steps.map((s) => ({ ...s })),
			fraction,
			done_steps: doneSteps,
			total_steps: task.steps.length,
			finished
		};
	};

	const addTask = (over = {}) => {
		const id = over.id ?? `task-${++taskSeq}`;
		const now = Date.now() / 1000;
		const task = {
			id,
			kind: 'background',
			title: 'A job',
			status: 'queued',
			steps: [],
			detail: '',
			result: '',
			error: '',
			source: '',
			open_ended: false,
			created: now,
			updated: now,
			...over
		};
		taskStore.set(id, task);
		broadcast('jarvis_task_added', { task: taskDict(task) });
		return task;
	};

	const updateTask = (id, changes = {}) => {
		const task = taskStore.get(id);
		if (!task) return null;
		Object.assign(task, changes, { updated: Date.now() / 1000 });
		// jarvis-core closes out every step when a task finishes, so a task
		// never reports `done` above a step still `running`. The console draws
		// what it is sent; a mock that skipped this would let a contradiction
		// through that the real server cannot produce.
		if (task.status === 'done') for (const step of task.steps) step.status = 'done';
		broadcast('jarvis_task_updated', { task: taskDict(task) });
		return task;
	};

	const removeTask = (id) => {
		const task = taskStore.get(id);
		if (!task) return false;
		taskStore.delete(id);
		broadcast('jarvis_task_removed', { task: taskDict(task) });
		return true;
	};

	const taskListing = ({ kind, activeOnly } = {}) =>
		[...taskStore.values()]
			.filter((t) => (kind ? t.kind === kind : true))
			.filter((t) => (activeOnly ? !TASK_TERMINAL.includes(t.status) : true))
			.sort((a, b) => b.created - a.created)
			.map(taskDict);

	/**
	 * The model's own toolbox, as `jarvis/tools/list` answers it.
	 *
	 * A superset of the editable ones: built-ins the console cannot edit are
	 * exactly the rows the union on the Tools page exists to keep. One is
	 * deliberately Tier 3 so the approval path can be tested from here — a
	 * console test-runner that ran a held tool would be the easiest Tier-3
	 * bypass in the product, and that is worth an e2e, not just a unit test.
	 */
	let toolsUnsupported = false;
	let approvalSeq = 0;
	const nativeTools = () => {
		// Built from `world.tools` plus the extras only the MODEL has, never a
		// hardcoded list beside it: `world.tools` already holds `lock_control`
		// and `turn_on`, and a second copy of either produced duplicate keys
		// in the console's `{#each}` — which blanked the whole Test-run
		// control with an `each_key_duplicate` error and nothing on screen to
		// say why. The page is hardened against that now; the fixture should
		// not be generating it either.
		const extras = [
			{
				name: 'get_state',
				description: 'Read one entity.',
				parameters: { type: 'object', properties: { name: { type: 'string' } } },
				tier: 1,
				domain: null
			},
			{
				name: 'code_task',
				description: 'Hand a coding job to the coding agent.',
				parameters: {
					type: 'object',
					properties: { repo: { type: 'string' }, instruction: { type: 'string' } }
				},
				tier: 2,
				domain: null
			}
		];
		const held = new Set(world.tools.map((t) => t.name));
		const rows = [
			...world.tools.map((t) => ({
				name: t.name,
				description: t.description ?? '',
				parameters: t.parameters ?? { type: 'object', properties: {} },
				tier: t.tier ?? 1,
				domain: t.domain ?? null
			})),
			...extras.filter((t) => !held.has(t.name))
		];
		return rows.map((t) => ({
			...t,
			// Mirrors jarvis-core: tier 3 OR a gated domain, computed server-side
			// so the console never re-derives the rule.
			needs_approval: t.tier >= 3 || t.domain === 'lock' || t.domain === 'notify',
			may_escalate: t.name === 'turn_on'
		}));
	};

	/**
	 * Jarvis Code.
	 *
	 * Two repositories, one writable and one not, because the read-only case is
	 * the one the page has to say something about before somebody types an
	 * instruction into it.
	 *
	 * A job here is a real task in `taskStore` with `kind: 'code'`, so the Code
	 * page and the task dock are looking at the same record — which is the
	 * property worth testing, and one a separate job store would quietly break.
	 */
	let codeRepos = [
		{
			name: 'jarvis',
			path: '/srv/jarvis',
			description: 'the assistant itself',
			checks: ['pytest -q', 'ruff check .'],
			writable: true
		},
		{
			name: 'notes',
			path: '/srv/notes',
			description: 'a wiki nobody should be editing by machine',
			checks: [],
			writable: false
		}
	];
	let codeSandboxed = false;
	const codeEnvironments = [
		{
			name: 'python',
			image: 'python:3.12-bookworm',
			network: 'egress',
			memory: '2g',
			cpus: '2',
			env: [],
			setup: []
		},
		{
			name: 'offline',
			image: 'python:3.12-bookworm',
			network: 'none',
			memory: '1g',
			cpus: '1',
			env: [],
			setup: []
		}
	];
	const codeWorkspace = '/srv/jarvis/workspaces';
	/**
	 * Forges, exactly as jarvis-core sends them: the allow-list verbatim and
	 * `has_token` in place of the token, which never leaves the server.
	 */
	const codeForges = [
		{
			name: 'work',
			kind: 'github',
			host: 'github.com',
			has_token: true,
			allow: ['chasesunstrom/jarvis', 'chasesunstrom/*'],
			push: true
		},
		{
			name: 'mirror',
			kind: 'gitlab',
			host: 'gitlab.com',
			has_token: false,
			allow: ['acme/widgets'],
			push: false
		}
	];
	/** One shape, four handlers — a payload that drifts between them is a bug
	 *  the console sees as a field that exists on some responses. */
	const codePayload = () => ({
		repositories: codeRepos,
		jobs: taskListing({ kind: 'code' }),
		sandboxed: codeSandboxed,
		environments: codeEnvironments,
		forges: codeForges,
		can_create: true,
		workspace: codeWorkspace
	});
	/** Mirrors `_RESERVED` in jarvis-core's repos.py. */
	const codeReserved = new Set([
		'con', 'prn', 'aux', 'nul', 'com1', 'lpt1',
		'git', '.git', 'node_modules', '__pycache__', 'venv', '.venv',
		'tmp', 'temp', 'test', 'dist', 'build'
	]);
	const badRepoName = (name) => {
		const text = String(name ?? '').trim();
		if (!text) return 'A repository needs a name.';
		if (text.length > 64) return 'That name is too long — 64 characters at most.';
		if (text !== text.toLowerCase()) return 'Use lowercase.';
		if (!/^[a-z0-9][a-z0-9._-]*$/.test(text)) {
			return 'Use lowercase letters, digits, dot, dash and underscore.';
		}
		if (text.includes('..')) return 'A name may not contain "..".';
		if (codeReserved.has(text)) return `'${text}' is reserved.`;
		return '';
	};
	/** task id -> the finished job's diff, checks and trail. */
	const codeResults = new Map();

	const codeDiff = `diff --git a/src/app.py b/src/app.py
index 1234567..89abcde 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def handle():
-    return 1
+    return 2
`;

	/**
	 * Scheduled jobs.
	 *
	 * `describes` is written HERE rather than by the console, because that is
	 * how the real one works: jarvis-core owns the schedule arithmetic and sends
	 * the sentence, precisely so two surfaces cannot disagree about what
	 * "every day at 07:30" means. A mock that let the console compute it would
	 * hide exactly that.
	 *
	 * @type {Map<string, any>}
	 */
	const scheduled = new Map([
		[
			"brief",
			{
				id: "brief",
				title: "Morning brief",
				kind: "notify",
				when: { mode: "daily", at: "07:30", days: [], minutes: 0 },
				describes: "every day at 07:30",
				payload: { message: "Good morning." },
				enabled: true,
				next_at: Date.now() / 1000 + 3600,
				last_at: 0,
				last_result: "",
				missed: 0,
				created: 1,
				source: "",
				// From the file. The console may look; it may not edit.
				editable: false,
			},
		],
	]);
	let schedSeq = 0;

	const describeWhen = (when) => {
		if (when.mode === "every") {
			return when.minutes % 60 === 0
				? `every ${when.minutes / 60} hour${when.minutes === 60 ? "" : "s"}`
				: `every ${when.minutes} minutes`;
		}
		if (when.mode === "daily") return `every day at ${when.at}`;
		if (when.mode === "weekly") {
			const names = when.days.map((d) => d[0].toUpperCase() + d.slice(1)).join(", ");
			return `${names} at ${when.at}`;
		}
		return `once, at ${when.at}`;
	};

	/**
	 * MCP servers, as jarvis-core keeps them.
	 *
	 * Modelled rather than stubbed because the console's panel turns on two
	 * server-side rules that a stub would quietly satisfy: `allow_stdio` is
	 * read-only over the wire, and a server that came from configuration.yaml
	 * refuses to be edited or removed by a request. Both are refusals, and a
	 * mock that agreed with every request could not test a refusal.
	 *
	 * @type {Map<string, any>}
	 */
	const mcpServers = new Map([
		[
			"house",
			{
				name: "house",
				transport: "http",
				url: "http://127.0.0.1:9100/mcp",
				command: "",
				args: [],
				tier: 2,
				enabled: true,
				// From the file. The console may look and reconnect, never edit.
				editable: false,
				has_token: true,
				connected: true,
				error: "",
				tools: [
					{
						name: "mcp_house_read_note",
						remote_name: "read_note",
						description: "[from the MCP server 'house'] Read one note.",
					},
				],
			},
		],
	]);
	/** Read only from the mock's own "config"; no frame may set it. */
	let mcpAllowStdio = false;

	const mcpListing = () => ({
		servers: [...mcpServers.values()].map((s) => ({ ...s, tool_count: s.tools.length })),
		allow_stdio: mcpAllowStdio,
		default_tier: 2,
	});

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
		// Whose voice Jarvis answers.
		//
		// The shape here is deliberately the REAL one, including what it leaves
		// out: the payload carries counts, scores and timestamps and never the
		// voiceprint vectors, because that is the claim the console panel's
		// e2e case asserts. A mock that helpfully included them would make the
		// test pass on a lie.
		// Adding a sample. Real jarvis-core takes a WAV or raw 16 kHz mono PCM
		// and answers with the profile's new state; it refuses a sample with too
		// little speech in it, and the refusal text is written for a person to
		// act on, so the mock refuses on the same axis rather than always
		// accepting. A mock that accepted anything would let the panel's
		// error-handling rot.
		if (url.pathname === '/api/voice/speaker/enrol' && req.method === 'POST') {
			if (req.headers.authorization !== `Bearer ${token}`) {
				res.writeHead(401, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ detail: 'unauthorized' }));
				return;
			}
			// Callback style, matching the two body readers already in this file:
			// the handler is not async, and making it so to read one request
			// would change how every other route is scheduled.
			let received = 0;
			req.on('data', (chunk) => (received += chunk.length));
			req.on('end', () => {
				// 16-bit samples at 16 kHz: 32000 bytes is one second, so this is
				// a fifth of a second — the "you tapped it" case.
				if (received < 6400) {
					res.writeHead(400, { 'content-type': 'application/json' });
					res.end(
						JSON.stringify({
							detail: 'not enough speech in that sample to enrol from — say the whole phrase'
						})
					);
					return;
				}
				world.enrolledSamples = (world.enrolledSamples ?? 0) + 1;
				world.voiceprintDeleted = false;
				res.writeHead(200, { 'content-type': 'application/json' });
				res.end(
					JSON.stringify({
						enrolled: true,
						samples: 5 + world.enrolledSamples,
						min_samples: 3,
						max_samples: 20,
						prompts: PROMPTS
					})
				);
			});
			return;
		}

		if (url.pathname === '/api/voice/speaker') {
			if (req.headers.authorization !== `Bearer ${token}`) {
				res.writeHead(401);
				res.end('unauthorized');
				return;
			}
			if (req.method === 'DELETE') {
				world.voiceprintDeleted = true;
				res.writeHead(200, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ enrolled: false, samples: 0, mode: 'observe', active: false }));
				return;
			}
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end(
				JSON.stringify(
					world.voiceprintDeleted
						? { enrolled: false, samples: 0, mode: 'observe', active: false,
							min_samples: 3, max_samples: 20, prompts: PROMPTS }
						: {
								enrolled: true,
								samples: 5,
								min_samples: 3,
								// What it takes to MEASURE a threshold rather than
								// inherit one: scoring a sample means rebuilding the
								// profile from the others, and that rebuilt profile
								// needs min_samples itself. So five samples is
								// measurable and three is not — and the console draws
								// two different sentences for the two cases.
								measure_samples: 4,
								max_samples: 20,
								mode: 'observe',
								active: true,
								threshold: 8.831,
								self_score: 2.527,
								worst_self_score: 7.065,
								suggested_threshold: 8.831,
								threshold_measured: true,
								label: 'owner',
								embedder: 'jarvis-mfcc-v1',
								// jarvis-core's own ENROLMENT_PROMPTS, not an abbreviation of
								// them. The console's enrolment panel renders this list
								// rather than carrying a copy, which is the entire argument
								// for letting a browser enrol at all — two surfaces, one
								// list, no drift. Shortening them here would let a panel
								// that DID hard-code its own phrases pass.
								prompts: PROMPTS
							}
				)
			);
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
			let minting = '';
			req.on('data', (chunk) => (minting += chunk));
			req.on('end', () => {
				let parsed = {};
				try {
					parsed = JSON.parse(minting || '{}');
				} catch {
					parsed = {};
				}
				// The second secret. The API token is deliberately not enough:
				// this console's relay hands that token to anything that
				// connects, so a script with transient reach would otherwise
				// mint itself a permanent one.
				if (parsed.secret !== PAIRING_SECRET) {
					res.writeHead(403, { 'content-type': 'application/json' });
					res.end(JSON.stringify({ detail: 'That pairing secret is not correct.' }));
					return;
				}
				const code = `mock-code-${++world.pairingCodes}`;
				world.livePairingCodes.add(code);
				res.writeHead(200, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ code, expires_at: Date.now() / 1000 + 300, ttl: 300 }));
			});
			return;
		}
		if (url.pathname === '/api/pair/claim' && req.method === 'POST') {
			// A browser may not claim: browsers always send Origin on a
			// cross-origin POST and phones never do.
			if (req.headers.origin) {
				res.writeHead(403, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ detail: 'Pairing codes are claimed by the app.' }));
				return;
			}
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

		/**
		 * The intent stage, in the order a real turn produces it: the model
		 * thinks, calls a tool, reads the result, then speaks.
		 *
		 * The reasoning and tool events are here because a chat client renders
		 * them INLINE and in order — a mock that only replayed text deltas would
		 * let a client that put the tool row after the answer pass.
		 */
		async function runIntent(r, text) {
			event(r.id, 'intent-start', { engine: 'conversation' });
			await sleep(10);
			event(r.id, 'intent-thinking', { delta: 'the lab strip, then confirm' });
			await sleep(10);
			event(r.id, 'intent-tool-start', {
				name: 'turn_on',
				arguments: { name: 'lab lights' },
				round: 1,
				index: 0,
				total: 1
			});
			await sleep(15);
			event(r.id, 'intent-tool-end', {
				name: 'turn_on',
				round: 1,
				index: 0,
				total: 1,
				ok: true,
				status: 'ok',
				error: null,
				duration_ms: 15
			});
			for (const delta of DELTAS) {
				await sleep(25);
				event(r.id, 'intent-progress', { chat_log_delta: { content: delta } });
			}
			await sleep(15);
			const conversationId = r.conversationId ?? 'conv-mock-1';
			recordTurn(conversationId, text, RESPONSE);
			event(r.id, 'intent-end', {
				intent_output: {
					conversation_id: conversationId,
					response: { speech: { plain: { speech: RESPONSE } } }
				}
			});
		}

		async function finishRun(r) {
			if (r.done) return;
			r.done = true;
			log(`mock-ha: end-of-audio after ${r.audioBytes} PCM bytes`);
			event(r.id, 'stt-vad-end', {});
			await sleep(20);
			event(r.id, 'stt-end', { stt_output: { text: TRANSCRIPT } });
			await sleep(15);
			await runIntent(r, TRANSCRIPT);
			await sleep(10);
			event(r.id, 'tts-start', { engine: 'tts.piper' });
			await sleep(20);
			event(r.id, 'tts-end', { tts_output: { url: TTS_PATH, mime_type: 'audio/wav' } });
			await sleep(10);
			event(r.id, 'run-end', {});
			run = null;
		}

		/** A typed turn: no audio, and `end_stage` decides whether it speaks. */
		async function runText(r, text, speaks) {
			if (r.done) return;
			r.done = true;
			await sleep(10);
			await runIntent(r, text);
			if (speaks) {
				await sleep(10);
				event(r.id, 'tts-start', { engine: 'tts.piper' });
				await sleep(20);
				event(r.id, 'tts-end', { tts_output: { url: TTS_PATH, mime_type: 'audio/wav' } });
			}
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
						// Whether the turn that asked had already read somebody
						// else's words. The console renders the question
						// verbatim, so this is what lets a human tell a real
						// question from one an injected page wrote.
						tainted: Boolean(msg.tainted),
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

				// --- scheduled jobs ------------------------------------------
				case 'jarvis/schedule/list':
					ok(msg.id, { jobs: [...scheduled.values()] });
					break;

				case 'jarvis/schedule/add': {
					const when = msg.when || {};
					if (!when.mode) {
						fail(msg.id, 'invalid_format', 'that is not a schedule I can read');
						break;
					}
					if (when.mode === 'once' && Date.parse(when.at) < Date.now()) {
						fail(msg.id, 'invalid_format', 'that time has already passed');
						break;
					}
					if (msg.kind === 'service' && !String(msg.service || '').includes('.')) {
						fail(msg.id, 'invalid_format', 'a service looks like light.turn_on');
						break;
					}
					if (msg.kind === 'code' && !(msg.repo && msg.instruction)) {
						fail(msg.id, 'invalid_format', 'a coding job needs a repository and an instruction');
						break;
					}
					const id = `job-${++schedSeq}`;
					const job = {
						id,
						title:
							String(msg.title || '') ||
							String(
								msg.message ||
									msg.question ||
									msg.service ||
									(msg.repo ? `${msg.repo}: ${msg.instruction}` : '') ||
									'scheduled job'
							),
						kind: String(msg.kind || 'notify'),
						when: {
							mode: when.mode,
							at: String(when.at || ''),
							days: when.days || [],
							minutes: Number(when.minutes || 0),
						},
						describes: describeWhen(when),
						payload: {},
						enabled: true,
						next_at: Date.now() / 1000 + 600,
						last_at: 0,
						last_result: '',
						missed: 0,
						created: Date.now() / 1000,
						source: 'console',
						editable: true,
					};
					scheduled.set(id, job);
					ok(msg.id, { status: 'ok', job });
					break;
				}

				case 'jarvis/schedule/remove': {
					const job = scheduled.get(String(msg.job_id || ''));
					if (!job) {
						fail(msg.id, 'not_found', `no scheduled job '${msg.job_id}'`);
						break;
					}
					if (!job.editable) {
						fail(
							msg.id,
							'not_found',
							`'${job.id}' comes from configuration.yaml; remove it there`
						);
						break;
					}
					scheduled.delete(job.id);
					ok(msg.id, { status: 'ok', removed: job.id });
					break;
				}

				case 'jarvis/schedule/enabled': {
					const job = scheduled.get(String(msg.job_id || ''));
					if (!job) {
						fail(msg.id, 'not_found', `no scheduled job '${msg.job_id}'`);
						break;
					}
					job.enabled = Boolean(msg.enabled);
					job.next_at = job.enabled ? Date.now() / 1000 + 600 : null;
					ok(msg.id, { status: 'ok', job });
					break;
				}

				/**
				 * Forget every console-added job, keeping the config-authored one.
				 *
				 * The suite shares one mock process, so a test that asserts "no job
				 * matches" is only meaningful if it can get back to that state
				 * deliberately rather than by running first.
				 */
				case 'jarvis/test/schedule_reset': {
					for (const [id, job] of [...scheduled.entries()]) {
						if (job.editable) scheduled.delete(id);
					}
					scheduled.get('brief').missed = 0;
					scheduled.get('brief').last_result = '';
					ok(msg.id, { jobs: [...scheduled.values()] });
					break;
				}

				/** Make a job look like it missed a firing while Jarvis was off. */
				case 'jarvis/test/schedule_missed': {
					const job = scheduled.get(String(msg.job_id || ''));
					if (job) {
						job.missed = Number(msg.missed || 3);
						job.last_result = String(msg.reason || 'missed while Jarvis was not running');
					}
					ok(msg.id, { jobs: [...scheduled.values()] });
					break;
				}

				// --- MCP -----------------------------------------------------
				case 'jarvis/mcp/list':
					ok(msg.id, mcpListing());
					break;

				case 'jarvis/mcp/add': {
					const name = String(msg.name || '')
						.trim()
						.toLowerCase()
						.replace(/[^a-z0-9_]+/g, '_')
						.replace(/^_+|_+$/g, '');
					if (!name) {
						fail(msg.id, 'invalid_format', 'an MCP server needs a name');
						break;
					}
					const held = mcpServers.get(name);
					if (held && !held.editable) {
						fail(
							msg.id,
							'invalid_format',
							`'${name}' is defined in configuration.yaml; edit it there`
						);
						break;
					}
					const stdio = msg.transport === 'stdio' || (!msg.url && msg.command);
					// The refusal the whole panel is arranged around. Note that
					// `msg.allow_stdio` is not consulted: a frame cannot turn it on.
					if (stdio && !mcpAllowStdio) {
						fail(
							msg.id,
							'invalid_format',
							'a stdio server starts a program on the Jarvis host. Set `mcp: allow_stdio: true` in configuration.yaml first'
						);
						break;
					}
					if (!stdio && !msg.url) {
						fail(msg.id, 'invalid_format', 'an http MCP server needs a url');
						break;
					}
					const tier = Math.min(3, Math.max(1, Number(msg.tier) || 2));
					mcpServers.set(name, {
						name,
						transport: stdio ? 'stdio' : 'http',
						url: String(msg.url || ''),
						command: String(msg.command || ''),
						args: Array.isArray(msg.args) ? msg.args.map(String) : [],
						tier,
						enabled: true,
						editable: true,
						has_token: Boolean(msg.token),
						connected: true,
						error: '',
						tools: [
							{
								name: `mcp_${name}_search`,
								remote_name: 'search',
								description: `[from the MCP server '${name}'] Search it.`,
							},
						],
					});
					ok(msg.id, { status: 'ok', name, connected: true, ...mcpListing() });
					break;
				}

				case 'jarvis/mcp/remove': {
					const target = mcpServers.get(String(msg.name || ''));
					if (!target) {
						fail(msg.id, 'not_found', `no MCP server called '${msg.name}'`);
						break;
					}
					if (!target.editable) {
						fail(
							msg.id,
							'not_found',
							`'${target.name}' comes from configuration.yaml; remove it there`
						);
						break;
					}
					mcpServers.delete(target.name);
					ok(msg.id, { status: 'ok', removed: target.name, ...mcpListing() });
					break;
				}

				case 'jarvis/mcp/reconnect': {
					const one = msg.name ? mcpServers.get(String(msg.name)) : null;
					if (msg.name && !one) {
						fail(msg.id, 'not_found', `no MCP server called '${msg.name}'`);
						break;
					}
					for (const s of one ? [one] : mcpServers.values()) {
						s.connected = true;
						s.error = '';
					}
					ok(msg.id, { reconnected: msg.name || 'all', ...mcpListing() });
					break;
				}

				/** Let a test say what an operator put in configuration.yaml. */
				case 'jarvis/test/mcp_allow_stdio':
					mcpAllowStdio = Boolean(msg.allow);
					ok(msg.id, { allow_stdio: mcpAllowStdio });
					break;

				/** And make one look like it has fallen over. */
				case 'jarvis/test/mcp_break': {
					const target = mcpServers.get(String(msg.name || ''));
					if (target) {
						target.connected = false;
						target.error = String(msg.error || 'no route to host');
						target.tools = [];
					}
					ok(msg.id, mcpListing());
					break;
				}

				// --- Jarvis Code ---------------------------------------------
				case 'jarvis/code/list':
					ok(msg.id, codePayload());
					break;

				case 'jarvis/code/start': {
					const repo = codeRepos.find((r) => r.name === String(msg.repo ?? ''));
					if (!repo) {
						fail(msg.id, 'invalid_format', `there is no repository called ${msg.repo}`);
						break;
					}
					const instruction = String(msg.instruction ?? '').trim();
					if (!instruction) {
						fail(msg.id, 'invalid_format', 'I need to know what to change.');
						break;
					}
					const plan = ['read the handler', 'change it', 'run the checks'];
					const task = addTask({
						kind: 'code',
						title: `${repo.name}: ${instruction}`,
						// The real integration starts with one step and grows the
						// plan into it, which is what makes the bar honest. The
						// mock does the same so the console is drawing the same
						// shape it will see in production.
						steps: [{ title: 'plan the work', status: 'queued', detail: '' }],
						open_ended: true,
						detail: 'planning'
					});
					ok(msg.id, { task_id: task.id, title: task.title, task: taskDict(task) });

					if (msg.hold) break;
					const tick = Number(msg.tick_ms) || 100;
					setTimeout(() => {
						const live = taskStore.get(task.id);
						if (!live || TASK_TERMINAL.includes(live.status)) return;
						live.steps[0].status = 'done';
						for (const title of [...plan, 'write it up']) {
							live.steps.push({ title, status: 'queued', detail: '' });
						}
						updateTask(task.id, { status: 'running', open_ended: false, detail: 'working' });

						let at = 1;
						const advance = () => {
							const now = taskStore.get(task.id);
							if (!now || TASK_TERMINAL.includes(now.status)) return;
							if (at > 1) now.steps[at - 1].status = 'done';
							if (at >= now.steps.length) {
								codeResults.set(task.id, {
									repo: repo.name,
									instruction,
									branch: `jarvis/20260101-${task.id}`,
									plan,
									files_changed: repo.writable ? ['src/app.py'] : [],
									diff_stat: ' src/app.py | 2 +-',
									diff: repo.writable ? codeDiff : '',
									checks: repo.checks.map((command, i) => ({
										command,
										ok: i === 0,
										output: i === 0 ? 'ok' : 'one failure'
									})),
									trail: [
										{ tool: 'read_file', args: 'path=src/app.py', outcome: 'read 2 lines' },
										{ tool: 'edit_file', args: 'path=src/app.py', outcome: 'edited' }
									],
									summary: 'changed the handler to return 2',
									rounds: 3
								});
								updateTask(task.id, {
									status: 'done',
									detail: `jarvis/20260101-${task.id}`,
									result: `jarvis/20260101-${task.id} · 1 file changed · 1/2 checks passed`
								});
								return;
							}
							now.steps[at].status = 'running';
							updateTask(task.id, {});
							at += 1;
							setTimeout(advance, tick);
						};
						setTimeout(advance, tick);
					}, tick);
					break;
				}

				case 'jarvis/code/create_repo': {
					const problem = badRepoName(msg.name);
					if (problem) {
						fail(msg.id, 'invalid_format', problem);
						break;
					}
					const wanted = String(msg.name).trim();
					if (codeRepos.some((r) => r.name === wanted)) {
						fail(msg.id, 'invalid_format', `There is already a repository called '${wanted}'.`);
						break;
					}
					const environment = String(msg.environment || '');
					if (environment && !codeEnvironments.some((e) => e.name === environment)) {
						fail(msg.id, 'invalid_format', `There is no environment called '${environment}'.`);
						break;
					}
					const made = {
						name: wanted,
						path: `${codeWorkspace}/${wanted}`,
						description: String(msg.description || ''),
						checks: [],
						writable: true,
						managed: true,
						environment,
						environment_detail: environment
							? codeEnvironments.find((e) => e.name === environment).image
							: '',
						networked: environment
							? codeEnvironments.find((e) => e.name === environment).network === 'egress'
							: false
					};
					codeRepos.push(made);
					ok(msg.id, { repository: made, ...codePayload() });
					break;
				}

				case 'jarvis/code/clone_repo': {
					const forge = codeForges.find((f) => f.name === String(msg.forge || ''));
					if (!forge) {
						fail(msg.id, 'invalid_format', `There is no forge called '${msg.forge}'.`);
						break;
					}
					const project = String(msg.project || '').replace(/^\/+|\/+$/g, '');
					const wantedParts = project.toLowerCase().split('/').filter(Boolean);
					const permitted =
						wantedParts.length >= 2 &&
						forge.allow.some((pattern) => {
							const segments = pattern.toLowerCase().split('/');
							if (segments[segments.length - 1] === '*') {
								const head = segments.slice(0, -1);
								return (
									wantedParts.length > head.length &&
									head.every((segment, i) => wantedParts[i] === segment)
								);
							}
							return (
								segments.length === wantedParts.length &&
								segments.every((segment, i) => wantedParts[i] === segment)
							);
						});
					if (!permitted) {
						fail(
							msg.id,
							'invalid_format',
							`${project} is not on ${forge.name}'s allow-list.`
						);
						break;
					}
					const local = String(msg.name || '').trim() || project.split('/').pop().toLowerCase();
					const problem = badRepoName(local);
					if (problem) {
						fail(msg.id, 'invalid_format', problem);
						break;
					}
					if (codeRepos.some((r) => r.name === local)) {
						fail(msg.id, 'invalid_format', `There is already a repository called '${local}'.`);
						break;
					}
					const cloneEnvironment = String(msg.environment || '');
					const found = codeEnvironments.find((e) => e.name === cloneEnvironment);
					if (cloneEnvironment && !found) {
						fail(msg.id, 'invalid_format', `There is no environment called '${cloneEnvironment}'.`);
						break;
					}
					const cloned = {
						name: local,
						path: `${codeWorkspace}/${local}`,
						description: `${forge.kind}:${project}`,
						checks: [],
						writable: true,
						managed: true,
						origin: `https://${forge.host}/${project}.git`,
						environment: cloneEnvironment,
						environment_detail: found ? found.image : '',
						networked: found ? found.network === 'egress' : false
					};
					codeRepos.push(cloned);
					ok(msg.id, { repository: cloned, ...codePayload() });
					break;
				}

				case 'jarvis/code/forget_repo': {
					const index = codeRepos.findIndex((r) => r.name === String(msg.name || ''));
					if (index < 0) {
						fail(msg.id, 'not_found', `There is no repository called '${msg.name}'.`);
						break;
					}
					const [dropped] = codeRepos.splice(index, 1);
					ok(msg.id, {
						forgotten: dropped.name,
						note: `Forgot ${dropped.name}. The files are still at ${dropped.path}.`,
						...codePayload()
					});
					break;
				}

				case 'jarvis/code/result': {
					const found = codeResults.get(String(msg.task_id ?? ''));
					if (!found) {
						fail(msg.id, 'not_found', 'no finished coding job with that id');
						break;
					}
					ok(msg.id, found);
					break;
				}

				// Put the mock back to a known state, and let a test see the
				// sandboxed wording without a second mock process.
				case 'jarvis/test/code_reset': {
					codeResults.clear();
					codeRepos = codeRepos.filter((r) => !r.managed);
					codeSandboxed = Boolean(msg.sandboxed);
					for (const task of [...taskStore.values()]) {
						if (task.kind === 'code') removeTask(task.id);
					}
					ok(msg.id, { sandboxed: codeSandboxed });
					break;
				}

				// --- tasks -------------------------------------------------
				case 'jarvis/tasks/list':
					ok(msg.id, {
						tasks: taskListing({ kind: msg.kind || null, activeOnly: Boolean(msg.active) })
					});
					break;

				case 'jarvis/tasks/get': {
					const task = taskStore.get(String(msg.task_id ?? ''));
					if (!task) {
						fail(msg.id, 'not_found', `no task ${msg.task_id}`);
						break;
					}
					ok(msg.id, { task: taskDict(task) });
					break;
				}

				case 'jarvis/tasks/cancel': {
					const task = taskStore.get(String(msg.task_id ?? ''));
					if (!task) {
						fail(msg.id, 'not_found', `no task ${msg.task_id}`);
						break;
					}
					if (TASK_TERMINAL.includes(task.status)) {
						ok(msg.id, { task: taskDict(task), cancelled: false, reason: 'already finished' });
						break;
					}
					updateTask(task.id, { status: 'cancelled', detail: 'cancelled from a client' });
					ok(msg.id, {
						task: taskDict(taskStore.get(task.id)),
						cancelled: true,
						note: 'marked cancelled; a worker that does not check for this may still be running'
					});
					break;
				}

				case 'jarvis/tasks/delete': {
					if (!removeTask(String(msg.task_id ?? ''))) {
						fail(msg.id, 'not_found', `no task ${msg.task_id}`);
						break;
					}
					ok(msg.id, { removed: msg.task_id });
					break;
				}

				case 'jarvis/tasks/clear_finished': {
					const gone = [...taskStore.values()].filter((t) => TASK_TERMINAL.includes(t.status));
					for (const task of gone) removeTask(task.id);
					ok(msg.id, { removed: gone.length });
					break;
				}

				// Drive a task through its steps on demand, the way
				// `jarvis/test/tool_run` drives a round of tool calls: the
				// console's job is to draw whatever the registry reports, and a
				// test should not have to wait for a real research run to
				// decide to report something.
				// Empty the registry. The e2e suite shares one mock process, so
				// "there is nothing running" is only a testable claim if a test
				// can get back to that state without depending on how long the
				// previous test's job happened to take.
				case 'jarvis/test/task_reset': {
					const ids = [...taskStore.keys()];
					for (const id of ids) removeTask(id);
					ok(msg.id, { removed: ids.length });
					break;
				}

				case 'jarvis/test/task_run': {
					const task = addTask({
						kind: msg.kind || 'research',
						title: msg.title || 'Read twelve pages',
						steps: (msg.steps || ['search', 'read', 'write up']).map((title) => ({
							title,
							status: 'queued',
							detail: ''
						})),
						open_ended: Boolean(msg.open_ended)
					});
					ok(msg.id, { task_id: task.id });
					if (msg.hold) break; // leave it queued for the test to act on

					// `fail_at` is the step index that fails; `fail: true` without
					// one fails on the SECOND step rather than the last. A task
					// that failed on its final step has attempted every step, so
					// `done_steps / total_steps` is 1.0 and its bar reads as
					// complete — true, and useless for testing that a failure
					// keeps the ground it covered.
					const tick = Number(msg.tick_ms) || 120;
					const failAt = Number.isInteger(msg.fail_at) ? msg.fail_at : msg.fail ? 1 : -1;
					updateTask(task.id, { status: 'running' });

					let at = 0;
					const advance = () => {
						const live = taskStore.get(task.id);
						// A cancel or a delete from the console stops the reporting.
						// A mock that ploughed on would make CANCEL look broken.
						if (!live || TASK_TERMINAL.includes(live.status)) return;
						if (at > 0) live.steps[at - 1].status = 'done';
						if (at >= live.steps.length) {
							updateTask(task.id, { status: 'done', result: 'all twelve read' });
							return;
						}
						if (at === failAt) {
							live.steps[at].status = 'error';
							updateTask(task.id, { status: 'error', error: 'the model server refused' });
							return;
						}
						live.steps[at].status = 'running';
						updateTask(task.id, {});
						at += 1;
						setTimeout(advance, tick);
					};
					setTimeout(advance, tick);
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

				case 'config/token/list':
					ok(msg.id, world.tokens);
					break;

				case 'config/token/revoke': {
					const at = world.tokens.findIndex((t) => t.id === msg.token_id);
					if (at < 0) {
						fail(msg.id, 'not_found', `unknown token ${msg.token_id}`);
						break;
					}
					const [gone] = world.tokens.splice(at, 1);
					// A revoked token's live socket is hung up too, or "revoked"
					// would mean "revoked at the next reconnect".
					ok(msg.id, {
						id: gone.id,
						revoked: true,
						sockets_closed: gone.connected ? 1 : 0
					});
					break;
				}

				case 'config/companion/list':
					ok(msg.id, world.companions);
					break;

				// --- the model's own toolbox ---------------------------------
				//
				// jarvis-core implements these. The mock can be told to FORGET
				// them (`jarvis/test/tools_unsupported`) so the console's
				// graceful-degradation path stays covered too — a real
				// deployment may be an older backend, and that fallback is the
				// only thing this suite used to exercise, because neither the
				// mock nor the server had ever implemented the command.
				case 'jarvis/tools/list': {
					if (toolsUnsupported) {
						fail(msg.id, 'unknown_command', `unhandled: ${msg.type}`);
						break;
					}
					ok(msg.id, { tools: nativeTools(), count: nativeTools().length });
					break;
				}

				case 'jarvis/tools/call': {
					if (toolsUnsupported) {
						fail(msg.id, 'unknown_command', `unhandled: ${msg.type}`);
						break;
					}
					const wanted = String(msg.name ?? '');
					const tool = nativeTools().find((t) => t.name === wanted);
					if (!tool) {
						fail(msg.id, 'not_found', `unknown tool '${wanted}'`);
						break;
					}
					if (tool.needs_approval) {
						// What the real registry answers: held, with a request
						// id, and a card raised. NOT run.
						const requestId = `req-${++approvalSeq}`;
						broadcast('jarvis_approval_required', {
							request_id: requestId,
							tool: tool.name,
							arguments: msg.arguments ?? {},
							tier: tool.tier
						});
						ok(msg.id, {
							tool: tool.name,
							result: {
								status: 'approval_required',
								request_id: requestId,
								tool: tool.name
							}
						});
						break;
					}
					ok(msg.id, {
						tool: tool.name,
						result: { status: 'ok', tool: tool.name, arguments: msg.arguments ?? {} }
					});
					break;
				}

				case 'jarvis/test/tools_unsupported':
					toolsUnsupported = Boolean(msg.unsupported);
					ok(msg.id, { unsupported: toolsUnsupported });
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
					const typed = msg.input?.text;
					run = {
						id: msg.id,
						handlerId: 1,
						audioBytes: 0,
						done: false,
						conversationId: msg.conversation_id || 'conv-mock-1'
					};
					socket.send(JSON.stringify({ id: msg.id, type: 'result', success: true, result: null }));
					event(msg.id, 'run-start', {
						pipeline: msg.pipeline ?? 'pipe-other',
						runner_data: { stt_binary_handler_id: run.handlerId, timeout: 300 }
					});
					if (typed) {
						// A text run: no stt stage at all, and nothing to wait for
						// — there is no end-of-audio frame coming.
						void runText(run, String(typed), msg.end_stage !== 'intent');
						break;
					}
					event(msg.id, 'stt-start', {
						engine: 'stt.whisper',
						metadata: { sample_rate: msg.input?.sample_rate ?? 16000 }
					});
					break;
				}
				case 'jarvis/conversation/list':
					socket.send(
						JSON.stringify({
							id: msg.id,
							type: 'result',
							success: true,
							result: { conversations: conversationList() }
						})
					);
					break;
				case 'jarvis/conversation/get': {
					const stored = conversationStore.get(msg.conversation_id);
					if (!stored) {
						socket.send(
							JSON.stringify({
								id: msg.id,
								type: 'result',
								success: false,
								error: { code: 'not_found', message: 'no such conversation' }
							})
						);
						break;
					}
					socket.send(
						JSON.stringify({
							id: msg.id,
							type: 'result',
							success: true,
							result: { conversation: stored }
						})
					);
					break;
				}
				case 'jarvis/conversation/delete': {
					const gone = conversationStore.delete(msg.conversation_id);
					socket.send(
						JSON.stringify({
							id: msg.id,
							type: 'result',
							success: true,
							result: { deleted: gone }
						})
					);
					break;
				}
				case 'jarvis/conversation/rename': {
					const stored = conversationStore.get(msg.conversation_id);
					if (stored) stored.title = String(msg.title ?? '');
					socket.send(
						JSON.stringify({
							id: msg.id,
							type: 'result',
							success: true,
							result: { renamed: Boolean(stored) }
						})
					);
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
