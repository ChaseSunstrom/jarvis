<!--
  SETTINGS › SYSTEM (M114): every variable `.env.example` names, set from
  here and kept. The operator's report of 27 Aug 2026: "allow setting all
  .env variables in the jarvis console settings, and have them persist".

  What a row says: the name, the why (the comment above it in
  .env.example), which value is live (the container's environment, or one
  set here at the last boot), and whether a change waits for a restart. A
  secret is masked everywhere until REVEAL. What is set here is kept in the
  house's store and applied over the container's environment at the next
  boot — the file on the host is never written — so every change carries
  "applies on restart", and RESTART JARVIS is beside the list.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { describeError } from '$lib/connection';
	import { SectionLink } from '$lib/sectionLink.svelte';
	import { toasts } from '$lib/toast';
	import { Button, Dialog, Input, Panel, Pill, ScreenState, SettingRow, SkeletonRows } from '$lib/ui';

	interface Variable {
		name: string;
		why: string;
		default: string;
		secret: boolean;
		section: string;
		set: boolean;
		source: 'override' | 'environment' | 'unset';
		is_set_in_environment: boolean;
		pending: boolean;
		value: string | null;
		live: string | null;
	}

	let loaded = $state(false);
	let hint = $state('');
	let variables = $state<Variable[]>([]);
	let pendingCount = $state(0);
	let drafts = $state<Record<string, string>>({});
	let revealed = $state<Record<string, string>>({});
	let busy = $state('');
	let filter = $state('');
	let confirmRestart = $state(false);
	let restarting = $state(false);

	const link = new SectionLink(async (conn) => {
		await load(conn);
	});

	// The link opens with the section and closes with it — the one line the
	// first cut lacked, so CI's four environment cases waited on a skeleton.
	onMount(() => {
		void link.connect();
		return () => link.dispose();
	});

	async function load(conn = link.conn): Promise<void> {
		if (!conn) return;
		try {
			const answer = await conn.client.command<{ variables?: Variable[]; pending?: number }>({
				type: 'jarvis/environment/list'
			});
			variables = answer.variables ?? [];
			pendingCount = answer.pending ?? 0;
			hint = '';
		} catch (e) {
			hint = describeError(e);
		} finally {
			loaded = true;
		}
	}

	const shown = $derived(
		variables.filter((v) => {
			const q = filter.trim().toLowerCase();
			return !q || `${v.name} ${v.why} ${v.section}`.toLowerCase().includes(q);
		})
	);
	const setCount = $derived(variables.filter((v) => v.set).length);

	async function save(v: Variable): Promise<void> {
		if (!link.conn) return;
		busy = v.name;
		try {
			const answer = await link.conn.client.command<{ status?: string; error?: string }>({
				type: 'jarvis/environment/set',
				name: v.name,
				value: drafts[v.name] ?? ''
			});
			if (answer.error) {
				toasts.push('error', answer.error);
				return;
			}
			delete drafts[v.name];
			delete revealed[v.name];
			toasts.push('success', `${v.name} kept — applies on restart`);
			await load();
		} catch (e) {
			toasts.push('error', describeError(e));
		} finally {
			busy = '';
		}
	}

	async function clear(v: Variable): Promise<void> {
		if (!link.conn) return;
		busy = v.name;
		try {
			const answer = await link.conn.client.command<{ status?: string; error?: string }>({
				type: 'jarvis/environment/clear',
				name: v.name
			});
			if (answer.error) toasts.push('error', answer.error);
			else toasts.push('success', `${v.name} cleared — the environment's own value applies on restart`);
			delete revealed[v.name];
			await load();
		} catch (e) {
			toasts.push('error', describeError(e));
		} finally {
			busy = '';
		}
	}

	async function reveal(v: Variable): Promise<void> {
		if (!link.conn) return;
		try {
			const answer = await link.conn.client.command<{ value?: string | null; error?: string }>({
				type: 'jarvis/environment/reveal',
				name: v.name
			});
			if (answer.error) toasts.push('error', answer.error);
			else revealed[v.name] = answer.value ?? '';
		} catch (e) {
			toasts.push('error', describeError(e));
		}
	}

	async function restart(): Promise<void> {
		if (!link.conn) return;
		restarting = true;
		try {
			await link.conn.client.command({ type: 'jarvis/system/restart' });
			toasts.push('success', 'Restarting Jarvis — back in a moment');
		} catch (e) {
			toasts.push('error', describeError(e));
		} finally {
			confirmRestart = false;
			restarting = false;
		}
	}

	function liveOf(v: Variable): string {
		if (v.source === 'override') return `set here, live since the last boot`;
		if (v.source === 'environment') return `from the container's environment`;
		return 'not set';
	}
</script>

<section class="system" data-testid="settings-system">
	<p class="lede" data-testid="settings-system-lede">
		Every variable <code>.env.example</code> names. What is set here is kept and applied over the
		container's environment at the next restart, before configuration is read; the file on the host is
		never written. {setCount} set here{pendingCount ? ` · ${pendingCount} waiting for a restart` : ''}.
	</p>

	<ScreenState
		status={link.screen}
		errorTitle="The environment could not be read"
		errorDetail={link.err || hint}
		onretry={() => link.connect()}
		onreconnect={() => link.connect()}
		busy={link.redialling}
		errorTestid="settings-system-error"
	/>

	{#if !loaded}
		<SkeletonRows rows={6} />
	{:else}
		<Panel title="Restart" meta={pendingCount ? `${pendingCount} change(s) waiting` : 'nothing waiting'} testid="system-restart">
			{#snippet children()}
				<SettingRow label="Restart Jarvis" why="Stops the process cleanly; the container's restart policy brings it back with what is set here applied." testid="restart-row">
					<span class="muted">{pendingCount ? 'Changes below apply once it is back.' : 'Nothing is waiting; a restart changes nothing.'}</span>
					{#snippet acts()}
						<Button testid="system-restart-button" variant={pendingCount ? 'primary' : undefined} onclick={() => (confirmRestart = true)}>RESTART JARVIS</Button>
					{/snippet}
				</SettingRow>
			{/snippet}
		</Panel>

		<Panel title="Environment" meta={`${variables.length} variables`} testid="system-environment">
			{#snippet children()}
				<div class="filter">
					<Input bind:value={filter} placeholder="filter by name or words" testid="env-filter" mono />
				</div>
				{#each shown as v (v.name)}
					<SettingRow why={v.why || v.section} testid="env-{v.name}" live={v.pending}>
						{#snippet what()}
							<b><code>{v.name}</code></b>
							{#if v.secret}<Pill tone="neutral">secret</Pill>{/if}
							{#if v.pending}<Pill tone="warn" testid="env-pending-{v.name}">applies on restart</Pill>{/if}
						{/snippet}
						<div class="cell">
							<span class="live" data-testid="env-live-{v.name}">
								{liveOf(v)}{v.live !== null ? ` · ${revealed[v.name] ?? v.live}` : ''}{v.set && v.source !== 'override' ? ` · set here: ${revealed[v.name] ?? v.value}` : ''}
							</span>
							{#if v.secret}
								<input
									class="secret"
									type="password"
									autocomplete="off"
									placeholder={v.set ? 'new value' : v.default || 'value'}
									data-testid="env-input-{v.name}"
									value={drafts[v.name] ?? ''}
									oninput={(e) => (drafts[v.name] = (e.currentTarget as HTMLInputElement).value)}
								/>
							{:else}
								<Input
									value={drafts[v.name] ?? ''}
									placeholder={v.set ? 'new value' : v.default || 'value'}
									testid="env-input-{v.name}"
									mono
									oninput={(e) => (drafts[v.name] = (e.currentTarget as HTMLInputElement).value)}
								/>
							{/if}
						</div>
						{#snippet acts()}
							<Button testid="env-set-{v.name}" disabled={busy === v.name || drafts[v.name] === undefined} onclick={() => save(v)}>SET</Button>
							{#if v.set}
								<Button testid="env-clear-{v.name}" disabled={busy === v.name} onclick={() => clear(v)}>CLEAR</Button>
							{/if}
							{#if v.secret && (v.set || v.live !== null) && revealed[v.name] === undefined}
								<Button testid="env-reveal-{v.name}" onclick={() => reveal(v)}>REVEAL</Button>
							{/if}
						{/snippet}
					</SettingRow>
				{:else}
					<p class="muted" data-testid="env-no-match">Nothing matches.</p>
				{/each}
			{/snippet}
		</Panel>
	{/if}
</section>

<Dialog open={confirmRestart} title="Restart Jarvis?" onclose={() => (confirmRestart = false)}>
	<p>
		The house stops answering for a few seconds and comes back with what is set here applied. A job
		that is running is picked back up if its worker said it could be.
	</p>
	{#snippet actions()}
		<Button onclick={() => (confirmRestart = false)}>CANCEL</Button>
		<Button variant="primary" testid="system-restart-confirm" disabled={restarting} onclick={restart}>RESTART</Button>
	{/snippet}
</Dialog>

<style>
	.lede {
		margin: 0 0 var(--jv-space-4);
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.filter {
		margin-bottom: var(--jv-space-3);
		max-width: 24rem;
	}
	.cell {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.live {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.secret {
		width: 100%;
		font: inherit;
		font-family: var(--jv-font-mono, ui-monospace, monospace);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		background: var(--jv-panel-raised, transparent);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		padding: var(--jv-space-2) var(--jv-space-3);
	}
	.muted {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
</style>
