<script lang="ts">
	/**
	 * SETTINGS › Console: this screen, and the machines that show it.
	 *
	 * Nothing here is a house setting. Text size is a property of the screen
	 * somebody is reading; the backend panel is this web server's own
	 * environment; pairing puts a phone on the house; the desktop rows say
	 * whether this window is the Electron shell and which computers run the
	 * agent. The event stream — a raw firehose of the bus — is a diagnostic,
	 * folded away at the bottom.
	 */
	import { onMount } from 'svelte';
	import Pairing from '$lib/components/Pairing.svelte';
	import SettingsFold from '$lib/components/SettingsFold.svelte';
	import { relayUrl, describeError } from '$lib/connection';
	import type { BusEvent, CompanionDevice, Subscription } from '$lib/jarvisClient';
	import { SectionLink } from '$lib/sectionLink.svelte';
	import { TEXT_SIZES, applyTextSize, readTextSize, writeTextSize } from '$lib/textSize';
	import { toasts } from '$lib/toast';
	import { Button, Panel, Pill, ScreenState, SkeletonRows, SettingRow } from '$lib/ui';

	interface ClientConfig {
		pipeline?: string;
		ttsVoice?: string;
		backend?: string;
		backendUrl?: string;
		backendUrlVar?: string;
		backendTokenVar?: string;
		tokenConfigured?: boolean;
		problem?: string | null;
	}

	const MAX_LOG = 200;

	let hint = $state('');
	/** True once the socket has answered: what a skeleton waits for. */
	let loaded = $state(false);
	let config = $state<ClientConfig>({});
	let backendConfig = $state<Record<string, any> | null>(null);
	let pipelines = $state<any[]>([]);
	let preferred = $state<string | null>(null);
	let devices = $state<CompanionDevice[]>([]);

	/**
	 * The pipelines the backend reports, named, with the preferred one marked.
	 * Shown as text rather than a `<select>`: the HUD reads `JARVIS_PIPELINE`
	 * from the server's environment at load, so a control here could only ever
	 * print a note telling you to go and edit an environment variable.
	 */
	const pipelineNames = $derived(
		pipelines.map((p) => (p.id === preferred ? `${p.name} (preferred)` : p.name))
	);

	// --- the event stream ----------------------------------------------------
	let eventFilter = $state('state_changed');
	let liveFilter = $state('state_changed');
	let paused = $state(false);
	let log = $state<{ n: number; at: string; type: string; body: string }[]>([]);
	let counter = 0;
	let sub: Subscription | null = null;

	function push(event: BusEvent): void {
		if (paused) return;
		counter += 1;
		const entry = {
			n: counter,
			at: new Date().toLocaleTimeString(),
			type: event?.event_type ?? 'event',
			body: JSON.stringify(event?.data ?? {})
		};
		log = [entry, ...log].slice(0, MAX_LOG);
	}

	async function applyFilter(): Promise<void> {
		if (!link.conn) return;
		link.err = '';
		const next = eventFilter.trim();
		try {
			await sub?.unsubscribe();
			sub = await link.conn.client.subscribeEvents(push, next || undefined);
			liveFilter = next || '(all events)';
			log = [];
			counter = 0;
			toasts.success(`Subscribed to ${liveFilter}`);
		} catch (e) {
			link.err = describeError(e);
			toasts.error('Subscription failed', describeError(e));
		}
	}

	const link = new SectionLink(
		async (conn) => {
			try {
				backendConfig = await conn.client.getConfig();
			} catch (e) {
				hint = describeError(e);
			}
			try {
				const list = await conn.client.listPipelines();
				pipelines = list?.pipelines ?? [];
				preferred = list?.preferred_pipeline ?? null;
			} catch (e) {
				hint = describeError(e);
			}
			try {
				devices = (await conn.client.listCompanions()) ?? [];
			} catch (e) {
				hint = describeError(e);
			}
			loaded = true;
			sub = await conn.client.subscribeEvents(push, liveFilter || undefined);
		},
		() => {
			void sub?.unsubscribe();
			sub = null;
		}
	);

	// --- this window ---------------------------------------------------------
	/**
	 * Whether this page is inside the Electron shell. Feature-detected rather
	 * than sniffed from the user agent: the preload exposes `window.jarvisDesktop`,
	 * and a browser does not have it.
	 */
	let inShell = $state(false);
	let shellState = $state('');
	const desktops = $derived(
		devices.filter((d) => (d.platform ?? '').toLowerCase().match(/linux|windows|mac|desktop/))
	);

	// --- text size -----------------------------------------------------------
	/**
	 * How big the text is, in this browser. Not a house setting and deliberately
	 * not sent anywhere: the same house is read from a phone at arm's length
	 * and a wall panel across a room. Applied the moment it is picked — a
	 * text-size control you have to save is one you cannot judge.
	 */
	let textSize = $state('standard');

	function chooseTextSize(id: string): void {
		const size = writeTextSize(localStorage, id);
		applyTextSize(document, size);
		textSize = size.id;
		toasts.success(`Text size · ${size.label}`, 'Remembered in this browser.');
	}

	onMount(() => {
		textSize = readTextSize(localStorage).id;
		const shell = (window as unknown as { jarvisDesktop?: { state?: () => Promise<any> } }).jarvisDesktop;
		inShell = Boolean(shell);
		if (shell?.state) {
			void shell.state().then((s: { state?: string; detail?: string }) => {
				shellState = [s?.state, s?.detail].filter(Boolean).join(' — ');
			});
		}
		fetch('/api/config')
			.then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/api/config → ${r.status}`))))
			.then((c) => (config = c))
			// Was swallowed silently, which left the whole panel showing
			// placeholders with nothing to explain why.
			.catch((e) => (hint = describeError(e)));
		return link.mount();
	});
</script>

<div class="stack">
	<p class="lede" data-testid="settings-console-lede">
		{inShell ? 'running inside the Jarvis shell' : 'running in a browser'} · link {link.status} · relay
		<code>{typeof location === 'undefined' ? '' : relayUrl()}</code>
	</p>

	<ScreenState
		status={link.screen}
		errorTitle="This page hit an error"
		errorDetail={link.err}
		onretry={() => link.connect()}
		onreconnect={() => link.connect()}
		busy={link.redialling}
		errorTestid="error"
	/>

	{#if hint}<p class="line warn" data-testid="hint">{hint}</p>{/if}
	{#if config.problem}<p class="line bad" data-testid="config-problem" role="alert">{config.problem}</p>{/if}

	<!-- Text size first: the one setting on this page that changes what you can
	     read while you read it. -->
	<Panel title="Text size" meta="this browser only" testid="text-size">
		{#snippet children()}
			<SettingRow label="Scale" why="multiplies every size in the interface">
				<!-- A segmented choice: the current size is raised, not lit. The accent
				     is for what is about to happen, and a preference already in effect
				     is not that. -->
				<div class="seg" role="group" aria-label="Text size">
					{#each TEXT_SIZES as size (size.id)}
						<Button
							pressed={textSize === size.id}
							testid="text-size-{size.id}"
							title={size.note}
							onclick={() => chooseTextSize(size.id)}
						>
							{size.label}
						</Button>
					{/each}
				</div>
			</SettingRow>
			<p class="note">
				STANDARD is whatever text size this browser is already set to — so raising it in the
				browser and raising it here compound, which is the intent. Everything in the console and
				the voice screen is sized in <code>rem</code>, so one number moves all of it at once.
			</p>
		{/snippet}
	</Panel>

	<!--
	  This console's OWN environment, as opposed to the house settings.

	  Everything in this panel is a server-side environment variable of the web
	  server, readable and not settable from a browser — the token in particular
	  never leaves the server. Keeping it visually apart from the editable rows
	  is the point: a row you cannot change, sitting among rows you can, reads as
	  a control that is broken.
	-->
	<Panel title="This console" meta={config.backend ?? '…'} live={link.status === 'open'} testid="console-env">
		{#snippet children()}
			<SettingRow label="Backend" why="how this console reaches Jarvis">
				<span class="value">
					<Pill tone={link.status === 'open' ? 'live' : 'neutral'} testid="backend-kind">{config.backend ?? '…'}</Pill>
				</span>
			</SettingRow>
			<SettingRow>
				{#snippet what()}<b>URL</b><code>{config.backendUrlVar ?? 'JARVIS_URL'}</code>{/snippet}
				<span class="value mono" data-testid="backend-url">{config.backendUrl || 'not configured'}</span>
			</SettingRow>
			<SettingRow>
				{#snippet what()}<b>Token</b><code>{config.backendTokenVar ?? 'JARVIS_TOKEN'}</code>{/snippet}
				<span class="value" data-testid="backend-token">
					{config.tokenConfigured ? '•••••••• held server-side' : 'not configured'}
				</span>
			</SettingRow>
			<SettingRow label="Version" why="reported by the backend">
				<span class="value mono">{backendConfig?.version ?? backendConfig?.ha_version ?? 'unknown'}</span>
			</SettingRow>
			<SettingRow>
				{#snippet what()}<b>Voice pipeline</b><code>JARVIS_PIPELINE</code>{/snippet}
				<span class="value" data-testid="pipeline-name">
					{config.pipeline || 'not set'}{#if pipelineNames.length}
						<span class="dim"> · available: {pipelineNames.join(', ')}</span>
					{/if}
				</span>
			</SettingRow>
			<p class="note">
				These are server-side environment variables — the browser never receives the token. Change
				<code>JARVIS_BACKEND</code>, <code>JARVIS_URL</code>, <code>JARVIS_TOKEN</code> or
				<code>JARVIS_PIPELINE</code> where the web server runs, then restart it.
			</p>
		{/snippet}
	</Panel>

	<Pairing />

	<!--
	  The desktop: the agent on a computer, and the shell around this console.
	  Two different things with one name — the **agent** (`jarvis-desktop/`) is
	  a Python process that can act on a machine and asks before it does; the
	  **shell** (`jarvis-desktop-app/`) is an Electron window showing this
	  console. Neither is a thing this page can install; what it can do is say
	  whether they are there.
	-->
	<Panel title="This window" meta={inShell ? 'shell' : 'browser'} testid="this-window">
		{#snippet children()}
			{#if inShell}
				<p class="prose" data-testid="shell-present">
					You are in the Jarvis shell. The tray icon shows what the assistant is doing, approvals
					arrive as native notifications, and <strong>Super+Space</strong> starts a turn from anywhere.
				</p>
				{#if shellState}<p class="dim" data-testid="shell-state">Agent: {shellState}</p>{/if}
			{:else}
				<p class="prose" data-testid="shell-absent">
					This is a browser tab. Everything works, with three exceptions the shell adds: a tray
					icon, native notifications when Jarvis needs an approval, and a push-to-talk key that
					works while another window has focus.
				</p>
			{/if}
		{/snippet}
	</Panel>

	<Panel title="Paired computers" meta={loaded ? `${desktops.length}` : '…'} testid="paired-computers">
		{#snippet children()}
			{#if !loaded && link.status !== 'closed' && link.status !== 'error'}
				<!-- Connected, and told nothing yet: the window a skeleton is for. -->
				<SkeletonRows rows={2} label="Loading paired computers" />
			{:else if desktops.length === 0}
				<p class="prose dim" data-testid="desktop-empty">
					None. The agent lives in <code>jarvis-desktop/</code>; pair it the way a phone pairs,
					and what it will allow appears here.
				</p>
			{:else}
				<ul class="devices" data-testid="desktop-devices">
					{#each desktops as device (device.device_id)}
						<li data-jv-row data-testid="paired-{device.device_id}" class="device">
							<span class="name">{device.name}</span>
							<code>{device.platform ?? 'unknown'}</code>
							<code>{device.action_count ?? device.actions?.length ?? 0} actions</code>
							<Pill tone={device.connected ? 'ok' : 'neutral'}>
								{device.connected ? 'online' : 'offline'}
							</Pill>
						</li>
					{/each}
				</ul>
			{/if}
		{/snippet}
	</Panel>

	<!-- A diagnostic, folded away: every event on the bus, raw. -->
	<SettingsFold title="Event stream" meta={liveFilter || '(all events)'} testid="event-stream">
		{#snippet children()}
			<div class="stream-bar">
				<label class="jv-sr-only" for="event-filter">Event type filter</label>
				<input
					id="event-filter"
					class="stream-filter"
					type="text"
					placeholder="event_type filter (blank = everything)  ( / )"
					data-testid="event-filter"
					data-jv-filter
					bind:value={eventFilter}
					onkeydown={(e) => e.key === 'Enter' && applyFilter()}
				/>
				<Button testid="apply-filter" onclick={applyFilter}>SUBSCRIBE</Button>
				<Button testid="pause" aria-pressed={paused} onclick={() => (paused = !paused)}>
					{paused ? 'RESUME' : 'PAUSE'}
				</Button>
				<Button aria-label="Clear the event log" onclick={() => (log = [])}>CLEAR</Button>
				<span class="count" data-testid="event-count">{log.length}</span>
				<span class="jv-sr-only" data-testid="live-filter">{liveFilter || '(all events)'}</span>
			</div>
			<pre data-testid="event-log" aria-label="Live event stream">{log
					.map((e) => `${e.at}  ${e.type}  ${e.body}`)
					.join('\n') || 'waiting for events…'}</pre>
		{/snippet}
	</SettingsFold>
</div>

<style>
	.stack {
		display: grid;
		gap: var(--jv-space-4);
	}
	.lede {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.lede code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text);
	}
	.line {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.line.warn {
		color: var(--jv-warn);
	}
	.line.bad {
		color: var(--jv-danger-text);
	}
	/* The row's grid is SettingRow's (M107). */
	.setting:last-child {
		border-bottom: 0;
	}
	.what {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.what b {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	.what code,
	.value.mono {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	.dim {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.value {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.note {
		grid-column: 1 / -1;
		margin: 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 80ch;
	}
	.prose {
		margin: 0;
		font-size: var(--jv-fs-sm);
		line-height: 1.6;
		color: var(--jv-text);
		max-width: 70ch;
	}
	.prose.dim {
		color: var(--jv-text-dim);
	}
	.prose strong {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}

	/* The segmented choice: the pressed segment is raised on the surface. */
	.seg {
		grid-column: 2 / -1;
		display: inline-flex;
		justify-self: end;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.seg :global(.btn) {
		border: 0;
		border-right: 1px solid var(--jv-line-hair);
		border-radius: 0;
	}
	.seg :global(.btn:last-child) {
		border-right: 0;
	}
	.seg :global(.btn.on) {
		color: var(--jv-text-bright);
		background: var(--jv-surface-2);
	}

	.devices {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.device {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto auto;
		align-items: center;
		gap: var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.device:last-child {
		border-bottom: 0;
	}
	.name {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.stream-bar {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
		margin-bottom: var(--jv-space-3);
	}
	/* The one raw input on the page: `/` focuses it (data-jv-filter) and the
	   library's Input has no `id` for the label to bind to. Drawn as Input is. */
	.stream-filter {
		flex: 1 1 18rem;
		min-width: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
		background: var(--jv-field);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-3);
	}
	.stream-filter::placeholder {
		color: var(--jv-text-faint);
	}
	.stream-filter:hover {
		border-color: var(--jv-line);
	}
	.count {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
		margin-left: auto;
	}
	pre {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		background: var(--jv-surface-sunken);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-3);
		overflow-x: auto;
		max-height: var(--jv-measure-log);
	}

	@media (max-width: 720px) {
		.seg {
			grid-column: 1;
			justify-self: start;
		}
	}
	@media (max-width: 640px) {
		.device {
			grid-template-columns: minmax(0, 1fr) auto;
		}
	}
</style>
