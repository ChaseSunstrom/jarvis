<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, relayUrl, type Connection } from '$lib/connection';
	import type { BusEvent, Subscription } from '$lib/jarvisClient';

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

	let conn: Connection | null = null;
	let status = $state('connecting');
	let err = $state('');
	let hint = $state('');

	let config = $state<ClientConfig>({});
	let backendConfig = $state<Record<string, any> | null>(null);
	let pipelines = $state<any[]>([]);
	let preferred = $state<string | null>(null);
	let selectedPipeline = $state('');

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
		if (!conn) return;
		err = '';
		const next = eventFilter.trim();
		try {
			await sub?.unsubscribe();
			sub = await conn.client.subscribeEvents(push, next || undefined);
			liveFilter = next || '(all events)';
			log = [];
			counter = 0;
		} catch (e) {
			err = describeError(e);
		}
	}

	onMount(() => {
		let disposed = false;
		fetch('/api/config')
			.then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/api/config → ${r.status}`))))
			.then((c) => {
				config = c;
				selectedPipeline = c.pipeline ?? '';
			})
			// Was swallowed silently, which left the whole Backend panel showing
			// placeholders with nothing to explain why.
			.catch((e) => (hint = describeError(e)));

		(async () => {
			try {
				const connection = await openConnection({ onStatus: (s) => (status = s) });
				if (disposed) {
					connection.close();
					return;
				}
				conn = connection;
				try {
					backendConfig = await connection.client.getConfig();
				} catch (e) {
					hint = describeError(e);
				}
				try {
					const list = await connection.client.listPipelines();
					pipelines = list?.pipelines ?? [];
					preferred = list?.preferred_pipeline ?? null;
				} catch (e) {
					hint = describeError(e);
				}
				sub = await connection.client.subscribeEvents(push, liveFilter || undefined);
			} catch (e) {
				err = describeError(e);
			}
		})();

		return () => {
			disposed = true;
			void sub?.unsubscribe();
			conn?.close();
			conn = null;
		};
	});
</script>

<svelte:head><title>Jarvis · Settings</title></svelte:head>

<h1>SETTINGS</h1>
<p class="lede">link {status} · relay {typeof location === 'undefined' ? '' : relayUrl()}</p>

{#if err}<p class="err" data-testid="error">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}
{#if config.problem}<p class="err" data-testid="config-problem">{config.problem}</p>{/if}

<section class="panel">
	<div class="panel-head">
		<span>Backend</span>
		<span class="pill" class:on={status === 'open'} data-testid="backend-kind">
			{config.backend ?? '…'}
		</span>
	</div>
	<div class="row">
		<span class="name"><b>URL</b><span class="eid">{config.backendUrlVar ?? 'JARVIS_URL'}</span></span>
		<span class="muted" data-testid="backend-url">{config.backendUrl || 'not configured'}</span>
	</div>
	<div class="row">
		<span class="name">
			<b>Token</b><span class="eid">{config.backendTokenVar ?? 'JARVIS_TOKEN'}</span>
		</span>
		<span class="muted" data-testid="backend-token">
			{config.tokenConfigured ? '•••••••• held server-side' : 'not configured'}
		</span>
	</div>
	<div class="row">
		<span class="name"><b>Version</b><span class="eid">reported by the backend</span></span>
		<span class="muted">{backendConfig?.version ?? backendConfig?.ha_version ?? 'unknown'}</span>
	</div>
	<p class="muted">
		URL and token are server-side environment variables — the browser never receives the token.
		Change <code>JARVIS_BACKEND</code>, <code>JARVIS_URL</code> and <code>JARVIS_TOKEN</code> where the
		web server runs, then restart it.
	</p>
</section>

<section class="panel">
	<div class="panel-head"><span>Voice pipeline</span></div>
	<div class="row">
		<span class="name"><b>Pipeline</b><span class="eid">JARVIS_PIPELINE</span></span>
		<select data-testid="pipeline-select" bind:value={selectedPipeline}>
			{#each pipelines as pipeline (pipeline.id)}
				<option value={pipeline.name}>
					{pipeline.name}{pipeline.id === preferred ? ' (preferred)' : ''}
				</option>
			{/each}
			{#if !pipelines.length}
				<option value={selectedPipeline}>{selectedPipeline || 'none reported'}</option>
			{/if}
		</select>
	</div>
	<div class="row">
		<span class="name"><b>TTS voice</b><span class="eid">JARVIS_TTS_VOICE</span></span>
		<span class="muted" data-testid="tts-voice">{config.ttsVoice ?? '…'}</span>
	</div>
	{#if selectedPipeline && selectedPipeline !== config.pipeline}
		<p class="notice">
			The HUD picks its pipeline from <code>JARVIS_PIPELINE</code> at load. Set it to
			<code>{selectedPipeline}</code> on the server to make this stick.
		</p>
	{/if}
</section>

<section class="panel">
	<div class="panel-head">
		<span>Event stream</span>
		<span class="muted" data-testid="live-filter">{liveFilter || '(all events)'}</span>
	</div>
	<div class="row">
		<input
			type="text"
			placeholder="event_type filter (blank = everything)"
			data-testid="event-filter"
			bind:value={eventFilter}
			onkeydown={(e) => e.key === 'Enter' && applyFilter()}
		/>
		<button class="btn" data-testid="apply-filter" onclick={applyFilter}>SUBSCRIBE</button>
		<button class="btn ghost" data-testid="pause" onclick={() => (paused = !paused)}>
			{paused ? 'RESUME' : 'PAUSE'}
		</button>
		<button class="btn ghost" onclick={() => (log = [])}>CLEAR</button>
		<span class="muted" data-testid="event-count">{log.length}</span>
	</div>
	<pre data-testid="event-log">{log
			.map((e) => `${e.at}  ${e.type}  ${e.body}`)
			.join('\n') || 'waiting for events…'}</pre>
</section>
