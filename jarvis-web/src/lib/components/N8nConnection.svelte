<!--
  The house's n8n (M77), as one line on Settings › Tools: whether it answers
  and how many workflows it holds — or what to set for it to. The operator
  judges capability from this screen, and a connection that exists only in
  .env is invisible to them.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import type { Connection } from '$lib/connection';
	import { describeError } from '$lib/connection';
	import { Pill } from '$lib/ui';

	let { conn }: { conn: Connection | null } = $props();

	type Status = {
		status: 'ok' | 'not_configured' | 'unreachable' | string;
		url?: string;
		assistant_url?: string;
		workflows?: number;
		active?: number;
		error?: string;
	};
	let status = $state<Status | null>(null);
	let error = $state('');
	let loading = $state(true);

	async function load(connection: Connection) {
		loading = true;
		error = '';
		try {
			const result = await connection.client.callService('n8n', 'status', {}, { returnResponse: true });
			// The websocket keys a service response `response` — `service_response` is Home
			// Assistant's REST name, and reading it here made the whole envelope the status,
			// so the row said "connected" to a house with no n8n (27 Aug 2026).
			const payload = (result as { response?: Status })?.response ?? (result as Status);
			status = payload && typeof payload === 'object' ? payload : null;
		} catch (e) {
			error = describeError(e);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const connection = conn;
		if (connection) void load(connection);
	});
	onMount(() => {});
</script>

<section class="n8n" data-testid="n8n-connection" aria-label="n8n">
	<div class="row">
		<span class="name">n8n</span>
		{#if loading}
			<span class="dim" data-testid="n8n-loading">asking…</span>
		{:else if error}
			<span class="bad" role="alert" data-testid="n8n-error">{error}</span>
		{:else if !status || status.status === 'not_configured'}
			<Pill tone="neutral" testid="n8n-state">not configured</Pill>
			<span class="dim">set <code>N8N_URL</code> and <code>N8N_API_KEY</code> in <code>.env</code>; the tools then list, run and build workflows under approval</span>
		{:else if status.status === 'unreachable'}
			<Pill tone="warn" testid="n8n-state">unreachable</Pill>
			<span class="dim">{status.url} — {status.error}</span>
		{:else}
			<Pill tone="live" testid="n8n-state">connected</Pill>
			<span class="dim">{status.url} · {status.workflows ?? 0} workflow{status.workflows === 1 ? '' : 's'}, {status.active ?? 0} active</span>
		{/if}
	</div>
</section>

<style>
	.n8n {
		border-top: 1px solid var(--jv-line-hair);
		padding: var(--jv-space-3) 0;
	}
	.row {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.name {
		font-size: var(--jv-fs-xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.dim {
		color: var(--jv-text-dim);
	}
	.bad {
		color: var(--jv-danger-text);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
	}
</style>
