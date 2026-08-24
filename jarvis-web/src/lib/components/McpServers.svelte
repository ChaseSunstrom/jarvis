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
	 * Node. This file owns the buttons.
	 */
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import { isUnsupported, type McpListing } from '$lib/jarvisClient';
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

	let { conn }: { conn: Connection | null } = $props();

	let servers = $state<McpServer[]>([]);
	let allowStdio = $state(false);
	let supported = $state(true);
	let loaded = $state(false);
	let busy = $state('');
	let err = $state('');
	let adding = $state(false);
	let form = $state<McpForm>(blankMcpForm());
	let expanded = $state('');

	const preview = $derived(toolNamePreview(form.name));
	const normalised = $derived(safeName(form.name));

	function take(listing: McpListing | null | undefined): void {
		servers = listing?.servers ?? [];
		allowStdio = Boolean(listing?.allow_stdio);
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

	async function add(): Promise<void> {
		if (!conn || busy) return;
		err = '';
		const parsed = parseMcpForm(form, { allowStdio });
		if (!parsed.ok) {
			err = parsed.error;
			document.getElementById(`mcp-${parsed.field}`)?.focus();
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
		} catch (e) {
			err = describeError(e);
		} finally {
			busy = '';
		}
	}
</script>

{#if supported}
	<section class="panel" data-testid="mcp-panel">
		<div class="panel-head">
			<span>MCP servers</span>
			<span class="muted">
				{servers.length} configured{allowStdio ? ' · stdio allowed' : ''}
			</span>
		</div>

		<p class="muted lede">
			Any MCP server can lend Jarvis its tools. They arrive named
			<code>mcp_&lt;server&gt;_&lt;tool&gt;</code> so nothing can shadow a built-in, everything they
			return is treated as untrusted text, and they need confirming before they run unless you say
			otherwise.
		</p>

		{#if err}<p class="err" role="alert" data-testid="mcp-error">{err}</p>{/if}

		{#if loaded && !servers.length && !adding}
			<p class="muted" data-testid="mcp-empty">No MCP servers yet.</p>
		{/if}

		<ul class="list">
			{#each servers as server (server.name)}
				<li data-testid="mcp-row-{server.name}" data-connected={server.connected}>
					<div class="row head">
						<span class="name">
							<b>{server.name}</b>
							<span class="eid" data-testid="mcp-detail-{server.name}">
								{describeServer(server)}
							</span>
						</span>
						<span class="acts">
							<span class="pill" data-tier={server.tier}>{tierLabel(server.tier)}</span>
							{#if server.tools.length}
								<button
									type="button"
									class="btn ghost"
									data-testid="mcp-tools-{server.name}"
									aria-expanded={expanded === server.name}
									onclick={() => (expanded = expanded === server.name ? '' : server.name)}
								>
									{server.tool_count} TOOLS
								</button>
							{/if}
							<button
								type="button"
								class="btn ghost"
								data-testid="mcp-reconnect-{server.name}"
								disabled={!!busy}
								onclick={() => reconnect(server.name)}
							>
								{busy === server.name ? '…' : 'RECONNECT'}
							</button>
							{#if server.editable}
								<button
									type="button"
									class="btn ghost danger"
									data-testid="mcp-remove-{server.name}"
									disabled={!!busy}
									onclick={() => remove(server)}
								>
									REMOVE
								</button>
							{:else}
								<span class="eid" data-testid="mcp-readonly-{server.name}">
									{readOnlyNote(server)}
								</span>
							{/if}
						</span>
					</div>
					{#if expanded === server.name}
						<ul class="tools" data-testid="mcp-tool-list-{server.name}">
							{#each server.tools as tool (tool.name)}
								<li><code>{tool.name}</code> <span class="eid">{tool.description}</span></li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
		</ul>

		<div class="toolbar">
			<button
				type="button"
				class="btn"
				data-testid="mcp-new"
				aria-expanded={adding}
				onclick={() => {
					adding = !adding;
					err = '';
				}}
			>
				{adding ? 'CANCEL' : '+ ADD SERVER'}
			</button>
			{#if servers.length}
				<button
					type="button"
					class="btn ghost"
					data-testid="mcp-reconnect-all"
					disabled={!!busy}
					onclick={() => reconnect()}
				>
					RECONNECT ALL
				</button>
			{/if}
		</div>

		{#if adding}
			<div class="editor" data-testid="mcp-editor">
				<label for="mcp-name">Name</label>
				<input id="mcp-name" type="text" bind:value={form.name} placeholder="nextcloud" />
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

				<label for="mcp-transport">Transport</label>
				<select id="mcp-transport" bind:value={form.transport} data-testid="mcp-transport">
					<option value="http">http — a URL Jarvis fetches</option>
					<option value="stdio" disabled={!allowStdio}>
						stdio — a program Jarvis starts{allowStdio ? '' : ' (not allowed)'}
					</option>
				</select>
				{#if !allowStdio}
					<p class="hint" data-testid="mcp-stdio-note">
						A stdio server runs a program on the Jarvis host. Turn it on with
						<code>mcp: allow_stdio: true</code> in <code>configuration.yaml</code> — deliberately
						not something this page can do.
					</p>
				{/if}

				{#if form.transport === 'stdio'}
					<label for="mcp-command">Command</label>
					<input id="mcp-command" type="text" bind:value={form.command} placeholder="npx" />
					<label for="mcp-args">Arguments, one per line</label>
					<textarea id="mcp-args" rows="3" bind:value={form.args}></textarea>
				{:else}
					<label for="mcp-url">URL</label>
					<input
						id="mcp-url"
						type="text"
						bind:value={form.url}
						placeholder="http://127.0.0.1:9100/mcp"
					/>
					<label for="mcp-token">Token (optional)</label>
					<input id="mcp-token" type="password" bind:value={form.token} autocomplete="off" />
				{/if}

				<label for="mcp-tier">When it may run</label>
				<select id="mcp-tier" bind:value={form.tier} data-testid="mcp-tier">
					<option value="1">1 — run it and answer</option>
					<option value="2">2 — confirm first</option>
					<option value="3">3 — never without a yes</option>
				</select>

				<div class="row">
					<button
						type="button"
						class="btn"
						data-testid="mcp-save"
						disabled={busy === 'add'}
						onclick={add}
					>
						{busy === 'add' ? 'CONNECTING…' : 'ADD'}
					</button>
				</div>
			</div>
		{/if}
	</section>
{/if}

<style>
	/* Only what the shared chrome does not provide — `.panel`, `.row`, `.btn`,
	   `.name`, `.eid`, `.muted`, `.err` and `.editor` all come from chrome.css. */
	.lede {
		margin: 0 0 var(--jv-space-2);
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.list > li {
		border-bottom: 1px dashed var(--jv-line-hair);
	}
	.list > li:last-child {
		border-bottom: 0;
	}
	.acts {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.pill {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
		padding: 1px var(--jv-space-2);
		color: var(--jv-text-dim);
	}
	.pill[data-tier='1'] {
		color: var(--jv-warn);
		border-color: var(--jv-warn);
	}
	.pill[data-tier='3'] {
		color: var(--jv-ok);
	}
	li[data-connected='false'] .name b {
		color: var(--jv-danger-text);
	}
	.tools {
		list-style: none;
		margin: 0 0 var(--jv-space-2);
		padding: 0 0 0 var(--jv-space-3);
	}
	.tools li {
		display: flex;
		gap: var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		padding: var(--jv-rule-live) 0;
	}
	.hint {
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.toolbar {
		display: flex;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-2);
	}
</style>
