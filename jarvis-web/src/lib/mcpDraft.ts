/**
 * Turning what someone typed into an MCP server jarvis-core will accept.
 *
 * Same shape as `toolDraft.ts`: the checks mirror `jarvis/integrations/mcp/`
 * so a mistake is caught with the cursor still in the field, and the server
 * re-runs every one of them regardless — this is a convenience, never the
 * control.
 *
 * ## The two things this file is careful about
 *
 * **The name becomes part of every tool's name.** jarvis-core namespaces an
 * MCP tool as `mcp_<server>_<tool>` so that a server offering `control_device`
 * cannot shadow the real one. `safeName` is that same normalisation, run here
 * so the form can SHOW what the tools will be called before anything is saved
 * — a name silently becoming something else is how somebody ends up with two
 * servers they cannot tell apart.
 *
 * **stdio is not a transport choice, it is a decision about the host.** An http
 * server is a URL jarvis-core fetches; a stdio server is a program it starts.
 * The console cannot enable that — `allow_stdio` lives in configuration.yaml
 * precisely so no request can — so the form's job is to say WHY the fields are
 * closed rather than to accept the input and let the server refuse it.
 */

/** Mirrors `safe_server_name` in `jarvis/integrations/mcp/catalog.py`. */
export const MAX_NAME_CHARS = 48;

export type Transport = 'http' | 'stdio';

export interface McpTool {
	name: string;
	remote_name: string;
	description: string;
}

/** One server as `jarvis/mcp/list` reports it. Never carries the token. */
export interface McpServer {
	name: string;
	transport: Transport;
	url: string;
	command: string;
	args: string[];
	tier: number;
	enabled: boolean;
	/** False for servers written in configuration.yaml — the file owns those. */
	editable: boolean;
	has_token: boolean;
	connected: boolean;
	error: string;
	tools: McpTool[];
	tool_count: number;
	server_info?: Record<string, unknown>;
}

export interface McpForm {
	name: string;
	transport: Transport;
	url: string;
	token: string;
	command: string;
	/** One argument per line, which is how people actually paste them. */
	args: string;
	tier: string;
}

export type McpResult =
	| { ok: true; payload: Record<string, unknown> }
	| { ok: false; error: string; field: keyof McpForm };

export function blankMcpForm(): McpForm {
	return {
		name: '',
		transport: 'http',
		url: '',
		token: '',
		command: '',
		args: '',
		// Two, matching jarvis-core's own default: an MCP tool is third-party
		// code with side effects nothing in the house can see, and Tier 1 means
		// "run it and answer".
		tier: '2'
	};
}

/**
 * The name jarvis-core will actually use.
 *
 * Mirrors `safe_server_name`: lowercase, `[a-z0-9_]`, runs of anything else
 * collapsed to one underscore, trimmed, capped.
 */
export function safeName(raw: string): string {
	return String(raw ?? '')
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9_]+/g, '_')
		.replace(/^_+|_+$/g, '')
		.slice(0, MAX_NAME_CHARS);
}

/** What a tool from this server will be called, for the form to show. */
export function toolNamePreview(serverName: string, toolName = 'search'): string {
	const server = safeName(serverName);
	if (!server) return '';
	return `mcp_${server}_${safeName(toolName) || 'tool'}`;
}

export function parseMcpForm(form: McpForm, opts: { allowStdio: boolean }): McpResult {
	const name = safeName(form.name);
	if (!name) {
		return { ok: false, error: 'A name is needed; it becomes part of every tool’s name.', field: 'name' };
	}

	const tier = Number(form.tier);
	if (!Number.isInteger(tier) || tier < 1 || tier > 3) {
		return { ok: false, error: 'Tier is 1, 2 or 3.', field: 'tier' };
	}

	if (form.transport === 'stdio') {
		if (!opts.allowStdio) {
			// Said here rather than left to the server, because the fix is in a
			// file on the Jarvis host and nobody would guess that from a 400.
			return {
				ok: false,
				error:
					'A stdio server runs a program on the Jarvis host. Set `mcp: allow_stdio: true` in configuration.yaml first — deliberately not something this page can turn on.',
				field: 'command'
			};
		}
		const command = form.command.trim();
		if (!command) {
			return { ok: false, error: 'Which program should it run?', field: 'command' };
		}
		return {
			ok: true,
			payload: {
				name,
				transport: 'stdio',
				command,
				args: splitArgs(form.args),
				tier
			}
		};
	}

	const url = form.url.trim();
	if (!url) {
		return { ok: false, error: 'An http MCP server needs a URL.', field: 'url' };
	}
	if (!/^https?:\/\//i.test(url)) {
		return { ok: false, error: 'The URL needs to start with http:// or https://.', field: 'url' };
	}

	const payload: Record<string, unknown> = { name, transport: 'http', url, tier };
	// Only when there is one: sending `token: ""` over an edit would clear a
	// token the console was never shown and cannot retype.
	if (form.token.trim()) payload.token = form.token.trim();
	return { ok: true, payload };
}

/** One argument per line, blanks dropped. */
export function splitArgs(raw: string): string[] {
	return String(raw ?? '')
		.split('\n')
		.map((line) => line.trim())
		.filter(Boolean)
		.slice(0, 32);
}

export function tierLabel(tier: number): string {
	switch (tier) {
		case 1:
			return 'RUNS';
		case 2:
			return 'CONFIRMS';
		case 3:
			return 'ASKS';
		default:
			return `TIER ${tier}`;
	}
}

/** One line under a server's name: what it is and how it is doing. */
export function describeServer(server: McpServer): string {
	if (!server.enabled) return 'disabled';
	if (server.error) return server.error;
	if (!server.connected) return 'not connected';
	const count = server.tool_count;
	return `${count} tool${count === 1 ? '' : 's'} · ${tierLabel(server.tier).toLowerCase()}`;
}

/**
 * Why a server's row has no buttons, or "" when it has.
 *
 * The same reasoning as `toolReadOnlyNote`: a disabled control with no
 * explanation reads as a bug.
 */
export function readOnlyNote(server: McpServer): string {
	if (server.editable) return '';
	return 'defined in configuration.yaml — edit it there';
}
