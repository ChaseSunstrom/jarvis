import { describe, it, expect, vi, afterEach } from 'vitest';
import {
	DEFAULT_COMMAND_TIMEOUT_MS,
	JarvisClient,
	JarvisCommandError,
	UnsupportedCommandError,
	applyStateChanged,
	areaForEntity,
	areaKey,
	domainOf,
	friendlyName,
	isOn,
	isUnsupported,
	splitEntityId,
	toolsFromServices,
	type EntityState
} from './jarvisClient';

afterEach(() => {
	vi.useRealTimers();
});

function setup(opts: ConstructorParameters<typeof JarvisClient>[1] = {}) {
	const sent: any[] = [];
	const client = new JarvisClient((data) => sent.push(JSON.parse(data)), opts);
	const reply = (id: number, result: any = null) =>
		client.handleMessage(JSON.stringify({ id, type: 'result', success: true, result }));
	const fail = (id: number, code: string, message = 'nope') =>
		client.handleMessage(
			JSON.stringify({ id, type: 'result', success: false, error: { code, message } })
		);
	return { sent, client, reply, fail };
}

describe('message framing and ids', () => {
	it('numbers commands from 1 and merges the payload', async () => {
		const { sent, client, reply } = setup();
		const first = client.getStates();
		const second = client.callService('light', 'turn_on', { entity_id: 'light.a' });

		expect(sent).toEqual([
			{ id: 1, type: 'get_states' },
			{
				id: 2,
				type: 'call_service',
				domain: 'light',
				service: 'turn_on',
				service_data: { entity_id: 'light.a' }
			}
		]);

		reply(1, [{ entity_id: 'light.a', state: 'on', attributes: {} }]);
		reply(2, { context: {} });
		await expect(first).resolves.toHaveLength(1);
		await expect(second).resolves.toEqual({ context: {} });
		expect(client.pendingIds).toEqual([]);
	});

	it('resolves results out of order and ignores unknown ids', async () => {
		const { client, reply } = setup();
		const a = client.getStates();
		const b = client.getServices();
		reply(2, { light: {} });
		reply(99, 'stray');
		reply(1, []);
		await expect(b).resolves.toEqual({ light: {} });
		await expect(a).resolves.toEqual([]);
	});

	it('only sets target / return_response when asked', () => {
		const { sent, client } = setup();
		client.callService('light', 'turn_on', {}, { target: { area_id: 'lab' }, returnResponse: true });
		expect(sent[0]).toEqual({
			id: 1,
			type: 'call_service',
			domain: 'light',
			service: 'turn_on',
			service_data: {},
			target: { area_id: 'lab' },
			return_response: true
		});
	});

	it('callEntityService derives the domain from the entity_id', () => {
		const { sent, client } = setup();
		client.callEntityService('cover.blind', 'set_cover_position', { position: 40 });
		expect(sent[0]).toMatchObject({
			type: 'call_service',
			domain: 'cover',
			service: 'set_cover_position',
			service_data: { entity_id: 'cover.blind', position: 40 }
		});
	});

	it('rejects with the backend code, and unknown_command as UnsupportedCommandError', async () => {
		const { client, fail } = setup();
		const bad = client.callService('light', 'nope');
		fail(1, 'service_not_found', 'no such service');
		await expect(bad).rejects.toBeInstanceOf(JarvisCommandError);

		const missing = client.listAreas();
		fail(2, 'unknown_command', "unknown command 'config/area_registry/list'");
		const err = await missing.catch((e) => e);
		expect(isUnsupported(err)).toBe(true);
		expect((err as UnsupportedCommandError).command).toBe('config/area_registry/list');
	});

	it('answers a ping with the pong frame', async () => {
		const { client } = setup();
		const pong = client.ping();
		client.handleMessage(JSON.stringify({ id: 1, type: 'pong' }));
		await expect(pong).resolves.toBeNull();
	});

	it('survives garbage frames', () => {
		const { client } = setup();
		expect(() => client.handleMessage('not json')).not.toThrow();
		expect(() => client.handleMessage('null')).not.toThrow();
		expect(() => client.handleMessage({ type: 'result' })).not.toThrow();
	});

	it('reports unhandled frames to the caller', () => {
		const onUnhandled = vi.fn();
		const client = new JarvisClient(() => {}, { onUnhandled });
		client.handleMessage(JSON.stringify({ type: 'auth_ok' }));
		expect(onUnhandled).toHaveBeenCalledWith({ type: 'auth_ok' });
	});

	it('rejects everything in flight when the socket closes', async () => {
		const { client } = setup();
		const pending = client.getStates();
		client.handleClose('gone');
		await expect(pending).rejects.toThrow('gone');
		expect(client.pendingIds).toEqual([]);
	});

	// Regression: `{ id, ...payload }` let a caller-supplied `id` key overwrite
	// the command id. The frame then carried the wrong id, no result could ever
	// match the pending entry, and the promise hung forever.
	it('a payload key named id cannot overwrite the command id', async () => {
		const { sent, client, reply } = setup();
		const promise = client.updateEntity('light.a', { id: 999, name: 'Lamp' } as any);
		expect(sent[0].id).toBe(1);
		expect(client.pendingIds).toEqual([1]);
		reply(1, { entity_entry: {} });
		await expect(promise).resolves.toEqual({ entity_entry: {} });
	});

	it('rejects the caller when the transport refuses to send', async () => {
		const client = new JarvisClient(() => {
			throw new Error('websocket is not open');
		});
		await expect(client.getStates()).rejects.toThrow('websocket is not open');
		expect(client.pendingIds).toEqual([]);
	});

	// Regression: the /ws relay finishes the browser upgrade before it has dialled
	// the backend, so with the backend down every command is accepted and then
	// buffered forever. Without a timeout the page sits on "Connecting…" with no
	// error and any `busy` flag stays stuck true.
	it('times out a command the backend never answers', async () => {
		vi.useFakeTimers();
		const { client } = setup({ timeoutMs: 1000 });
		const promise = client.getStates();
		const settled = promise.catch((e) => e);
		vi.advanceTimersByTime(1001);
		const err = await settled;
		expect(err).toBeInstanceOf(JarvisCommandError);
		expect((err as JarvisCommandError).code).toBe('timeout');
		expect(client.pendingIds).toEqual([]);
	});

	it('cancels the timeout once a result arrives', async () => {
		vi.useFakeTimers();
		const { client, reply } = setup({ timeoutMs: 1000 });
		const promise = client.getStates();
		reply(1, []);
		await expect(promise).resolves.toEqual([]);
		vi.advanceTimersByTime(5000);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('has a timeout by default and honours 0 as "never"', async () => {
		expect(DEFAULT_COMMAND_TIMEOUT_MS).toBeGreaterThan(0);
		vi.useFakeTimers();
		const { client } = setup({ timeoutMs: 0 });
		const promise = client.getStates();
		vi.advanceTimersByTime(10 * 60 * 1000);
		expect(client.pendingIds).toEqual([1]);
		client.handleClose();
		await expect(promise).rejects.toBeInstanceOf(JarvisCommandError);
	});

	it('surfaces a result for an unknown id instead of dropping it', () => {
		const onUnhandled = vi.fn();
		const client = new JarvisClient(() => {}, { onUnhandled });
		const stray = { id: null, type: 'result', success: false, error: { code: 'invalid_format' } };
		client.handleMessage(JSON.stringify(stray));
		expect(onUnhandled).toHaveBeenCalledWith(stray);
	});
});

describe('subscription bookkeeping', () => {
	it('keys the subscription on the command id and routes events to it', async () => {
		const { sent, client, reply } = setup();
		const seen: any[] = [];
		const promise = client.subscribeEvents((e) => seen.push(e), 'state_changed');
		expect(sent[0]).toEqual({ id: 1, type: 'subscribe_events', event_type: 'state_changed' });
		reply(1);
		const sub = await promise;
		expect(sub.id).toBe(1);
		expect(client.subscriptionIds).toEqual([1]);

		client.handleMessage(
			JSON.stringify({ id: 1, type: 'event', event: { event_type: 'state_changed', data: { a: 1 } } })
		);
		client.handleMessage(JSON.stringify({ id: 7, type: 'event', event: { event_type: 'other' } }));
		expect(seen).toEqual([{ event_type: 'state_changed', data: { a: 1 } }]);
	});

	it('registers the callback before the result arrives so early events are not lost', async () => {
		const { client, reply } = setup();
		const seen: any[] = [];
		const promise = client.subscribeEvents((e) => seen.push(e));
		client.handleMessage(
			JSON.stringify({ id: 1, type: 'event', event: { event_type: 'early', data: {} } })
		);
		reply(1);
		await promise;
		expect(seen.map((e) => e.event_type)).toEqual(['early']);
	});

	it('omits event_type when subscribing to everything', async () => {
		const { sent, client, reply } = setup();
		const promise = client.subscribeEvents(() => {});
		expect(sent[0]).toEqual({ id: 1, type: 'subscribe_events' });
		reply(1);
		await promise;
	});

	it('drops the callback when the subscribe command fails', async () => {
		const { client, fail } = setup();
		const promise = client.subscribeEvents(() => {});
		fail(1, 'unknown_command');
		await expect(promise).rejects.toBeInstanceOf(UnsupportedCommandError);
		expect(client.subscriptionIds).toEqual([]);
	});

	it('unsubscribe sends the id once and is idempotent', async () => {
		const { sent, client, reply } = setup();
		const promise = client.subscribeEvents(() => {});
		reply(1);
		const sub = await promise;

		const first = sub.unsubscribe();
		expect(sent[1]).toEqual({ id: 2, type: 'unsubscribe_events', subscription: 1 });
		reply(2);
		await first;
		expect(client.subscriptionIds).toEqual([]);

		await sub.unsubscribe();
		expect(sent).toHaveLength(2);
	});

	it('unsubscribe survives a backend that rejects it', async () => {
		const { client, reply, fail } = setup();
		const promise = client.subscribeEvents(() => {});
		reply(1);
		const sub = await promise;
		const done = sub.unsubscribe();
		fail(2, 'not_found');
		await expect(done).resolves.toBeUndefined();
	});

	it('keeps several subscriptions apart', async () => {
		const { client, reply } = setup();
		const a: any[] = [];
		const b: any[] = [];
		const subA = client.subscribeEvents((e) => a.push(e), 'state_changed');
		reply(1);
		await subA;
		const subB = client.subscribeEvents((e) => b.push(e), 'call_service');
		reply(2);
		await subB;
		expect(client.subscriptionIds).toEqual([1, 2]);

		client.handleMessage({ id: 2, type: 'event', event: { event_type: 'call_service', data: {} } });
		expect(a).toHaveLength(0);
		expect(b).toHaveLength(1);
	});

	it('clears subscriptions when the socket closes', async () => {
		const { client, reply } = setup();
		const promise = client.subscribeEvents(() => {});
		reply(1);
		await promise;
		client.handleClose();
		expect(client.subscriptionIds).toEqual([]);
	});
});

describe('registry commands', () => {
	it('sends the registry payloads jarvis-core expects', () => {
		const { sent, client } = setup();
		client.createArea('Lab', ['workshop']);
		client.updateArea('lab', { name: 'Laboratory' });
		client.deleteArea('lab');
		client.updateEntity('light.a', { area_id: '', exposed: false });
		client.updateDevice('dev1', { area_id: 'lab' });

		expect(sent).toEqual([
			{ id: 1, type: 'config/area_registry/create', name: 'Lab', aliases: ['workshop'] },
			{ id: 2, type: 'config/area_registry/update', area_id: 'lab', name: 'Laboratory' },
			{ id: 3, type: 'config/area_registry/delete', area_id: 'lab' },
			{
				id: 4,
				type: 'config/entity_registry/update',
				entity_id: 'light.a',
				area_id: '',
				exposed: false
			},
			{ id: 5, type: 'config/device_registry/update', device_id: 'dev1', area_id: 'lab' }
		]);
	});
});

describe('tools', () => {
	it('falls back to the service catalogue when jarvis/tools/list is unknown', async () => {
		const { sent, client, reply, fail } = setup();
		const promise = client.listTools();
		expect(sent[0]).toEqual({ id: 1, type: 'jarvis/tools/list' });
		fail(1, 'unknown_command');
		// the fallback asks for services
		await Promise.resolve();
		expect(sent[1]).toEqual({ id: 2, type: 'get_services' });
		reply(2, { light: { turn_on: { description: 'on', fields: { brightness: {} } } } });
		const tools = await promise;
		expect(tools).toEqual([
			{
				name: 'light.turn_on',
				description: 'on',
				parameters: { type: 'object', properties: { brightness: {} } },
				domain: 'light',
				source: 'services'
			}
		]);
	});

	it('normalizes a native tools list', async () => {
		const { client, reply } = setup();
		const promise = client.listTools();
		reply(1, { tools: [{ name: 'light_control', description: 'lights', parameters: { a: 1 } }] });
		const tools = await promise;
		expect(tools[0]).toMatchObject({ name: 'light_control', source: 'tools' });
	});

	it('does not swallow real errors from jarvis/tools/list', async () => {
		const { client, fail } = setup();
		const promise = client.listTools();
		fail(1, 'unknown_error', 'boom');
		await expect(promise).rejects.toThrow('boom');
	});

	it('callTool falls back to the matching service call', async () => {
		const { sent, client, fail } = setup();
		const promise = client.callTool('light.turn_on', { entity_id: 'light.a' });
		expect(sent[0]).toEqual({
			id: 1,
			type: 'jarvis/tools/call',
			name: 'light.turn_on',
			arguments: { entity_id: 'light.a' }
		});
		fail(1, 'unknown_command');
		await Promise.resolve();
		expect(sent[1]).toMatchObject({ type: 'call_service', domain: 'light', service: 'turn_on' });
		client.handleMessage({ id: 2, type: 'result', success: true, result: { ok: true } });
		await expect(promise).resolves.toEqual({ ok: true });
	});

	it('sorts projected service tools by name', () => {
		const tools = toolsFromServices({ light: { turn_on: {} }, automation: { trigger: {} } });
		expect(tools.map((t) => t.name)).toEqual(['automation.trigger', 'light.turn_on']);
	});

	// Regression: the page labelled its run button from tools[0]?.source, so a
	// backend that answers jarvis/tools/list with an empty list was reported as
	// "call_service" while callTool() was in fact using jarvis/tools/call.
	it('records which command answered, even for an empty native list', async () => {
		const { client, reply } = setup();
		expect(client.supportsNativeTools).toBeNull();
		const promise = client.listTools();
		reply(1, { tools: [] });
		await expect(promise).resolves.toEqual([]);
		expect(client.supportsNativeTools).toBe(true);
	});

	it('records the fallback when jarvis/tools/list is unknown', async () => {
		const { client, reply, fail } = setup();
		const promise = client.listTools();
		fail(1, 'unknown_command');
		await Promise.resolve();
		reply(2, {});
		await promise;
		expect(client.supportsNativeTools).toBe(false);
	});
});

describe('pure helpers', () => {
	const state = (entity_id: string, s: string, attributes: any = {}): EntityState => ({
		entity_id,
		state: s,
		attributes
	});

	it('splits entity ids', () => {
		expect(splitEntityId('light.kitchen')).toEqual(['light', 'kitchen']);
		expect(splitEntityId('weird')).toEqual(['weird', '']);
		expect(domainOf('media_player.tv')).toBe('media_player');
	});

	it('prefers the registry name, then friendly_name', () => {
		expect(friendlyName(state('light.a', 'on', { friendly_name: 'Lamp' }))).toBe('Lamp');
		expect(
			friendlyName(state('light.a', 'on', { friendly_name: 'Lamp' }), {
				entity_id: 'light.a',
				name: 'Override'
			})
		).toBe('Override');
		expect(friendlyName(state('light.a', 'on'))).toBe('light.a');
	});

	it('reads an area id under either key', () => {
		expect(areaKey({ id: 'lab', name: 'Lab' })).toBe('lab');
		expect(areaKey({ area_id: 'lab', name: 'Lab' })).toBe('lab');
	});

	it('falls back to the device area', () => {
		const entities = new Map([
			['light.a', { entity_id: 'light.a', device_id: 'd1' }],
			['light.b', { entity_id: 'light.b', area_id: 'kitchen', device_id: 'd1' }],
			['light.c', { entity_id: 'light.c' }]
		]);
		const devices = new Map([['d1', { id: 'd1', name: 'D', area_id: 'lab' }]]);
		expect(areaForEntity('light.a', entities, devices)).toBe('lab');
		expect(areaForEntity('light.b', entities, devices)).toBe('kitchen');
		expect(areaForEntity('light.c', entities, devices)).toBeNull();
		expect(areaForEntity('light.missing', entities, devices)).toBeNull();
	});

	it('knows which states count as on', () => {
		expect(isOn(state('light.a', 'on'))).toBe(true);
		expect(isOn(state('cover.a', 'open'))).toBe(true);
		expect(isOn(state('media_player.a', 'playing'))).toBe(true);
		expect(isOn(state('light.a', 'off'))).toBe(false);
		expect(isOn(undefined)).toBe(false);
	});

	it('applies state_changed events, including removals', () => {
		const states = new Map<string, EntityState>();
		const changed = applyStateChanged(states, {
			event_type: 'state_changed',
			data: { entity_id: 'light.a', new_state: state('light.a', 'on') }
		});
		expect(changed).toBe(true);
		expect(states.get('light.a')?.state).toBe('on');

		applyStateChanged(states, {
			event_type: 'state_changed',
			data: { entity_id: 'light.a', new_state: null }
		});
		expect(states.has('light.a')).toBe(false);

		expect(applyStateChanged(states, { event_type: 'call_service', data: {} })).toBe(false);
		expect(applyStateChanged(states, undefined)).toBe(false);
	});

	it('reports no change when a removal is for an entity we never had', () => {
		const states = new Map<string, EntityState>();
		expect(
			applyStateChanged(states, {
				event_type: 'state_changed',
				data: { entity_id: 'light.ghost', new_state: null }
			})
		).toBe(false);
	});
});

describe('the task list', () => {
	it('asks for every task, and reads whole rows back', async () => {
		const { sent, client, reply } = setup();
		const promise = client.listTasks();
		expect(sent[0]).toEqual({ id: 1, type: 'jarvis/tasks/list' });
		reply(1, {
			tasks: [{ id: 'a', title: 'Research', status: 'running', fraction: 0.5, steps: [] }]
		});
		const tasks = await promise;
		expect(tasks[0].fraction).toBe(0.5);
	});

	it('sends a filter only when there is one', async () => {
		const { sent, client, reply } = setup();
		void client.listTasks({ kind: 'research', active: true });
		expect(sent[0]).toEqual({
			id: 1,
			type: 'jarvis/tasks/list',
			kind: 'research',
			active: true
		});
		reply(1, { tasks: [] });
	});

	it('survives a backend that answers with nothing usable', async () => {
		const { client, reply } = setup();
		const promise = client.listTasks();
		reply(1, null);
		await expect(promise).resolves.toEqual([]);
	});

	it('reads one task, and reports a forgotten one as null rather than throwing', async () => {
		const { client, reply, fail } = setup();
		const found = client.getTask('a');
		reply(1, { task: { id: 'a', title: 'x' } });
		expect((await found)?.id).toBe('a');

		const missing = client.getTask('gone');
		fail(2, 'not_found');
		await expect(missing).resolves.toBeNull();
	});

	it('passes a cancel’s honesty straight through', async () => {
		// jarvis-core says whether it actually cancelled and warns when a worker
		// may still be running. Collapsing that to a boolean here would put the
		// lie back one layer up.
		const { client, reply } = setup();
		const promise = client.cancelTask('a');
		reply(1, { cancelled: true, note: 'a worker that does not check may still be running' });
		const result = await promise;
		expect(result.cancelled).toBe(true);
		expect(result.note).toContain('still be running');
	});

	it('reports deleting a task that was already gone as false', async () => {
		const { client, reply, fail } = setup();
		const gone = client.deleteTask('nope');
		fail(1, 'not_found');
		await expect(gone).resolves.toBe(false);

		const real = client.deleteTask('a');
		reply(2, { removed: 'a' });
		await expect(real).resolves.toBe(true);
	});

	it('still raises anything that is not a missing task', async () => {
		const { client, fail } = setup();
		const promise = client.deleteTask('a');
		fail(1, 'unknown_error', 'the store is on fire');
		await expect(promise).rejects.toBeInstanceOf(JarvisCommandError);
	});

	it('counts what clearing finished actually removed', async () => {
		const { client, reply } = setup();
		const promise = client.clearFinishedTasks();
		reply(1, { removed: 4 });
		await expect(promise).resolves.toBe(4);
	});

	it('lets an older backend hide the feature rather than error', async () => {
		// The versioning rule in docs/clients.md: `unknown_command` means degrade.
		const { client, fail } = setup();
		const promise = client.listTasks();
		fail(1, 'unknown_command');
		await expect(promise).rejects.toBeInstanceOf(UnsupportedCommandError);
	});
});
