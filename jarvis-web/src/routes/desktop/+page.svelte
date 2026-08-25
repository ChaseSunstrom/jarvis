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
	import { Panel, Pill, Row, ScreenState } from '$lib/ui';

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

<svelte:head><title>Jarvis · Desktop</title></svelte:head>

<h1 data-testid="desktop">DESKTOP</h1>
<p class="lede" data-testid="desktop-lede" data-redialling={redialling}>
	{inShell ? 'Running inside the Jarvis shell' : 'Running in a browser'} · link {status}
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
					<p data-testid="shell-present">
						You are in the Jarvis shell. The tray icon shows what the assistant is doing,
						approvals arrive as native notifications, and <strong>Super+Space</strong> starts
						a turn from anywhere.
					</p>
					{#if shellState}<p class="dim" data-testid="shell-state">Agent: {shellState}</p>{/if}
				{:else}
					<p data-testid="shell-absent">
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
							<li>
								<Row>
									{#snippet children()}
										<Pill tone={device.connected ? 'ok' : 'neutral'}>
											{device.connected ? 'online' : 'offline'}
										</Pill>
										<span class="grow">{device.name}</span>
										<span class="dim">{device.platform ?? 'unknown'}</span>
										<span class="dim">{device.action_count ?? device.actions?.length ?? 0} actions</span>
									{/snippet}
								</Row>
							</li>
						{/each}
					</ul>
				{/if}
			{/snippet}
		</Panel>
	{/snippet}
</ScreenState>

<style>
	.devices {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
	}
	.dim {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-2xs);
	}
	.grow {
		flex: 1;
	}
</style>
