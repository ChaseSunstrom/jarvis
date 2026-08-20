/**
 * n8n, as the console understands it.
 *
 * jarvis-core owns everything that matters here: which workflows exist, what
 * may be switched on, and — the important one — that Jarvis never touches a
 * credential. This file is the reading of it.
 *
 * ## Why there is no form for the URL and the key
 *
 * They live in `configuration.yaml`, and the console only reports them. An API
 * key for n8n is a credential to a system that sends mail and moves money, and
 * the same argument that keeps a forge token out of the browser applies
 * harder: a setting that can be written by a request is a setting a stolen
 * session can write. So the page says exactly which two lines to add, and then
 * proves they work with CHECK — which is the part that is actually hard to get
 * right, because a wrong URL and a wrong key fail in nearly the same way.
 */

export interface N8nInstance {
	url: string;
	/** Whether a key is configured. jarvis-core NEVER sends the value. */
	has_key: boolean;
	/** Whether JARVIS may switch a workflow on. The console's button may anyway. */
	allow_activate: boolean;
	tag: string;
	configured: boolean;
	/** Present only when the backend knows about the optional `/rest` login. */
	login?: N8nInstanceLogin;
}

export interface N8nWorkflow {
	id: string;
	name: string;
	active: boolean;
	nodes: number;
	tags: string[];
	updated_at: string;
}

export interface N8nNode {
	name: string;
	type: string;
	has_credential: boolean;
	credential_types: string[];
}

export interface N8nConnectionNeeded {
	node: string;
	credential_type: string;
}

export interface N8nGraph {
	id: string;
	name: string;
	active: boolean;
	nodes: N8nNode[];
	edges: [string, string][];
	connections_needed: N8nConnectionNeeded[];
}

export interface N8nExecution {
	id: string;
	workflow_id: string;
	status: string;
	started_at: string;
	stopped_at: string;
	mode: string;
}

/** One layer of "does this work", with the reason it does not. */
export interface N8nCapability {
	available: boolean;
	/**
	 * A slug from a closed set: "", "unconfigured", "credentials", "mfa",
	 * "licence", "too old", "unreachable", "not set up", "bot filter". The
	 * console reads it to choose an emphasis; the detail is what it shows.
	 */
	reason: string;
	detail: string;
}

export interface N8nCapabilities {
	api: N8nCapability;
	login: N8nCapability;
	builder: N8nCapability;
	checked_at: number;
}

export interface N8nCheck {
	ok: boolean;
	detail: string;
	/** Absent on a backend older than the three-layer probe. */
	capabilities?: N8nCapabilities;
	summary?: string;
}

export interface N8nInstanceLogin {
	email: string;
	has_password: boolean;
}

export interface N8nHealth {
	workflow_id: string;
	name: string;
	active: boolean;
	healthy: boolean;
	summary: string;
	connections_needed: N8nConnectionNeeded[];
	runs: number;
	failures: number;
	running_now: number;
	last_status: string;
	last_run: string;
	next_step: string;
}

export interface N8nTranscriptLine {
	/** "builder", "tool", or "you". */
	role: string;
	text: string;
}

/**
 * What to do next, given how far the setup got.
 *
 * Three states and three different next steps, which is why this is not a
 * boolean: no URL is "add two lines", a URL with no key is "make a key", and
 * both present is "press CHECK" — because configured is not the same as
 * working, and this client was written against n8n's documentation rather than
 * a live instance.
 */
export function describeInstance(instance: N8nInstance | null): string {
	if (!instance || !instance.url) {
		return 'No n8n configured. Add `n8n: url:` to configuration.yaml and restart.';
	}
	if (!instance.has_key) {
		return `${instance.url} — no API key. Make one in n8n under Settings → n8n API, then set N8N_API_KEY.`;
	}
	return `${instance.url} — key configured. Press CHECK to prove it works.`;
}

/**
 * The sentence under a workflow that needs credentials attached.
 *
 * This is the console half of "ask for connections": Jarvis reports what a
 * node asked for and strips the guessed id, and a person attaches the real
 * credential in n8n, where the secrets already live.
 */
export function describeConnections(needed: readonly N8nConnectionNeeded[]): string {
	if (!needed.length) return '';
	const parts = needed.map((n) => `${n.credential_type} for “${n.node}”`);
	const list =
		parts.length === 1
			? parts[0]
			: `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
	return `Connect ${list} in n8n (Credentials → New), attach each to its node, then activate.`;
}

/** Whether this workflow can do anything yet. */
export function isReady(graph: N8nGraph | null): boolean {
	return !!graph && graph.connections_needed.length === 0;
}

/** `active` as a word, because a bare checkbox does not say which way is safe. */
export function describeActive(workflow: N8nWorkflow): string {
	return workflow.active ? 'live — its trigger is firing' : 'off';
}

/**
 * A run's status as something to colour by.
 *
 * n8n's own vocabulary is `success`, `error`, `waiting`, `running`,
 * `canceled` (one L, American spelling — it is their field, not ours).
 */
export type RunTone = 'good' | 'bad' | 'busy' | 'idle';

export function runTone(status: string): RunTone {
	const said = (status || '').toLowerCase();
	if (said === 'success') return 'good';
	if (said === 'error' || said === 'crashed' || said === 'failed') return 'bad';
	if (said === 'running' || said === 'waiting' || said === 'new') return 'busy';
	return 'idle';
}

/** Newest first, which is the order anybody wants runs in. */
export function newestFirst(runs: readonly N8nExecution[]): N8nExecution[] {
	return [...runs].sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''));
}

/**
 * The three-line answer to "does this work?", in the order to fix them.
 *
 * Not one line, because the three layers fail independently and for different
 * reasons: an API key can be wrong while the login is fine, and the AI builder
 * can be missing while both work. A single "n8n: no" sends somebody to the
 * wrong half of their setup.
 *
 * Older backends answer CHECK with `{ok, detail}` and no capabilities. That is
 * not an error — it is a jarvis-core that predates the probe — so this returns
 * the one line it does have rather than inventing two empty ones.
 */
export interface CapabilityLine {
	label: string;
	available: boolean;
	detail: string;
	/** True when this line is the reason the ones below it are unavailable. */
	blocking: boolean;
}

export function capabilityLines(check: N8nCheck | null): CapabilityLine[] {
	if (!check) return [];
	const caps = check.capabilities;
	if (!caps) {
		return [{ label: 'n8n', available: check.ok, detail: check.detail, blocking: !check.ok }];
	}
	return [
		{
			label: 'Public API',
			available: caps.api.available,
			detail: caps.api.detail,
			// The one everything else depends on: with no API key there is no
			// workflow list, and the other two lines are beside the point.
			blocking: !caps.api.available
		},
		{
			label: 'Login',
			available: caps.login.available,
			detail: caps.login.detail,
			blocking: false
		},
		{
			label: 'AI builder',
			available: caps.builder.available,
			detail: caps.builder.detail,
			blocking: false
		}
	];
}

/**
 * Whether to offer the "ask n8n's builder" box at all.
 *
 * A form that submits into a 403 is worse than no form. The reason is shown
 * either way — somebody who set up a model and did not get the feature should
 * be told which of the two switches is missing, not left wondering.
 */
export function canUseBuilder(check: N8nCheck | null): boolean {
	return Boolean(check?.capabilities?.builder.available);
}

/**
 * Why the builder box is absent, phrased for the person who expected it.
 *
 * "" when it is available, or when nothing has been checked yet — an absence
 * before CHECK is not a finding.
 */
export function whyNoBuilder(check: N8nCheck | null): string {
	const builder = check?.capabilities?.builder;
	if (!builder || builder.available) return '';
	return builder.detail;
}

/** How a health verdict should be coloured. Same vocabulary as a run. */
export function healthTone(health: N8nHealth | null): RunTone {
	if (!health) return 'idle';
	if (health.healthy) return health.running_now ? 'busy' : 'good';
	return health.failures ? 'bad' : 'idle';
}

/**
 * A transcript line's speaker, as something to label.
 *
 * The `builder` role is the one that matters: those words were written by a
 * different AI, on somebody else's machine, and the page should say so rather
 * than letting them read as Jarvis's own.
 */
export function describeSpeaker(role: string): string {
	switch (role) {
		case 'builder':
			return "n8n's builder";
		case 'tool':
			return 'it ran';
		case 'you':
			return 'you';
		default:
			return role || '—';
	}
}
