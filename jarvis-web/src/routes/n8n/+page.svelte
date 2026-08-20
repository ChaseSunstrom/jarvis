<script lang="ts">
	/**
	 * n8n — the workflows Jarvis writes for other people's services.
	 *
	 * ## The one thing this page is really for
	 *
	 * Telling you what to connect. Jarvis writes a workflow and strips the
	 * credential id the model guessed, because that id points at nothing, at
	 * the wrong account, or at an account the request had no business
	 * reaching. What is left is a workflow that cannot run and a list of what
	 * it asked for — and that list is useless in a log and useful here, next
	 * to the workflow, with the node named.
	 *
	 * ## What this page does not decide
	 *
	 * Whether Jarvis may write a workflow (Tier 3, always), whether it may
	 * switch one on (`allow_activate:`, and off by default), and whether a
	 * credential ever reaches the model (it does not). All three are
	 * jarvis-core's and are enforced there. The ACTIVATE button here is a
	 * person pressing it, which is the human that flag exists to insist on —
	 * so it works regardless.
	 */
	import { onMount } from 'svelte';
	import Reconnect from '$lib/components/Reconnect.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import {
		canUseBuilder,
		capabilityLines,
		describeActive,
		describeConnections,
		describeInstance,
		describeSpeaker,
		healthTone,
		newestFirst,
		runTone,
		whyNoBuilder,
		type N8nCheck,
		type N8nExecution,
		type N8nGraph,
		type N8nHealth,
		type N8nInstance,
		type N8nTranscriptLine,
		type N8nWorkflow
	} from '$lib/n8n';
	import { openConnection, describeError, type Connection } from '$lib/connection';
	import { isUnsupported } from '$lib/jarvisClient';
	import { staggerStyle } from '$lib/motion';
	import { toasts } from '$lib/toast';

	let conn = $state<Connection | null>(null);
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');
	let loading = $state(true);
	let redialling = $state(false);
	let instance = $state<N8nInstance | null>(null);
	let workflows = $state<N8nWorkflow[]>([]);
	let check = $state<N8nCheck | null>(null);
	let checking = $state(false);
	let opened = $state('');
	let graph = $state<N8nGraph | null>(null);
	let runs = $state<N8nExecution[]>([]);
	let busy = $state('');
	let health = $state<N8nHealth | null>(null);
	let checkingHealth = $state(false);
	let instruction = $state('');
	let building = $state(false);
	let buildTask = $state('');
	let transcript = $state<N8nTranscriptLine[]>([]);

	const configured = $derived(!!instance?.configured);
	const layers = $derived(capabilityLines(check));
	const builderReady = $derived(canUseBuilder(check));
	const builderProblem = $derived(whyNoBuilder(check));
	const needed = $derived(graph?.connections_needed ?? []);
	const connectLine = $derived(describeConnections(needed));

	async function load(connection: Connection): Promise<void> {
		const listing = await connection.client.listN8nWorkflows();
		workflows = listing.workflows ?? [];
		instance = listing.instance ?? null;
	}

	async function open(id: string): Promise<void> {
		if (!conn) return;
		if (opened === id) {
			opened = '';
			graph = null;
			runs = [];
			return;
		}
		opened = id;
		graph = null;
		runs = [];
		health = null;
		try {
			graph = (await conn.client.getN8nWorkflow(id)).workflow;
			runs = newestFirst((await conn.client.listN8nExecutions(id, 10)).executions ?? []);
		} catch (e) {
			err = describeError(e);
		}
	}

	async function setActive(workflow: N8nWorkflow, active: boolean): Promise<void> {
		if (!conn || busy) return;
		busy = workflow.id;
		err = '';
		try {
			await conn.client.setN8nActive(workflow.id, active);
			await load(conn);
			toasts.success(
				`${workflow.name} is ${active ? 'live' : 'off'}`,
				active ? 'its trigger is firing now' : 'it will not fire again until you switch it on'
			);
		} catch (e) {
			err = describeError(e);
			toasts.error(`Could not switch ${workflow.name}`, describeError(e));
		} finally {
			busy = '';
		}
	}

	async function runHealth(id: string): Promise<void> {
		if (!conn || checkingHealth) return;
		checkingHealth = true;
		health = null;
		try {
			health = await conn.client.n8nHealth(id);
		} catch (e) {
			err = describeError(e);
		} finally {
			checkingHealth = false;
		}
	}

	async function build(): Promise<void> {
		if (!conn || building || !instruction.trim()) return;
		building = true;
		err = '';
		transcript = [];
		try {
			const started = await conn.client.buildN8nWorkflow(instruction.trim());
			buildTask = started.task.id;
			instruction = '';
			toasts.success(
				"n8n's builder is working on it",
				'It may stop to ask you something — that arrives as an approval.'
			);
		} catch (e) {
			err = describeError(e);
			toasts.error('Could not start the build', describeError(e));
		} finally {
			building = false;
		}
	}

	async function readTranscript(): Promise<void> {
		if (!conn || !buildTask) return;
		try {
			transcript = (await conn.client.n8nTranscript(buildTask)).transcript ?? [];
		} catch (e) {
			err = describeError(e);
		}
	}

	async function runCheck(): Promise<void> {
		if (!conn || checking) return;
		checking = true;
		check = null;
		try {
			check = await conn.client.checkN8n();
		} catch (e) {
			check = { ok: false, detail: describeError(e) };
		} finally {
			checking = false;
		}
	}

	let disposed = false;
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mineDial = ++dial;
		conn?.close();
		conn = null;
		err = '';
		hint = '';
		loading = true;
		try {
			const connection = await openConnection({
				onStatus: (s) => {
					if (mineDial === dial) status = s;
				}
			});
			if (disposed || mineDial !== dial) {
				connection.close();
				return;
			}
			conn = connection;
			await load(connection);
		} catch (e) {
			if (isUnsupported(e)) {
				hint = 'this backend has no n8n integration — nothing here will fill in';
			} else {
				err = describeError(e);
			}
		} finally {
			redialling = false;
			if (!disposed) loading = false;
		}
	}

	onMount(() => {
		disposed = false;
		void connect();
		return () => {
			disposed = true;
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · n8n</title></svelte:head>

<h1>N8N</h1>
<p class="lede" data-testid="n8n-lede" data-redialling={redialling}>
	{workflows.length} workflow{workflows.length === 1 ? '' : 's'} · link {status}
</p>

<Reconnect {status} busy={redialling} retry={connect} />

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}

{#if loading}
	<Skeleton rows={3} />
{:else}
	<section class="panel" data-testid="n8n-instance">
		<div class="panel-head">
			<span>Instance</span>
			<span class="muted" data-testid="n8n-url">{instance?.url || 'not configured'}</span>
		</div>
		<p class="hint" data-testid="n8n-instance-note">{describeInstance(instance)}</p>

		{#if !configured}
			<!-- The URL and the key are configuration.yaml's, not the console's: a
			     setting a request can write is a setting a stolen session can
			     write, and this one reaches a system that sends mail. -->
			<pre class="snippet" data-testid="n8n-snippet">n8n:
  url: http://127.0.0.1:5678
  api_key: !env_var N8N_API_KEY ""</pre>
			<p class="hint">
				Make the key in n8n under <strong>Settings → n8n API</strong>, put it in
				<code>.env</code> as <code>N8N_API_KEY</code>, and restart jarvis-core.
			</p>
		{/if}

		<div class="row">
			<button
				type="button"
				class="btn"
				data-testid="n8n-check"
				disabled={checking || !instance?.url}
				onclick={runCheck}
			>
				{checking ? 'CHECKING…' : 'CHECK'}
			</button>
			<span class="hint">
				Makes the smallest real API call there is. Configured is not the same as
				working — n8n's API has moved between versions.
			</span>
		</div>
		{#if check}
			<p
				class={check.ok ? 'notice' : 'err'}
				data-testid="n8n-check-result"
				data-ok={check.ok ? 'true' : 'false'}
			>
				{check.detail}
			</p>
			<!--
				Three lines, not one. The public API, the optional login and the AI
				builder fail independently and for different reasons, and a single
				"n8n: no" sends somebody to the wrong half of their setup.
			-->
			{#if layers.length > 1}
				<div class="layers" data-testid="n8n-layers">
					{#each layers as layer (layer.label)}
						<div
							class="layer"
							data-testid="n8n-layer-{layer.label.toLowerCase().replace(/ /g, '-')}"
							data-available={layer.available ? 'true' : 'false'}
						>
							<span class="layer-name">{layer.label}</span>
							<span class="layer-detail">{layer.detail}</span>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
		{#if configured && !instance?.allow_activate}
			<p class="hint" data-testid="n8n-activate-note">
				Jarvis may not switch a workflow on by itself — you can, with the button on
				each row. Set <code>n8n: allow_activate: true</code> to let it ask.
			</p>
		{/if}
	</section>

	{#if configured && check}
		<!--
			Only after CHECK, and only when the licence actually allows it. A form
			that submits into a 403 is worse than no form — and somebody who wired
			a model up to n8n and did not get the feature deserves the sentence
			saying which of the two switches is missing, not a button that fails.
		-->
		<section class="panel" data-testid="n8n-builder">
			<div class="panel-head">
				<span>Ask n8n's builder</span>
				<span class="muted">{builderReady ? 'available' : 'not available here'}</span>
			</div>

			{#if builderReady}
				<label for="n8n-instruction">What should the workflow do?</label>
				<textarea
					id="n8n-instruction"
					rows="3"
					data-testid="n8n-instruction"
					placeholder="Every morning at 8, email me yesterday's orders from the shop"
					bind:value={instruction}
				></textarea>
				<div class="row">
					<button
						type="button"
						class="btn"
						data-testid="n8n-build"
						disabled={building || !instruction.trim()}
						onclick={build}
					>
						{building ? 'STARTING…' : 'BUILD IT'}
					</button>
					<span class="hint">
						It runs in the background and <strong>may stop to ask you something</strong>,
						which arrives as an approval. What it produces still arrives switched
						off, with no credentials attached.
					</span>
				</div>
				{#if buildTask}
					<p class="notice" data-testid="n8n-build-started">
						Started as task <code>{buildTask}</code>. Its progress is on the Tasks page.
					</p>
					<div class="row">
						<button
							type="button"
							class="btn ghost"
							data-testid="n8n-transcript"
							onclick={readTranscript}
						>
							READ THE TRANSCRIPT
						</button>
						<span class="hint">
							What the builder said, in its own words. Jarvis's model is given one
							sentence instead — this is prose written by a different AI.
						</span>
					</div>
					{#if transcript.length}
						<div class="editor" data-testid="n8n-transcript-lines">
							{#each transcript as line, i (i)}
								<p class="line" data-role={line.role}>
									<span class="who">{describeSpeaker(line.role)}</span>
									<span>{line.text}</span>
								</p>
							{/each}
						</div>
					{/if}
				{/if}
			{:else}
				<p class="hint" data-testid="n8n-builder-problem">{builderProblem}</p>
				<p class="hint">
					This is not a fault to work around. Jarvis writes workflows itself, which
					works on every n8n — just ask it for one.
				</p>
			{/if}
		</section>
	{/if}

	{#if configured}
		<section class="panel" data-testid="n8n-workflows">
			<div class="panel-head">
				<span>Workflows</span>
				<span class="muted">{workflows.length}</span>
			</div>

			{#if !workflows.length}
				<div class="jv-empty" data-testid="n8n-empty">
					<span class="jv-empty-mark" aria-hidden="true">[ ∅ ]</span>
					<p class="jv-empty-title">No workflows yet</p>
					<p class="jv-empty-body">
						Ask Jarvis for one — “write me a workflow that files receipts into
						Notion”. It arrives switched off, with a list of what to connect.
					</p>
				</div>
			{:else}
				{#each workflows as workflow, i (workflow.id)}
					<div class="row-wrap jv-stagger" style={staggerStyle(i)}>
						<div class="row" data-testid="n8n-workflow-{workflow.id}">
							<button
								type="button"
								class="btn ghost name"
								data-testid="n8n-open-{workflow.id}"
								aria-expanded={opened === workflow.id}
								onclick={() => open(workflow.id)}
							>
								{workflow.name}
							</button>
							<span
								class="state"
								data-testid="n8n-state-{workflow.id}"
								data-active={workflow.active ? 'true' : 'false'}
							>
								{describeActive(workflow)}
							</span>
							<span class="muted">{workflow.nodes} node{workflow.nodes === 1 ? '' : 's'}</span>
							<button
								type="button"
								class="btn"
								data-testid="n8n-toggle-{workflow.id}"
								disabled={busy === workflow.id}
								onclick={() => setActive(workflow, !workflow.active)}
							>
								{busy === workflow.id ? '…' : workflow.active ? 'DEACTIVATE' : 'ACTIVATE'}
							</button>
						</div>

						{#if opened === workflow.id}
							<div class="editor" data-testid="n8n-detail-{workflow.id}">
								{#if !graph}
									<Skeleton rows={2} />
								{:else}
									{#if connectLine}
										<!-- The whole point of the page. -->
										<p class="notice" data-testid="n8n-connections-needed">
											{connectLine}
										</p>
									{/if}
									<ul class="nodes">
										{#each graph.nodes as node (node.name)}
											<li data-testid="n8n-node-{node.name}">
												<span class="node-name">{node.name}</span>
												<span class="muted">{node.type}</span>
												{#if node.has_credential}
													<span class="tag">{node.credential_types.join(', ')}</span>
												{/if}
											</li>
										{/each}
									</ul>
									<p class="hint">
										Node parameters are not shown here, and jarvis-core does not send
										them — people type API keys into them.
									</p>

									{#if runs.length}
										<p class="hint">Recent runs</p>
										<ul class="runs" data-testid="n8n-runs">
											{#each runs as run (run.id)}
												<li data-tone={runTone(run.status)}>
													<span>{run.status || 'unknown'}</span>
													<span class="muted">{run.started_at}</span>
												</li>
											{/each}
										</ul>
									{/if}

									<!--
										"Is this working?" is three separate reads — connected,
										switched on, has it run — and joining them is the only way
										to see the state that matters: on, connected, and never
										run. That one looks identical to working from every other
										angle, and it is what a schedule in the wrong timezone
										does.
									-->
									<div class="row">
										<button
											type="button"
											class="btn ghost"
											data-testid="n8n-health-{workflow.id}"
											disabled={checkingHealth}
											onclick={() => runHealth(workflow.id)}
										>
											{checkingHealth ? 'CHECKING…' : 'IS IT WORKING?'}
										</button>
									</div>
									{#if health && health.workflow_id === workflow.id}
										<p
											class={health.healthy ? 'notice' : 'err'}
											data-testid="n8n-health-result"
											data-tone={healthTone(health)}
										>
											{health.summary}
											{#if health.next_step}<br /><span class="muted">{health.next_step}</span>{/if}
										</p>
									{/if}
								{/if}
							</div>
						{/if}
					</div>
				{/each}
			{/if}
		</section>
	{/if}
{/if}

<style>
	.layers {
		display: grid;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-2);
	}
	.layer {
		display: grid;
		grid-template-columns: 8rem 1fr;
		gap: var(--jv-space-3);
		font-size: var(--jv-fs-2xs);
		border-left: 2px solid var(--jv-line);
		padding-left: var(--jv-space-3);
	}
	.layer[data-available='true'] {
		border-left-color: var(--jv-accent);
	}
	.layer-name {
		font-family: var(--jv-font-chrome);
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}
	.layer-detail {
		color: var(--jv-text-dim);
	}
	.line {
		display: grid;
		grid-template-columns: 8rem 1fr;
		gap: var(--jv-space-3);
		margin: 0 0 var(--jv-space-2);
		font-size: var(--jv-fs-2xs);
	}
	.line .who {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-dim);
	}
	/* Words written by a different AI, on somebody else's machine. Marked, so
	   they do not read as Jarvis's own. */
	.line[data-role='builder'] .who {
		color: var(--jv-warn);
	}
	.snippet {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		background: var(--jv-field);
		border-left: 2px solid var(--jv-accent);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-3);
		overflow-x: auto;
		margin: 0;
	}
	.row .name {
		flex: 1 1 auto;
		text-align: left;
	}
	.state[data-active='true'] {
		color: var(--jv-accent);
	}
	.nodes,
	.runs {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
	}
	.nodes li,
	.runs li {
		display: flex;
		gap: var(--jv-space-2);
		align-items: baseline;
		flex-wrap: wrap;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
	}
	.node-name {
		color: var(--jv-text);
	}
	.tag {
		color: var(--jv-accent);
	}
	.runs li[data-tone='bad'] span:first-child {
		color: var(--jv-danger, crimson);
	}
	.runs li[data-tone='good'] span:first-child {
		color: var(--jv-accent);
	}
</style>
