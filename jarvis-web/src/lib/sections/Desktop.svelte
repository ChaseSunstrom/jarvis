<script lang="ts">
	/**
	 * The desktop: the agent on a computer, and the shell around this console.
	 *
	 * Two different things with one name, and the page says which is which —
	 * the **agent** (`jarvis-desktop/`) is a Python process that can act on a
	 * machine and asks before it does; the **shell** (`jarvis-desktop-app/`) is
	 * an Electron window showing this console, with a tray icon, native
	 * notifications and a push-to-talk key.
	 *
	 * Neither is a thing this page can install. What it can do is say whether
	 * they are there, what the agent will let Jarvis do, and — the one question
	 * a person actually arrives with — why the hotkey is not working.
	 */
	import { onDestroy, onMount } from 'svelte';
	import { openConnection, type Connection, type ConnectionStatus } from '$lib/connection';
	import type { CompanionDevice } from '$lib/jarvisClient';
	import { Panel, Pill, ScreenState } from '$lib/ui';

	let conn = $state<Connection | null>(null);
	let status = $state<ConnectionStatus>('connecting');
	let err = $state('');
	let loading = $state(true);
	let redialling = $state(false);
	let devices = $state<CompanionDevice[]>([]);

	/**
	 * Whether this page is inside the Electron shell.
	 *
	 * Feature-detected rather than sniffed from the user agent: the preload
	 * exposes `window.jarvisDesktop`, and a browser does not have it. That is
	 * the whole difference, and it is the difference that decides what this
	 * page should say.
	 */
	let inShell = $state(false);
	let shellState = $state('');

	const desktops = $derived(
		devices.filter((d) => (d.platform ?? '').toLowerCase().match(/linux|windows|mac|desktop/))
	);

	let screen = $derived<'ready' | 'error' | 'offline' | 'loading' | 'empty'>(
		status === 'closed' || status === 'error'
			? 'offline'
			: err
				? 'error'
				: loading
					? 'loading'
					: devices.length || inShell
						? 'ready'
						: 'empty'
	);

	async function connect() {
		redialling = true;
		try {
			conn?.close();
			const link = await openConnection({ onStatus: (s) => (status = s) });
			conn = link;
			devices = await link.client.listCompanions();
			err = '';
		} catch (error) {
			err = error instanceof Error ? error.message : String(error);
		} finally {
			loading = false;
			redialling = false;
		}
	}

	onMount(() => {
		const shell = (window as unknown as { jarvisDesktop?: { state?: () => Promise<any> } })
			.jarvisDesktop;
		inShell = Boolean(shell);
		if (shell?.state) {
			void shell.state().then((s: { state?: string; detail?: string }) => {
				shellState = [s?.state, s?.detail].filter(Boolean).join(' — ');
			});
		}
		void connect();
	});
	onDestroy(() => conn?.close());
</script>

<div class="stack">
	<p class="lede" data-testid="desktop-lede" data-redialling={redialling}>
		{inShell ? 'running inside the Jarvis shell' : 'running in a browser'} · link {status}
	</p>

	<ScreenState
		status={screen}
		rows={3}
		errorTitle="Could not reach the server"
		errorDetail={err}
		emptyTitle="No desktop is paired"
		emptyBody="Run the desktop agent on a computer and pair it; it will appear here."
		onretry={connect}
		onreconnect={connect}
		busy={redialling}
		errorTestid="error"
		emptyTestid="desktop-empty"
	>
		{#snippet children()}
			<Panel title="This window" meta={inShell ? 'shell' : 'browser'}>
				{#snippet children()}
					{#if inShell}
						<p class="prose" data-testid="shell-present">
							You are in the Jarvis shell. The tray icon shows what the assistant is doing,
							approvals arrive as native notifications, and <strong>Super+Space</strong> starts
							a turn from anywhere.
						</p>
						{#if shellState}<p class="dim" data-testid="shell-state">Agent: {shellState}</p>{/if}
					{:else}
						<p class="prose" data-testid="shell-absent">
							This is a browser tab. Everything works, with three exceptions the shell adds:
							a tray icon, native notifications when Jarvis needs an approval, and a
							push-to-talk key that works while another window has focus.
						</p>
					{/if}
				{/snippet}
			</Panel>

			<Panel title="Paired computers" meta={`${desktops.length}`}>
				{#snippet children()}
					{#if desktops.length === 0}
						<p class="dim">
							None. The agent lives in <code>jarvis-desktop/</code>; pair it the way a phone
							pairs, and what it will allow appears here.
						</p>
					{:else}
						<ul class="devices" data-testid="desktop-devices">
							{#each desktops as device (device.device_id)}
								<li class="device">
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
		{/snippet}
	</ScreenState>
</div>

<style>
	.stack {
		display: grid;
		gap: var(--jv-space-4);
	}
	/* A sentence, so the body face: a whole paragraph in mono is the M48
	   look the direction retired, and the look spec reads it as such. */
	.lede {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.prose {
		margin: 0;
		font-size: var(--jv-fs-sm);
		line-height: 1.6;
		color: var(--jv-text);
		max-width: 70ch;
	}
	.prose strong {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	.dim {
		margin: var(--jv-space-2) 0 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		color: var(--jv-text-faint);
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
	@media (max-width: 640px) {
		.device {
			grid-template-columns: minmax(0, 1fr) auto;
		}
	}
</style>
