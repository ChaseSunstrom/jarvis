<script lang="ts">
	import { onMount } from 'svelte';
	import { openConnection, describeError, relayUrl, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import type { BusEvent, SettingRow, Subscription } from '$lib/jarvisClient';

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

	/**
	 * The pipelines the backend reports, named, with the preferred one marked.
	 *
	 * Shown as text rather than a `<select>`. There used to be a dropdown here,
	 * and picking from it did nothing at all — the HUD reads `JARVIS_PIPELINE`
	 * from the server's environment at load, so the control could only ever
	 * print a note telling you to go and edit an environment variable. A
	 * disabled-looking list of what exists is honest; a control that cannot
	 * commit its own value is not.
	 */
	let pipelineNames = $derived(
		pipelines.map((p) => (p.id === preferred ? `${p.name} (preferred)` : p.name))
	);

	/**
	 * The editable settings jarvis-core exposes, grouped as it groups them.
	 *
	 * Distinct from the `Backend` panel below, which shows *this web server's*
	 * environment. Those are genuinely not editable from here: the admin token
	 * never reaches the browser, so a page that offered to change it would be
	 * offering something it cannot do.
	 */
	let settings = $state<SettingRow[]>([]);
	let settingsSupported = $state(true);
	/** Per-key draft values, so typing does not fight the server's copy. */
	let drafts = $state<Record<string, string>>({});
	let busyKey = $state('');
	let fieldError = $state<Record<string, string>>({});
	let restartNeeded = $state<string[]>([]);

	let groups = $derived.by(() => {
		const byGroup = new Map<string, SettingRow[]>();
		for (const row of settings) {
			const list = byGroup.get(row.group) ?? [];
			list.push(row);
			byGroup.set(row.group, list);
		}
		return [...byGroup.entries()];
	});

	function draftOf(row: SettingRow): string {
		return drafts[row.key] ?? (row.value == null ? '' : String(row.value));
	}

	function isDirty(row: SettingRow): boolean {
		const current = row.value == null ? '' : String(row.value);
		return drafts[row.key] !== undefined && drafts[row.key] !== current;
	}

	function adopt(rows: SettingRow[]): void {
		settings = rows;
		// Drop drafts the server has now confirmed, so the field stops showing
		// as edited once it has been saved.
		const next: Record<string, string> = {};
		for (const row of rows) {
			const draft = drafts[row.key];
			const current = row.value == null ? '' : String(row.value);
			if (draft !== undefined && draft !== current) next[row.key] = draft;
		}
		drafts = next;
	}

	async function loadSettings(): Promise<void> {
		if (!conn) return;
		try {
			adopt((await conn.client.listSettings())?.settings ?? []);
		} catch (e) {
			// An older jarvis-core has no settings API. The rest of the page is
			// still useful, so say so once rather than showing an error.
			settingsSupported = false;
			console.warn('settings unavailable', e);
		}
	}

	function noteResult(key: string, result: { restart_required: boolean }): void {
		const rest = restartNeeded.filter((k) => k !== key);
		restartNeeded = result.restart_required ? [...rest, key] : rest;
	}

	/** Coerce a form string back to what the setting's type wants. */
	function valueFor(row: SettingRow, raw: string): unknown {
		if (row.type === 'boolean') return raw === 'true';
		if (row.type === 'number' || row.type === 'integer') return raw.trim();
		return raw;
	}

	async function saveSetting(row: SettingRow): Promise<void> {
		if (!conn) return;
		busyKey = row.key;
		fieldError = { ...fieldError, [row.key]: '' };
		try {
			const result = await conn.client.setSetting(row.key, valueFor(row, draftOf(row)));
			adopt(result.settings ?? settings);
			noteResult(row.key, result);
			toasts.success(`${row.label} saved`, result.restart_required ? 'restart to apply' : 'in effect now');
		} catch (e) {
			fieldError = { ...fieldError, [row.key]: describeError(e) };
		} finally {
			busyKey = '';
		}
	}

	async function resetSetting(row: SettingRow): Promise<void> {
		if (!conn) return;
		busyKey = row.key;
		fieldError = { ...fieldError, [row.key]: '' };
		try {
			const result = await conn.client.resetSetting(row.key);
			const { [row.key]: _dropped, ...rest } = drafts;
			drafts = rest;
			adopt(result.settings ?? settings);
			noteResult(row.key, result);
			toasts.success(`${row.label} reset`);
		} catch (e) {
			fieldError = { ...fieldError, [row.key]: describeError(e) };
		} finally {
			busyKey = '';
		}
	}

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
			toasts.success(`Subscribed to ${liveFilter}`);
		} catch (e) {
			err = describeError(e);
			toasts.error('Subscription failed', describeError(e));
		}
	}

	onMount(() => {
		let disposed = false;
		fetch('/api/config')
			.then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/api/config → ${r.status}`))))
			.then((c) => (config = c))
			// Was swallowed silently, which left the whole panel showing
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
				await loadSettings();
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

{#if err}<p class="err" data-testid="error" role="alert">{err}</p>{/if}
{#if hint}<p class="notice" data-testid="hint">{hint}</p>{/if}
{#if config.problem}<p class="err" data-testid="config-problem" role="alert">{config.problem}</p>{/if}

{#if restartNeeded.length}
	<p class="notice" data-testid="restart-needed">
		Saved, but {restartNeeded.length === 1 ? 'this setting needs' : 'these settings need'} a restart
		of jarvis-core to take effect: {restartNeeded.join(', ')}.
	</p>
{/if}

{#if settingsSupported}
	{#each groups as [group, rows] (group)}
		<section class="panel" data-testid="group-{group.toLowerCase()}">
			<div class="panel-head"><span>{group}</span></div>
			{#each rows as row (row.key)}
				{@const locked = row.source === 'package'}
				<div class="row" data-testid="setting-{row.key}">
					<span class="name">
						<b>{row.label}</b>
						<span class="eid">{row.key}</span>
					</span>

					{#if row.type === 'choice' && row.choices?.length}
						<select
							aria-label={row.label}
							data-testid="input-{row.key}"
							disabled={locked}
							value={draftOf(row)}
							onchange={(e) => (drafts = { ...drafts, [row.key]: e.currentTarget.value })}
						>
							{#each row.choices as choice (choice)}<option value={choice}>{choice}</option>{/each}
							{#if !row.choices.includes(draftOf(row))}
								<!-- What is configured is not among what could be discovered.
								     Shown rather than silently reset to the first option. -->
								<option value={draftOf(row)}>{draftOf(row) || '(unset)'}</option>
							{/if}
						</select>
					{:else if row.type === 'boolean'}
						<select
							aria-label={row.label}
							data-testid="input-{row.key}"
							disabled={locked}
							value={draftOf(row)}
							onchange={(e) => (drafts = { ...drafts, [row.key]: e.currentTarget.value })}
						>
							<option value="true">on</option>
							<option value="false">off</option>
						</select>
					{:else}
						<input
							type={row.type === 'string' || row.type === 'choice' ? 'text' : 'number'}
							aria-label={row.label}
							data-testid="input-{row.key}"
							disabled={locked}
							value={draftOf(row)}
							oninput={(e) => (drafts = { ...drafts, [row.key]: e.currentTarget.value })}
						/>
					{/if}

					<span class="pill" class:on={row.source === 'overlay'} data-testid="source-{row.key}">
						{row.source}
					</span>

					<button
						type="button"
						class="btn"
						data-testid="save-{row.key}"
						disabled={locked || busyKey === row.key || !isDirty(row)}
						onclick={() => saveSetting(row)}
					>
						{busyKey === row.key ? '…' : 'SAVE'}
					</button>
					{#if row.source === 'overlay' || row.source === 'unapplied'}
						<button
							type="button"
							class="btn ghost"
							data-testid="reset-{row.key}"
							disabled={busyKey === row.key}
							aria-label="Reset {row.label} to the value in configuration.yaml"
							onclick={() => resetSetting(row)}
						>
							RESET
						</button>
					{/if}
				</div>

				{#if row.note}<p class="muted note" data-testid="note-{row.key}">{row.note}</p>{/if}
				{#if row.unapplied_reason}
					<p class="err" data-testid="unapplied-{row.key}" role="alert">{row.unapplied_reason}</p>
				{:else if locked}
					<p class="muted" data-testid="package-{row.key}">
						Set by packages/{row.package}.yaml — edit that file to change it.
					</p>
				{/if}
				{#if fieldError[row.key]}
					<p class="err" data-testid="error-{row.key}" role="alert">{fieldError[row.key]}</p>
				{/if}
			{/each}
		</section>
	{/each}
{/if}

<!--
  This console's OWN environment, as opposed to the house settings above.

  Everything in this panel is a server-side environment variable of the web
  server, readable and not settable from a browser — the token in particular
  never leaves the server. Keeping it visually apart from the editable groups
  is the point: a row you cannot change, sitting among rows you can, reads as
  a control that is broken.
-->
<section class="panel" data-testid="console-env">
	<div class="panel-head">
		<span>This console</span>
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
	<div class="row">
		<span class="name"><b>Voice pipeline</b><span class="eid">JARVIS_PIPELINE</span></span>
		<span class="muted" data-testid="pipeline-name">
			{config.pipeline || 'not set'}{#if pipelineNames.length}
				<span class="eid"> · available: {pipelineNames.join(', ')}</span>
			{/if}
		</span>
	</div>
	<p class="muted">
		These are server-side environment variables — the browser never receives the token. Change
		<code>JARVIS_BACKEND</code>, <code>JARVIS_URL</code>, <code>JARVIS_TOKEN</code> or
		<code>JARVIS_PIPELINE</code> where the web server runs, then restart it.
	</p>
</section>

<!--
  A diagnostic, folded away.

  It is a raw firehose of every event on the bus, and it was sitting open at
  the bottom of the settings page as if it were a setting — the longest panel
  on the screen, below the things people actually came to change. Collapsed by
  default, one click away, and the summary says what is inside so nobody has to
  open it to find out.
-->
<details class="panel" data-testid="event-stream">
	<summary class="panel-head">
		<span>Event stream</span>
		<span class="muted" data-testid="live-filter">{liveFilter || '(all events)'}</span>
	</summary>
	<div class="row">
		<label class="jv-sr-only" for="event-filter">Event type filter</label>
		<input
			id="event-filter"
			type="text"
			placeholder="event_type filter (blank = everything)  ( / )"
			data-testid="event-filter"
			data-jv-filter
			bind:value={eventFilter}
			onkeydown={(e) => e.key === 'Enter' && applyFilter()}
		/>
		<button type="button" class="btn" data-testid="apply-filter" onclick={applyFilter}>
			SUBSCRIBE
		</button>
		<button
			type="button"
			class="btn ghost"
			data-testid="pause"
			aria-pressed={paused}
			onclick={() => (paused = !paused)}
		>
			{paused ? 'RESUME' : 'PAUSE'}
		</button>
		<button type="button" class="btn ghost" aria-label="Clear the event log" onclick={() => (log = [])}>
			CLEAR
		</button>
		<span class="muted" data-testid="event-count">{log.length}</span>
	</div>
	<pre data-testid="event-log" aria-label="Live event stream">{log
			.map((e) => `${e.at}  ${e.type}  ${e.body}`)
			.join('\n') || 'waiting for events…'}</pre>
</details>

<style>
	/* A setting's note belongs under its row, indented to the row's control
	   column so it reads as belonging to that setting and not the next one. */
	.note {
		margin: 0 0 0.5rem;
	}

	/* The collapsed diagnostic. `.panel-head` already lays this out; the marker
	   is replaced with one that reads as part of the console's chrome rather
	   than as a browser default triangle. */
	details.panel > summary {
		cursor: pointer;
		list-style: none;
	}
	details.panel > summary::-webkit-details-marker {
		display: none;
	}
	details.panel > summary::after {
		content: '▸';
		color: var(--jv-text-faint);
		margin-left: auto;
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	details.panel[open] > summary::after {
		transform: rotate(90deg);
	}
	@media (prefers-reduced-motion: reduce) {
		details.panel > summary::after {
			transition: none;
		}
	}
</style>
