import { describe, expect, it } from 'vitest';
import {
	blankMcpForm,
	describeServer,
	parseMcpForm,
	readOnlyNote,
	safeName,
	splitArgs,
	tierLabel,
	toolNamePreview,
	type McpServer
} from './mcpDraft';

function server(over: Partial<McpServer> = {}): McpServer {
	return {
		name: 'notes',
		transport: 'http',
		url: 'http://s/mcp',
		command: '',
		args: [],
		tier: 2,
		enabled: true,
		editable: true,
		has_token: false,
		connected: true,
		error: '',
		tools: [],
		tool_count: 2,
		...over
	};
}

describe('the name, which becomes part of every tool’s name', () => {
	it('normalises the way jarvis-core does', () => {
		// Mirrors `safe_server_name`. Drifting from it means the form shows one
		// name and the tools get another.
		expect(safeName('My Notes!')).toBe('my_notes');
		expect(safeName('  --a--b-- ')).toBe('a_b');
		expect(safeName('ALREADY_fine')).toBe('already_fine');
		expect(safeName('x'.repeat(200)).length).toBeLessThanOrEqual(48);
	});

	it('shows what a tool will actually be called', () => {
		// So somebody can see the namespacing before they save, rather than
		// wondering later why the model calls it something else.
		expect(toolNamePreview('My Notes')).toBe('mcp_my_notes_search');
		expect(toolNamePreview('notes', 'Read File')).toBe('mcp_notes_read_file');
		expect(toolNamePreview('!!!')).toBe('');
	});

	it('refuses a name that would normalise to nothing', () => {
		const form = { ...blankMcpForm(), name: '???', url: 'http://s/mcp' };
		const result = parseMcpForm(form, { allowStdio: false });
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('name');
	});
});

describe('an http server', () => {
	it('takes a url and sends the tier as a number', () => {
		const form = { ...blankMcpForm(), name: 'notes', url: 'https://s/mcp', tier: '3' };
		const result = parseMcpForm(form, { allowStdio: false });
		expect(result.ok).toBe(true);
		if (result.ok) {
			expect(result.payload).toMatchObject({
				name: 'notes',
				transport: 'http',
				url: 'https://s/mcp',
				tier: 3
			});
		}
	});

	it('needs a url', () => {
		const result = parseMcpForm({ ...blankMcpForm(), name: 'n' }, { allowStdio: false });
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('url');
	});

	it('needs a url that is one', () => {
		const form = { ...blankMcpForm(), name: 'n', url: 'notes.local/mcp' };
		const result = parseMcpForm(form, { allowStdio: false });
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.error).toMatch(/http/);
	});

	it('omits the token entirely when the field is blank', () => {
		// Sending `token: ""` on an edit would clear a token the console was
		// never shown and cannot retype.
		const form = { ...blankMcpForm(), name: 'n', url: 'http://s/mcp' };
		const result = parseMcpForm(form, { allowStdio: false });
		if (result.ok) expect('token' in result.payload).toBe(false);
	});

	it('sends the token when there is one', () => {
		const form = { ...blankMcpForm(), name: 'n', url: 'http://s/mcp', token: '  t  ' };
		const result = parseMcpForm(form, { allowStdio: false });
		if (result.ok) expect(result.payload.token).toBe('t');
	});
});

describe('a stdio server', () => {
	it('is refused with the actual fix, not a generic error', () => {
		// The fix is a line in a file on the Jarvis host. Nobody would guess
		// that from a 400, and the server cannot tell them because by then the
		// form has already been filled in.
		const form = { ...blankMcpForm(), name: 'files', transport: 'stdio' as const, command: 'npx' };
		const result = parseMcpForm(form, { allowStdio: false });
		expect(result.ok).toBe(false);
		if (!result.ok) {
			expect(result.error).toMatch(/allow_stdio/);
			expect(result.error).toMatch(/configuration\.yaml/);
		}
	});

	it('is accepted once the operator has said so in the file', () => {
		const form = {
			...blankMcpForm(),
			name: 'files',
			transport: 'stdio' as const,
			command: 'npx',
			args: '-y\n@modelcontextprotocol/server-filesystem\n/srv/share',
			tier: '3'
		};
		const result = parseMcpForm(form, { allowStdio: true });
		expect(result.ok).toBe(true);
		if (result.ok) {
			expect(result.payload).toMatchObject({
				transport: 'stdio',
				command: 'npx',
				args: ['-y', '@modelcontextprotocol/server-filesystem', '/srv/share'],
				tier: 3
			});
		}
	});

	it('needs a command', () => {
		const form = { ...blankMcpForm(), name: 'x', transport: 'stdio' as const };
		const result = parseMcpForm(form, { allowStdio: true });
		expect(result.ok).toBe(false);
		if (!result.ok) expect(result.field).toBe('command');
	});

	it('splits arguments one per line and drops the blanks', () => {
		expect(splitArgs('-y\n\n  pkg  \n')).toEqual(['-y', 'pkg']);
		expect(splitArgs('')).toEqual([]);
		expect(splitArgs(Array(100).fill('a').join('\n')).length).toBe(32);
	});
});

describe('the tier', () => {
	it('defaults to confirming rather than running', () => {
		// jarvis-core's own default, and for its reason: an MCP tool is
		// third-party code with side effects nothing here can see.
		expect(blankMcpForm().tier).toBe('2');
	});

	it('refuses anything that is not one of the three', () => {
		for (const tier of ['0', '4', 'x', '2.5']) {
			const form = { ...blankMcpForm(), name: 'n', url: 'http://s/mcp', tier };
			expect(parseMcpForm(form, { allowStdio: false }).ok).toBe(false);
		}
	});

	it('says what each tier means in one word', () => {
		expect(tierLabel(1)).toBe('RUNS');
		expect(tierLabel(2)).toBe('CONFIRMS');
		expect(tierLabel(3)).toBe('ASKS');
	});
});

describe('what a row says', () => {
	it('leads with the reason when a server is down', () => {
		expect(describeServer(server({ connected: false, error: 'no route to host' }))).toBe(
			'no route to host'
		);
	});

	it('counts tools when it is up', () => {
		expect(describeServer(server({ tool_count: 1 }))).toBe('1 tool · confirms');
		expect(describeServer(server({ tool_count: 4, tier: 3 }))).toBe('4 tools · asks');
	});

	it('says disabled before anything else', () => {
		expect(describeServer(server({ enabled: false, error: 'x' }))).toBe('disabled');
	});

	it('explains a row with no buttons rather than leaving it inert', () => {
		expect(readOnlyNote(server({ editable: false }))).toMatch(/configuration\.yaml/);
		expect(readOnlyNote(server({ editable: true }))).toBe('');
	});
});
