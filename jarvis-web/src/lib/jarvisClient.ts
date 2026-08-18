// Typed websocket client for the jarvis-core management API.
//
// Same socket, same framing and the same id discipline as `pipeline.ts` (which
// owns the voice run) — this one covers everything else the management UI
// needs: states, service calls, event subscriptions and the registries.
// Home Assistant answers most of these too; the commands it does not know come
// back as `unknown_command`, which surfaces as an UnsupportedCommandError the
// pages render as a hint instead of an exception.
//
// The transport is injected as a `send` function and incoming frames are fed to
// `handleMessage()`, so the whole class is unit-testable in plain Node.

import * as conversations from './conversations';
import { toTaskList, toTaskRow, type TaskRow } from './tasks';
import type { McpServer } from './mcpDraft';

export type SendFn = (data: string) => void;

/** A state object as `get_states` / `state_changed` report it. */
export interface EntityState {
	entity_id: string;
	state: string;
	attributes: Record<string, any>;
	last_changed?: string;
	last_updated?: string;
	context?: Record<string, any>;
}

export interface AreaEntry {
	/** jarvis-core calls it `id`; Home Assistant calls it `area_id`. */
	id?: string;
	area_id?: string;
	name: string;
	aliases?: string[];
}

/** One registered tool, with whether the console created it. */
export interface ToolRow {
	name: string;
	description: string;
	tier: number;
	domain?: string | null;
	parameters?: Record<string, any> | null;
	/** False for built-ins and tools from the user's `*.tool.yaml`. */
	editable: boolean;
	service?: Record<string, any> | null;
	created_at?: number | null;
	updated_at?: number | null;
}

/** What the console may send when creating or editing a tool. */
export interface ToolDraft {
	name: string;
	description: string;
	tier: number;
	service: Record<string, any>;
}

/** A phone, desktop or satellite registered on the socket. */
export interface CompanionDevice {
	device_id: string;
	name: string;
	platform?: string;
	capabilities?: string[];
	connected?: boolean;
	app_version?: string | null;
	action_count?: number;
	actions?: { name: string; description?: string; tier?: number }[];
}

/** A tier-3 action held until a human says yes. */
export interface PendingApproval {
	request_id?: string;
	id?: string;
	tool: string;
	description?: string;
	arguments?: Record<string, any>;
	tier?: number;
	created?: number;
	expires_at?: number;
	/**
	 * Set when this held request is a QUESTION rather than an action.
	 *
	 * The name of the single argument the human's reply is allowed to write —
	 * see `Tool.answerable` in jarvis-core. It travels on the request so that a
	 * surface can tell a question from an action without holding a tool
	 * registry; the phone does not have one, and a rule based on the tool's
	 * name would be wrong for the first tool anybody adds.
	 */
	answerable?: string | null;
	/** The answers on offer. Empty or absent means free text. */
	choices?: string[];
	/**
	 * True when the turn that raised this had already read somebody else's
	 * words — a fetched page, a scraped document, a notification.
	 *
	 * It changes nothing about what may run: the tier already decided that. It
	 * changes what the human is told, because for a QUESTION the text on screen
	 * is the model's own sentence, and a turn that has read a hostile page can
	 * write that sentence.
	 */
	tainted?: boolean;
}

/** A row in the chat sidebar: enough to list a conversation, not to read it. */
export interface ConversationSummary {
	id: string;
	title: string;
	created: number;
	last_active: number;
	/** How many turns it holds, user and assistant counted separately. */
	turns: number;
	/** The last thing said in it, bounded. */
	preview: string;
}

/** One stored tool call. The tool's *result* is never kept — only its verdict. */
export interface ArchivedToolCall {
	name: string;
	arguments?: Record<string, unknown>;
	status?: string | null;
	ok?: boolean;
	error?: string | null;
}

export interface ArchivedTurn {
	role: 'user' | 'assistant';
	content: string;
	timestamp?: number;
	/** Only on an assistant turn, and only when the model reasons out loud. */
	thinking?: string;
	tool_calls?: ArchivedToolCall[];
}

/** A past conversation, as `jarvis/conversation/get` returns it. */
export interface ArchivedConversation {
	id: string;
	title: string;
	created: number;
	last_active: number;
	turns: ArchivedTurn[];
}

/** One long-lived access token. Never carries the secret. */
export interface AccessToken {
	id: string;
	name: string;
	created_at?: number;
	last_used_at?: number | null;
	/** Something is holding a live socket open with it right now. */
	connected?: boolean;
}

/** One editable setting, with where its current value came from. */
export interface SettingRow {
	key: string;
	label: string;
	group: string;
	type: 'string' | 'number' | 'integer' | 'boolean' | 'choice';
	/** 'live' | 'restart' | 'split' — when a change takes effect. */
	apply: string;
	note?: string;
	value: unknown;
	/** What configuration.yaml says, which is what a reset falls back to. */
	yaml_value: unknown;
	/** 'overlay' | 'yaml' | 'package' | 'default' | 'unapplied'. */
	source: string;
	unapplied_reason?: string | null;
	package?: string | null;
	choices?: string[];
}

export interface UnappliedSetting {
	key: string;
	value: unknown;
	reason: string;
}

export interface SettingResult {
	key: string;
	value: unknown;
	/** Whether it reached the running system, or only the store. */
	applied: boolean;
	apply: string;
	restart_required: boolean;
	settings: SettingRow[];
}

/** One automation as jarvis-core reports it, YAML-authored or console-authored. */
export interface AutomationRow {
	id: string;
	entity_id: string;
	alias: string;
	description?: string;
	mode?: string;
	enabled?: boolean;
	trigger: unknown[];
	condition: unknown[];
	action: unknown[];
	/**
	 * False for automations that came from `automations.yaml`. They are listed
	 * so the page is not empty on a box that is visibly running them, but the
	 * console cannot edit or delete them — only the file they live in can.
	 */
	editable: boolean;
	/** True when RUNNING this one has to go past a human. */
	needs_approval?: boolean;
	/** Why, in a phrase — "can lock", "calls something this cannot read". */
	reach?: string;
	created_at?: number | null;
	updated_at?: number | null;
}

/** What the console may send when creating or editing an automation. */
export interface AutomationDraft {
	alias: string;
	description?: string;
	mode?: string;
	trigger: unknown[];
	condition?: unknown[];
	action: unknown[];
}

export interface EntityRegistryEntry {
	entity_id: string;
	unique_id?: string;
	platform?: string;
	name?: string | null;
	original_name?: string | null;
	device_id?: string | null;
	area_id?: string | null;
	aliases?: string[];
	icon?: string | null;
	disabled?: boolean;
	hidden?: boolean;
	/** jarvis-core only: visible to the LLM / voice assistant. */
	exposed?: boolean;
	capabilities?: Record<string, any>;
}

export interface DeviceRegistryEntry {
	id: string;
	name: string;
	manufacturer?: string | null;
	model?: string | null;
	area_id?: string | null;
	platform?: string | null;
	disabled?: boolean;
}

/** What `jarvis/mcp/list` and the three write commands all answer with. */
export interface McpListing {
	servers: McpServer[];
	/**
	 * Whether jarvis-core will start a program for a stdio server. Read-only
	 * here by design — see the methods below.
	 */
	allow_stdio: boolean;
	default_tier: number;
}

export interface BusEvent {
	event_type: string;
	data: Record<string, any>;
	origin?: string;
	time_fired?: string;
	context?: Record<string, any>;
}

export interface ServiceDescription {
	description?: string;
	fields?: Record<string, any>;
	supports_response?: boolean;
}
export type ServiceCatalog = Record<string, Record<string, ServiceDescription>>;

export interface ToolDescription {
	name: string;
	description?: string;
	parameters?: Record<string, any>;
	domain?: string | null;
	/** Set by the client when the entry was synthesised from a service. */
	source?: 'tools' | 'services';
}

/** A command the server rejected. `code` is the backend's machine code. */
export class JarvisCommandError extends Error {
	code: string;
	constructor(code: string, message: string) {
		super(message);
		this.name = 'JarvisCommandError';
		this.code = code;
	}
}

/** The backend does not implement this command (HA vs jarvis-core drift). */
export class UnsupportedCommandError extends JarvisCommandError {
	command: string;
	constructor(command: string, message: string) {
		super('unknown_command', message);
		this.name = 'UnsupportedCommandError';
		this.command = command;
	}
}

export function isUnsupported(err: unknown): err is UnsupportedCommandError {
	return err instanceof UnsupportedCommandError;
}

export interface Subscription {
	/** The command id the backend keyed the subscription on. */
	id: number;
	unsubscribe(): Promise<void>;
}

interface Pending {
	type: string;
	resolve: (value: any) => void;
	reject: (err: Error) => void;
	timer?: ReturnType<typeof setTimeout>;
}

/** Default ceiling on how long a command may stay in flight. */
export const DEFAULT_COMMAND_TIMEOUT_MS = 20_000;

export interface JarvisClientOptions {
	/** Every frame that is not a result/event for a known id. */
	onUnhandled?: (msg: any) => void;
	/**
	 * Reject a command that has had no result after this many ms. 0 disables it.
	 * A timeout is required, not optional: the /ws relay completes the browser
	 * upgrade *before* it has dialled the backend, so with the backend down every
	 * command is accepted and then silently buffered forever.
	 */
	timeoutMs?: number;
}

export class JarvisClient {
	private send: SendFn;
	private opts: JarvisClientOptions;
	private pending = new Map<number, Pending>();
	private subs = new Map<number, (event: BusEvent) => void>();
	private timeoutMs: number;

	/** Next command id. Shared counter — ids are unique per connection. */
	nextId = 1;

	/**
	 * Whether the backend answered `jarvis/tools/list`: true after a native list,
	 * false after the service-catalogue fallback, null before `listTools()` ran.
	 * Pages label the run button from this rather than guessing from the rows,
	 * which is wrong whenever a native list comes back empty.
	 */
	supportsNativeTools: boolean | null = null;

	constructor(send: SendFn, opts: JarvisClientOptions = {}) {
		this.send = send;
		this.opts = opts;
		this.timeoutMs = opts.timeoutMs ?? DEFAULT_COMMAND_TIMEOUT_MS;
	}

	/** Live subscription ids, in creation order. */
	get subscriptionIds(): number[] {
		return [...this.subs.keys()];
	}

	/** Command ids still awaiting a result. */
	get pendingIds(): number[] {
		return [...this.pending.keys()];
	}

	/** Feed every incoming text frame here. */
	handleMessage(raw: string | Record<string, any>): void {
		let msg: any;
		if (typeof raw === 'string') {
			try {
				msg = JSON.parse(raw);
			} catch {
				return;
			}
		} else {
			msg = raw;
		}
		if (!msg || typeof msg !== 'object') return;

		if (msg.type === 'result') {
			const p = this.pending.get(msg.id);
			if (!p) {
				// A result for an id we never sent, or an id-less protocol error
				// (jarvis-core answers a malformed frame with `"id": null`). Do not
				// drop it on the floor — the caller asked to see stray frames.
				this.opts.onUnhandled?.(msg);
				return;
			}
			this.settle(msg.id);
			if (msg.success) {
				p.resolve(msg.result);
				return;
			}
			const code = msg.error?.code ?? 'unknown_error';
			const message = msg.error?.message ?? `${p.type} failed`;
			p.reject(
				code === 'unknown_command'
					? new UnsupportedCommandError(p.type, message)
					: new JarvisCommandError(code, message)
			);
			return;
		}

		if (msg.type === 'pong') {
			const p = this.pending.get(msg.id);
			if (p) {
				this.settle(msg.id);
				p.resolve(null);
			}
			return;
		}

		if (msg.type === 'event') {
			const cb = this.subs.get(msg.id);
			if (cb) {
				cb(msg.event as BusEvent);
				return;
			}
		}
		this.opts.onUnhandled?.(msg);
	}

	/** Drop a pending entry and cancel its timeout. */
	private settle(id: number): Pending | undefined {
		const p = this.pending.get(id);
		if (p?.timer !== undefined) clearTimeout(p.timer);
		this.pending.delete(id);
		return p;
	}

	/** Fail every in-flight command and drop subscription bookkeeping. */
	handleClose(reason = 'connection closed'): void {
		const inflight = [...this.pending.values()];
		for (const p of inflight) if (p.timer !== undefined) clearTimeout(p.timer);
		this.pending.clear();
		this.subs.clear();
		for (const p of inflight) p.reject(new JarvisCommandError('closed', reason));
	}

	/** Send a command and await its result. */
	command<T = any>(payload: Record<string, any>): Promise<T> {
		const id = this.nextId++;
		const type = String(payload.type ?? 'unknown');
		const promise = new Promise<T>((resolve, reject) => {
			const entry: Pending = { type, resolve, reject };
			if (this.timeoutMs > 0) {
				entry.timer = setTimeout(() => {
					this.pending.delete(id);
					this.subs.delete(id);
					reject(
						new JarvisCommandError('timeout', `${type} timed out after ${this.timeoutMs}ms`)
					);
				}, this.timeoutMs);
				// Never hold a Node process open just for an in-flight command.
				(entry.timer as any)?.unref?.();
			}
			this.pending.set(id, entry);
		});
		try {
			// `id` last: a caller-supplied payload key named `id` (registry updates
			// spread arbitrary change objects into the frame) would otherwise
			// overwrite the command id and orphan the pending entry forever.
			this.send(JSON.stringify({ ...payload, id }));
		} catch (err) {
			this.settle(id);
			return Promise.reject(err instanceof Error ? err : new Error(String(err)));
		}
		return promise;
	}

	ping(): Promise<null> {
		return this.command({ type: 'ping' });
	}

	// --- state -------------------------------------------------------------
	getStates(): Promise<EntityState[]> {
		return this.command<EntityState[]>({ type: 'get_states' });
	}

	getConfig(): Promise<Record<string, any>> {
		return this.command({ type: 'get_config' });
	}

	getServices(): Promise<ServiceCatalog> {
		return this.command<ServiceCatalog>({ type: 'get_services' });
	}

	callService(
		domain: string,
		service: string,
		serviceData: Record<string, any> = {},
		extra: { target?: Record<string, any>; returnResponse?: boolean } = {}
	): Promise<any> {
		const payload: Record<string, any> = {
			type: 'call_service',
			domain,
			service,
			service_data: serviceData
		};
		if (extra.target) payload.target = extra.target;
		if (extra.returnResponse) payload.return_response = true;
		return this.command(payload);
	}

	/** Convenience: call a service against one entity. */
	callEntityService(
		entityId: string,
		service: string,
		serviceData: Record<string, any> = {}
	): Promise<any> {
		return this.callService(domainOf(entityId), service, { entity_id: entityId, ...serviceData });
	}

	// --- events ------------------------------------------------------------
	/**
	 * Subscribe to bus events. `eventType` omitted means every event.
	 * The returned handle removes both the server subscription and the local
	 * callback; calling it twice is a no-op.
	 */
	async subscribeEvents(
		callback: (event: BusEvent) => void,
		eventType?: string
	): Promise<Subscription> {
		const id = this.nextId;
		const payload: Record<string, any> = { type: 'subscribe_events' };
		if (eventType) payload.event_type = eventType;
		// Register before awaiting: events may arrive before the result frame.
		this.subs.set(id, callback);
		try {
			await this.command(payload);
		} catch (err) {
			this.subs.delete(id);
			throw err;
		}
		let done = false;
		return {
			id,
			unsubscribe: async () => {
				if (done) return;
				done = true;
				if (!this.subs.delete(id)) return;
				try {
					await this.command({ type: 'unsubscribe_events', subscription: id });
				} catch {
					// The socket may already be gone; local bookkeeping is what matters.
				}
			}
		};
	}

	fireEvent(eventType: string, eventData: Record<string, any> = {}): Promise<any> {
		return this.command({ type: 'fire_event', event_type: eventType, event_data: eventData });
	}

	// --- registries --------------------------------------------------------
	listAreas(): Promise<AreaEntry[]> {
		return this.command<AreaEntry[]>({ type: 'config/area_registry/list' });
	}

	createArea(name: string, aliases?: string[]): Promise<AreaEntry> {
		const payload: Record<string, any> = { type: 'config/area_registry/create', name };
		if (aliases) payload.aliases = aliases;
		return this.command<AreaEntry>(payload);
	}

	updateArea(areaId: string, changes: { name?: string; aliases?: string[] }): Promise<AreaEntry> {
		return this.command<AreaEntry>({
			type: 'config/area_registry/update',
			area_id: areaId,
			...changes
		});
	}

	deleteArea(areaId: string): Promise<any> {
		return this.command({ type: 'config/area_registry/delete', area_id: areaId });
	}

	listEntities(): Promise<EntityRegistryEntry[]> {
		return this.command<EntityRegistryEntry[]>({ type: 'config/entity_registry/list' });
	}

	/**
	 * Update a registry entry. jarvis-core ignores null-valued fields, so pass
	 * `''` (not null) to clear an area assignment.
	 */
	updateEntity(
		entityId: string,
		changes: Partial<
			Pick<
				EntityRegistryEntry,
				'name' | 'icon' | 'area_id' | 'device_id' | 'aliases' | 'disabled' | 'hidden' | 'exposed'
			>
		>
	): Promise<any> {
		return this.command({
			type: 'config/entity_registry/update',
			entity_id: entityId,
			...changes
		});
	}

	/** The manageable view of tools, with `editable` and the service block. */
	listToolRows(): Promise<ToolRow[]> {
		return this.command<ToolRow[]>({ type: 'config/tool/list' });
	}

	createTool(draft: ToolDraft): Promise<{ tool: ToolRow }> {
		return this.command({ type: 'config/tool/create', tool: draft });
	}

	updateTool(name: string, draft: ToolDraft): Promise<{ tool: ToolRow }> {
		return this.command({ type: 'config/tool/update', name, tool: draft });
	}

	deleteTool(name: string): Promise<any> {
		return this.command({ type: 'config/tool/delete', name });
	}

	/**
	 * The machines running Jarvis clients — phones, desktops, satellites.
	 *
	 * Not `listDevices()`, which is the registry of things in the HOUSE. These
	 * are the clients on the other end of the socket, each advertising what it
	 * will let Jarvis do to it.
	 */
	listCompanions(): Promise<CompanionDevice[]> {
		return this.command<CompanionDevice[]>({ type: 'config/companion/list' });
	}

	/**
	 * Every credential that may talk to this house, and whether one is in use.
	 *
	 * Built server-side from the auth manager, not from any pairing record: a
	 * token store that failed to load would otherwise render as "no devices"
	 * over a live full-privilege credential.
	 */
	listTokens(): Promise<AccessToken[]> {
		return this.command<AccessToken[]>({ type: 'config/token/list' });
	}

	/** Revoke a credential, and hang up whatever is holding it open. */
	revokeToken(tokenId: string): Promise<any> {
		return this.command({ type: 'config/token/revoke', token_id: tokenId });
	}

	// --- approvals ---------------------------------------------------------
	/**
	 * Approve or deny a held tier-3 action.
	 *
	 * Single use and enforced server-side: the request is popped before it runs,
	 * so a double-click cannot execute it twice and a replayed id does nothing.
	 */
	resolveApproval(requestId: string, approved: boolean, answer?: string): Promise<any> {
		return this.command({
			type: 'jarvis/approve',
			request_id: requestId,
			approved,
			// Only ever reaches the one argument the held tool named, and is
			// ignored entirely by tools that take no answer. Omitted rather
			// than sent as null so an older jarvis-core sees the same frame it
			// always did.
			...(answer === undefined ? {} : { answer })
		});
	}

	/** What is waiting on a human right now. */
	async pendingApprovals(): Promise<PendingApproval[]> {
		const result = await this.callService('llm', 'pending_requests', {}, {
			returnResponse: true
		});
		const list = Array.isArray(result)
			? result
			: (result?.response ?? result?.result ?? result?.requests ?? []);
		return Array.isArray(list) ? list : [];
	}

	// --- conversation history -----------------------------------------------
	//
	// Delegated to `$lib/conversations` rather than written out here, because
	// `PipelineClient` needs the same four commands — the HUD holds one of
	// those and opening a second socket to draw a sidebar would be absurd. The
	// wire strings live in one file so a rename cannot leave chat mode working
	// on one surface and 404ing on the other.
	private readonly send_ = <T,>(payload: Record<string, any>): Promise<T> =>
		this.command<T>(payload);

	/** Past conversations, most recent first. Summaries only. */
	listConversations(): Promise<ConversationSummary[]> {
		return conversations.listConversations(this.send_);
	}

	/** One conversation in full, with each turn's reasoning and tool calls. */
	getConversation(conversationId: string): Promise<ArchivedConversation | null> {
		return conversations.getConversation(this.send_, conversationId);
	}

	/** Forget it, in the model's memory and in the archive alike. */
	deleteConversation(conversationId: string): Promise<boolean> {
		return conversations.deleteConversation(this.send_, conversationId);
	}

	/** A name of your own, instead of the conversation's first sentence. */
	renameConversation(conversationId: string, title: string): Promise<boolean> {
		return conversations.renameConversation(this.send_, conversationId, title);
	}

	// --- MCP servers ---------------------------------------------------------
	//
	// Read, add, remove, reconnect. There is deliberately no way to turn
	// `allow_stdio` on from here: that is the line between jarvis-core fetching
	// a URL and jarvis-core starting a program, and it lives in
	// configuration.yaml so that no request can cross it. `listMcpServers`
	// reports the flag so this console can explain the closed fields rather
	// than submitting a form the server will refuse.

	listMcpServers(): Promise<McpListing> {
		return this.command<McpListing>({ type: 'jarvis/mcp/list' });
	}

	addMcpServer(payload: Record<string, unknown>): Promise<McpListing> {
		return this.command<McpListing>({ type: 'jarvis/mcp/add', ...payload });
	}

	removeMcpServer(name: string): Promise<McpListing> {
		return this.command<McpListing>({ type: 'jarvis/mcp/remove', name });
	}

	/** One server, or all of them. Re-reads the tool list, which is the point. */
	reconnectMcp(name = ''): Promise<McpListing> {
		const payload: Record<string, any> = { type: 'jarvis/mcp/reconnect' };
		if (name) payload.name = name;
		return this.command<McpListing>(payload);
	}

	// --- tasks ---------------------------------------------------------------
	//
	// Read plus two destructive verbs, and no create: jarvis-core mints a task
	// from whatever is about to do the work, because a task nothing is driving
	// is the empty seam the registry exists to close.
	//
	// The console keeps its list live from `jarvis_task_added/updated/removed`
	// over `subscribeEvents`, so these are for the first paint and for acting on
	// a row — not for polling.

	/** Every tracked job, newest first. Whole tasks, steps included. */
	async listTasks(opts: { kind?: string; active?: boolean } = {}): Promise<TaskRow[]> {
		const payload: Record<string, any> = { type: 'jarvis/tasks/list' };
		if (opts.kind) payload.kind = opts.kind;
		if (opts.active) payload.active = true;
		return toTaskList(await this.command(payload));
	}

	/** One task in full, or null if it has been forgotten. */
	async getTask(taskId: string): Promise<TaskRow | null> {
		try {
			const result = await this.command<{ task?: unknown }>({
				type: 'jarvis/tasks/get',
				task_id: taskId
			});
			return toTaskRow(result?.task);
		} catch (err) {
			if (err instanceof JarvisCommandError && err.code === 'not_found') return null;
			throw err;
		}
	}

	/**
	 * Ask a task to stop — and it is an ASK.
	 *
	 * jarvis-core's registry is a record, not a scheduler: it cannot reach into
	 * the coroutine doing the work. The reply carries `cancelled` and, when a
	 * worker might not be checking, a `note` saying so. Both are passed straight
	 * through, because a UI that shows "cancelled" over work that is still
	 * running is the same lie one layer up.
	 */
	cancelTask(taskId: string): Promise<{ task?: unknown; cancelled: boolean; note?: string; reason?: string }> {
		return this.command({ type: 'jarvis/tasks/cancel', task_id: taskId });
	}

	/** Forget one task. Does not stop it — see `cancelTask`. */
	async deleteTask(taskId: string): Promise<boolean> {
		try {
			await this.command({ type: 'jarvis/tasks/delete', task_id: taskId });
			return true;
		} catch (err) {
			if (err instanceof JarvisCommandError && err.code === 'not_found') return false;
			throw err;
		}
	}

	/** Forget every finished task, leaving the live ones. Returns how many went. */
	async clearFinishedTasks(): Promise<number> {
		const result = await this.command<{ removed?: number }>({
			type: 'jarvis/tasks/clear_finished'
		});
		return Number(result?.removed ?? 0) || 0;
	}

	// --- settings ----------------------------------------------------------
	listSettings(): Promise<{ settings: SettingRow[]; unapplied: UnappliedSetting[] }> {
		return this.command({ type: 'config/settings/list' });
	}

	setSetting(key: string, value: unknown): Promise<SettingResult> {
		return this.command({ type: 'config/settings/set', key, value });
	}

	/** Drop an override so the value in configuration.yaml shows through again. */
	resetSetting(key: string): Promise<SettingResult> {
		return this.command({ type: 'config/settings/reset', key });
	}

	// --- automations -------------------------------------------------------
	listAutomations(): Promise<AutomationRow[]> {
		return this.command<AutomationRow[]>({ type: 'config/automation/list' });
	}

	/**
	 * The draft goes under `automation:` rather than being spread into the
	 * frame. jarvis-core validates it against an allowlist and refuses unknown
	 * fields, and the frame's own `id` and `type` are not fields of an
	 * automation — nesting keeps the transport's keys out of the payload.
	 */
	createAutomation(draft: AutomationDraft): Promise<{ automation: AutomationRow }> {
		return this.command({ type: 'config/automation/create', automation: draft });
	}

	updateAutomation(
		automationId: string,
		draft: AutomationDraft
	): Promise<{ automation: AutomationRow }> {
		return this.command({
			type: 'config/automation/update',
			automation_id: automationId,
			automation: draft
		});
	}

	deleteAutomation(automationId: string): Promise<any> {
		return this.command({ type: 'config/automation/delete', automation_id: automationId });
	}

	listDevices(): Promise<DeviceRegistryEntry[]> {
		return this.command<DeviceRegistryEntry[]>({ type: 'config/device_registry/list' });
	}

	updateDevice(deviceId: string, changes: Record<string, any>): Promise<any> {
		return this.command({ type: 'config/device_registry/update', device_id: deviceId, ...changes });
	}

	// --- voice / llm -------------------------------------------------------
	listPipelines(): Promise<{ pipelines: any[]; preferred_pipeline: string | null }> {
		return this.command({ type: 'assist_pipeline/pipeline/list' });
	}

	/**
	 * LLM tools. Prefers `jarvis/tools/list`; when the backend does not know
	 * that command the service catalogue is projected into the same shape so
	 * the page still has something to show.
	 */
	async listTools(): Promise<ToolDescription[]> {
		try {
			const result = await this.command<any>({ type: 'jarvis/tools/list' });
			this.supportsNativeTools = true;
			return normalizeTools(result);
		} catch (err) {
			if (!isUnsupported(err)) throw err;
			this.supportsNativeTools = false;
			return toolsFromServices(await this.getServices());
		}
	}

	/** Run a tool by name; falls back to the service call it maps onto. */
	async callTool(name: string, args: Record<string, any> = {}): Promise<any> {
		try {
			return await this.command({ type: 'jarvis/tools/call', name, arguments: args });
		} catch (err) {
			if (!isUnsupported(err)) throw err;
			const [domain, service] = splitToolName(name);
			if (!domain || !service) throw err;
			return this.callService(domain, service, args);
		}
	}
}

// --- pure helpers -----------------------------------------------------------

export function splitEntityId(entityId: string): [string, string] {
	const idx = entityId.indexOf('.');
	return idx < 0 ? [entityId, ''] : [entityId.slice(0, idx), entityId.slice(idx + 1)];
}

export function domainOf(entityId: string): string {
	return splitEntityId(entityId)[0];
}

/** `light.kitchen` -> `light_kitchen`-ish tool names and back. */
export function splitToolName(name: string): [string, string] {
	const parts = String(name).split(/[./]/);
	if (parts.length < 2) return ['', ''];
	return [parts[0], parts.slice(1).join('_')];
}

export function friendlyName(
	state: EntityState | undefined,
	entry?: EntityRegistryEntry
): string {
	return (
		entry?.name ||
		state?.attributes?.friendly_name ||
		entry?.original_name ||
		state?.entity_id ||
		entry?.entity_id ||
		'unknown'
	);
}

export function areaKey(area: AreaEntry): string {
	return String(area.area_id ?? area.id ?? '');
}

/** Which area an entity belongs to, following its device when unset. */
export function areaForEntity(
	entityId: string,
	entities: Map<string, EntityRegistryEntry>,
	devices: Map<string, DeviceRegistryEntry>
): string | null {
	const entry = entities.get(entityId);
	if (!entry) return null;
	if (entry.area_id) return entry.area_id;
	if (entry.device_id) return devices.get(entry.device_id)?.area_id ?? null;
	return null;
}

const ON_STATES = new Set(['on', 'open', 'playing', 'home', 'unlocked', 'cleaning', 'heat', 'cool']);
export function isOn(state: EntityState | undefined): boolean {
	return state ? ON_STATES.has(state.state) : false;
}

export function normalizeTools(result: any): ToolDescription[] {
	const raw = Array.isArray(result) ? result : (result?.tools ?? []);
	return (Array.isArray(raw) ? raw : []).map((tool: any) => ({
		name: String(tool?.name ?? tool?.function?.name ?? ''),
		description: tool?.description ?? tool?.function?.description ?? '',
		parameters: tool?.parameters ?? tool?.function?.parameters ?? {},
		domain: tool?.domain ?? null,
		source: 'tools' as const
	}));
}

/** Project the service catalogue into tool-shaped rows. */
export function toolsFromServices(catalog: ServiceCatalog): ToolDescription[] {
	const out: ToolDescription[] = [];
	for (const [domain, services] of Object.entries(catalog ?? {})) {
		for (const [service, meta] of Object.entries(services ?? {})) {
			out.push({
				name: `${domain}.${service}`,
				description: meta?.description ?? '',
				parameters: { type: 'object', properties: meta?.fields ?? {} },
				domain,
				source: 'services'
			});
		}
	}
	out.sort((a, b) => a.name.localeCompare(b.name));
	return out;
}

/**
 * Apply a `state_changed` event to a state map. Returns true when something
 * moved, so callers can skip a re-render.
 */
export function applyStateChanged(
	states: Map<string, EntityState>,
	event: BusEvent | undefined
): boolean {
	if (!event || event.event_type !== 'state_changed') return false;
	const entityId = event.data?.entity_id;
	if (!entityId) return false;
	const newState = event.data?.new_state;
	if (newState) {
		states.set(entityId, newState as EntityState);
		return true;
	}
	// A removal for an entity we never had moves nothing — say so, so callers do
	// not republish (and re-render) the whole list on every unrelated removal.
	return states.delete(entityId);
}
