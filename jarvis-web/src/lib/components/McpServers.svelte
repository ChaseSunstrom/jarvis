<script lang="ts">
	/**
	 * Adding somebody else's tools to Jarvis, from the console.
	 *
	 * An MCP server lends Jarvis its tools; this is where you point at one.
	 * jarvis-core namespaces everything it offers as `mcp_<server>_<tool>` so a
	 * server cannot shadow a built-in, fences everything it returns, and
	 * registers it at Tier 2 unless told otherwise.
	 *
	 * ## The two things this panel is really for
	 *
	 * **Saying what a server will become before you add it.** The tool-name
	 * preview is not decoration: the name is normalised (`My Notes` becomes
	 * `my_notes`), and finding that out afterwards, from a tool the model calls
	 * by a name you did not choose, is the confusing version.
	 *
	 * **Explaining the closed door rather than hiding it.** A stdio server runs
	 * a program on the Jarvis host, and this page cannot turn that on — the
	 * switch is in `configuration.yaml` precisely so no request can. So the
	 * option is visible and disabled with the reason attached, because an
	 * option that is simply absent reads as a missing feature.
	 *
	 * Everything about validation lives in `$lib/mcpDraft.ts` and is tested in
	 * Node. This file owns the buttons. It draws no panel of its own: the tools
	 * page puts it behind a disclosure whose header carries the count this
	 * reports through `count`.
	 */
	import { Button, Field, Input, Pill, Select } from '$lib/ui';
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import { isUnsupported, type McpListing, type McpServerDetail } from '$lib/jarvisClient';
	import { toasts } from '$lib/toast';
	import {
		blankMcpForm,
		describeServer,
		parseMcpForm,
		readOnlyNote,
		safeName,
		tierLabel,
		toolNamePreview,
		type McpForm,
		type McpServer
	} from '$lib/mcpDraft';

	let { conn, count = $bindable(0) }: { conn: Connection | null; count?: number } = $props();

	let servers = $state<McpServer[]>([]);
	let allowStdio = $state(false);
	let supported = $state(true);
	let loaded = $state(false);
	let busy = $state('');
	let err = $state('');
	let adding = $state(false);
	let form = $state<McpForm>(blankMcpForm());
	let expanded = $state('');
	/** name -> the inspect payload, once it has been asked for. */
	let detail = $state<Record<string, McpServerDetail>>({});
	/** tool name -> what a test call returned, so the answer sits under it. */
	let tried = $state<Record<string, string>>({});

	const preview = $derived(toolNamePreview(form.name));
	const normalised = $derived(safeName(form.name));

	function take(listing: McpListing | null | undefined): void {
		servers = listing?.servers ?? [];
		allowStdio = Boolean(listing?.allow_stdio);
		count = servers.length;
	}

	async function refresh(connection: Connection): Promise<void> {
		try {
			take(await connection.client.listMcpServers());
			supported = true;
		} catch (e) {
			// The versioning rule: an older jarvis-core has no MCP integration,
			// and the panel simply is not drawn rather than showing a fault.
			if (isUnsupported(e)) supported = false;
			else err = describeError(e);
		} finally {
			loaded = true;
		}
	}

	$effect(() => {
		const connection = conn;
		if (!connection) return;
		void refresh(connection);
	});

	async function inspect(name: string, force = false): Promise<void> {
		if (!force && expanded === name) {
			expanded = '';
			return;
		}
		expanded = name;
		if (!conn) return;
		// Always re-read, keeping whatever is on screen until the answer
		// arrives. Cached-forever was wrong in the one case the view is for:
		// press RECONNECT after fixing a server and the panel went on showing
		// the error it had failed with, and no tools.
		try {
			const answer = await conn.client.inspectMcpServer(name);
			detail = { ...detail, [name]: answer.server };
		} catch (e) {
			err = describeError(e);
		}
	}

	/**
	 * Call one of the server's tools, from here, to find out whether it works.
	 *
	 * Through `jarvis/tools/call`, which is the SAME path and the same approval
	 * gate the model goes through — so a Tier-3 tool tested from this button is
	 * held for a human exactly as it would be mid-conversation. A second,
	 * console-only execution path would be a way around the gate, and the whole
	 * argument for the gate is that there is only one.
	 *
	 * No arguments: a test call is "is this server answering", not a form for
	 * composing a real one. A tool that needs arguments will say so, and that
	 * answer is the diagnosis.
	 */
	async function tryTool(toolName: string): Promise<void> {
		if (!conn || busy) return;
		busy = toolName;
		try {
			const result = await conn.client.callTool(toolName, {});
			tried = { ...tried, [toolName]: JSON.stringify(result).slice(0, 400) };
		} catch (e) {
			tried = { ...tried, [toolName]: describeError(e) };
		} finally {
			busy = '';
		}
	}

	async function add(): Promise<void> {
		if (!conn || busy) return;
		err = '';
		const parsed = parseMcpForm(form, { allowStdio });
		if (!parsed.ok) {
			err = parsed.error;
			document.querySelector<HTMLElement>(`[data-testid="mcp-${parsed.field}"]`)?.focus();
			return;
		}
		busy = 'add';
		try {
			take(await conn.client.addMcpServer(parsed.payload));
			const added = servers.find((s) => s.name === parsed.payload.name);
			if (added?.connected) {
				toasts.success(`${added.name} connected`, `${added.tool_count} tool(s) available`);
			} else {
				// Added but not reachable is a real outcome, not a failure: the
				// server may be starting. Saying so beats a green tick.
				toasts.info(`${String(parsed.payload.name)} was added but is not answering`, added?.error);
			}
			form = blankMcpForm();
			adding = false;
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function remove(server: McpServer): Promise<void> {
		if (!conn || busy) return;
		busy = server.name;
		err = '';
		try {
			take(await conn.client.removeMcpServer(server.name));
			toasts.success(`Forgot ${server.name}`, 'its tools are no longer offered');
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	async function reconnect(name = ''): Promise<void> {
		if (!conn || busy) return;
		busy = name || 'all';
		err = '';
		try {
			take(await conn.client.reconnectMcp(name));
			// An open inspect panel is now stale by definition.
			if (expanded) void inspect(expanded, true);
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}

	/** The tier tag's tone: a tier-1 server runs unasked, which is worth a colour. */
	const tierTone = (tier: number | string | undefined): 'warn' | 'ok' | 'neutral' =>
		String(tier) === '1' ? 'warn' : String(tier) === '3' ? 'ok' : 'neutral';
</script>

{#if supported}
	<div class="mcp" data-testid="mcp-panel">
		<p class="note">
			Any MCP server can lend Jarvis its tools. They arrive named
			<code>mcp_&lt;server&gt;_&lt;tool&gt;</code> so nothing can shadow a built-in, everything they
			return is treated as untrusted text, and they need confirming before they run unless you say
			otherwise.{allowStdio ? ' stdio servers are allowed on this host.' : ''}
		</p>

		{#if err}<p class="bad" role="alert" data-testid="mcp-error">{err}</p>{/if}

		{#if loaded && !servers.length && !adding}
			<p class="note" data-testid="mcp-empty">No MCP servers yet.</p>
		{/if}

		<ul class="list">
			{#each servers as server (server.name)}
				<li data-testid="mcp-row-{server.name}" data-connected={server.connected}>
					<div class="server">
						<div class="what">
							<b class:down={!server.connected}>{server.name}</b>
							<span class="dim" data-testid="mcp-detail-{server.name}">{describeServer(server)}</span>
						</div>
						<div class="acts">
							<Pill tone={tierTone(server.tier)}>{tierLabel(server.tier)}</Pill>
							<Button
								testid="mcp-tools-{server.name}"
								aria-expanded={expanded === server.name}
								title="What this server answers, and its tools' arguments"
								onclick={() => inspect(server.name)}
							>
								{server.tools.length ? `${server.tool_count} TOOLS` : 'INSPECT'}
							</Button>
							<Button
								testid="mcp-reconnect-{server.name}"
								disabled={!!busy}
								title={busy ? 'Working' : 'Dial it again'}
								onclick={() => reconnect(server.name)}
							>
								{busy === server.name ? '…' : 'RECONNECT'}
							</Button>
							{#if server.editable}
								<Button
									variant="danger"
									testid="mcp-remove-{server.name}"
									disabled={!!busy}
									title={busy ? 'Working' : 'Forget it; its tools are no longer offered'}
									onclick={() => remove(server)}
								>
									REMOVE
								</Button>
							{:else}
								<span class="dim" data-testid="mcp-readonly-{server.name}">{readOnlyNote(server)}</span>
							{/if}
						</div>
					</div>
					{#if expanded === server.name}
						<div class="inspect" data-testid="mcp-inspect-{server.name}">
							{#if detail[server.name]}
								<dl class="facts">
									<dt>protocol</dt>
									<dd data-testid="mcp-protocol-{server.name}">
										{detail[server.name].protocol_version || 'unknown'}
									</dd>
									<dt>server</dt>
									<dd>
										{String(
											detail[server.name].server_info?.name ??
												detail[server.name].server_info?.title ??
												'—'
										)}
									</dd>
									{#if detail[server.name].last_error}
										<dt>last error</dt>
										<dd class="bad-text" data-testid="mcp-last-error-{server.name}">
											{detail[server.name].last_error}
											{#if detail[server.name].next_attempt_in > 0}
												· retrying in {Math.round(detail[server.name].next_attempt_in)}s
											{/if}
										</dd>
									{/if}
								</dl>
							{/if}
							<ul class="tools" data-testid="mcp-tool-list-{server.name}">
								{#each detail[server.name]?.tools ?? server.tools as tool (tool.name)}
									<li>
										<div class="tool">
											<code>{tool.name}</code>
											<span class="dim">{tool.description}</span>
											<Button
												testid="mcp-try-{tool.name}"
												disabled={!!busy}
												onclick={() => tryTool(tool.name)}
												title="Call it through the same approval gate the assistant uses"
											>
												{busy === tool.name ? '…' : 'TEST CALL'}
											</Button>
										</div>
										{#if 'parameters' in tool && tool.parameters}
											<pre class="schema" data-testid="mcp-schema-{tool.name}">{JSON.stringify(
													tool.parameters,
													null,
													1
												)}</pre>
										{/if}
										{#if tried[tool.name]}
											<pre class="result" data-testid="mcp-result-{tool.name}">{tried[tool.name]}</pre>
										{/if}
									</li>
								{/each}
							</ul>
						</div>
					{/if}
				</li>
			{/each}
		</ul>

		<div class="foot">
			<Button
				testid="mcp-new"
				aria-expanded={adding}
				title={adding ? 'Close the form' : 'Point Jarvis at a server'}
				onclick={() => {
					adding = !adding;
					err = '';
				}}
			>
				{adding ? 'CANCEL' : '+ ADD SERVER'}
			</Button>
			{#if servers.length}
				<Button testid="mcp-reconnect-all" disabled={!!busy} title={busy ? 'Working' : 'Dial every server again'} onclick={() => reconnect()}>
					RECONNECT ALL
				</Button>
			{/if}
		</div>

		{#if adding}
			<div class="editor" data-testid="mcp-editor">
				<Field label="Name">
					<Input bind:value={form.name} testid="mcp-name" placeholder="nextcloud" mono />
				</Field>
				{#if form.name && normalised !== form.name.trim()}
					<!-- Shown BEFORE saving. Finding out afterwards, from a tool the
					     model calls by a name you did not choose, is the confusing
					     version of this. -->
					<p class="hint" data-testid="mcp-name-normalised">
						will be saved as <code>{normalised || '(nothing usable)'}</code>
					</p>
				{/if}
				{#if preview}
					<p class="hint" data-testid="mcp-preview">
						its tools will be named like <code>{preview}</code>
					</p>
				{/if}

				<!--
				  A raw select, because one of its options has to be DISABLED with
				  its reason attached, and the library's Select has no per-option
				  state. Drawn as Select is. Labelled with `for`, not wrapped in the
				  label: a state check on an option inside a wrapping label is
				  retargeted to the select, which is not disabled.
				-->
				<div class="field">
					<label class="label" for="mcp-transport">Transport</label>
					<select id="mcp-transport" class="sel" bind:value={form.transport} data-testid="mcp-transport">
						<option value="http">http — a URL Jarvis fetches</option>
						<option value="stdio" disabled={!allowStdio}>
							stdio — a program Jarvis starts{allowStdio ? '' : ' (not allowed)'}
						</option>
					</select>
				</div>
				{#if !allowStdio}
					<p class="hint" data-testid="mcp-stdio-note">
						A stdio server runs a program on the Jarvis host. Turn it on with
						<code>mcp: allow_stdio: true</code> in <code>configuration.yaml</code> — deliberately
						not something this page can do.
					</p>
				{/if}

				{#if form.transport === 'stdio'}
					<Field label="Command">
						<Input bind:value={form.command} testid="mcp-command" placeholder="npx" mono />
					</Field>
					<Field label="Arguments, one per line">
						<Input bind:value={form.args} testid="mcp-args" rows={3} mono />
					</Field>
				{:else}
					<Field label="URL">
						<Input bind:value={form.url} testid="mcp-url" placeholder="http://127.0.0.1:9100/mcp" mono />
					</Field>
					<!-- A password field: the library's Input has no `type`, and a
					     token must not be a text one. Drawn as Input is. -->
					<div class="field">
						<label class="label" for="mcp-token">Token (optional)</label>
						<input id="mcp-token" class="in" type="password" bind:value={form.token} data-testid="mcp-token" autocomplete="off" />
					</div>
				{/if}

				<Field label="When it may run">
					<Select
						bind:value={form.tier}
						testid="mcp-tier"
						options={[
							{ value: '1', label: '1 — run it and answer' },
							{ value: '2', label: '2 — confirm first' },
							{ value: '3', label: '3 — never without a yes' }
						]}
					/>
				</Field>

				<div class="editor-acts">
					<Button variant="primary" testid="mcp-save" disabled={busy === 'add'} title={busy === 'add' ? 'Connecting' : 'Add it and dial it'} onclick={add}>
						{busy === 'add' ? 'CONNECTING…' : 'ADD'}
					</Button>
				</div>
			</div>
		{/if}
	</div>
{/if}

<style>
	.note,
	.hint {
		margin: 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 70ch;
	}
	.note {
		margin-bottom: var(--jv-space-3);
	}
	.hint {
		color: var(--jv-text-faint);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	.bad {
		margin: 0;
		padding: var(--jv-space-2) 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.list > li {
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.list > li:last-child {
		border-bottom: 0;
	}
	.server {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-4);
		flex-wrap: wrap;
		padding: var(--jv-space-3) 0;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		flex: 1 1 16rem;
		min-width: 0;
	}
	/* A server's name is what the model's tools are prefixed with: an id, so mono. */
	.what b {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
	}
	.what b.down {
		color: var(--jv-danger-text);
	}
	.dim {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	.acts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.inspect {
		padding: 0 0 var(--jv-space-3) var(--jv-space-3);
		border-left: 1px solid var(--jv-line-soft);
		margin-bottom: var(--jv-space-3);
	}
	.facts {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: var(--jv-space-1) var(--jv-space-3);
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.facts dt {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	.facts dd {
		margin: 0;
	}
	.bad-text {
		color: var(--jv-danger-text);
	}
	.tools {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.tools > li {
		padding: var(--jv-space-2) 0;
		border-top: 1px solid var(--jv-line-hair);
	}
	.tool {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
	}
	.tool .dim {
		flex: 1 1 12rem;
	}
	.schema,
	.result {
		margin: var(--jv-space-2) 0 0;
		padding: var(--jv-space-2) var(--jv-space-3);
		background: var(--jv-surface-sunken);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.6;
		max-height: var(--jv-measure-log);
		overflow: auto;
		white-space: pre-wrap;
	}
	.foot {
		display: flex;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
		padding-top: var(--jv-space-3);
	}
	/* The form, inset from the list it adds to. */
	.editor {
		display: grid;
		gap: var(--jv-space-3);
		margin-top: var(--jv-space-3);
		padding: var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-left: var(--jv-rule-live) solid var(--jv-accent);
		border-radius: var(--jv-radius-md);
		background: var(--jv-bg-raised);
	}
	.field {
		display: grid;
		gap: var(--jv-space-1);
	}
	.label {
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.sel,
	.in {
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
		width: 100%;
	}
	.in {
		font-family: var(--jv-font-chrome);
	}
	.sel:hover,
	.in:hover {
		border-color: var(--jv-line);
	}
	.editor-acts {
		display: flex;
		gap: var(--jv-space-2);
	}
</style>
