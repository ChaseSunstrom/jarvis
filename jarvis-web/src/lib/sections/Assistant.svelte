<script lang="ts">
	import EnrolVoice from '$lib/components/EnrolVoice.svelte';
	import Pairing from '$lib/components/Pairing.svelte';
	import { onMount } from 'svelte';
	import { openConnection, describeError, relayUrl, type Connection } from '$lib/connection';
	import { toasts } from '$lib/toast';
	import { TEXT_SIZES, applyTextSize, readTextSize, writeTextSize } from '$lib/textSize';
	import { coerceSetting } from '$lib/settingsDraft';
	import type { BusEvent, SettingRow, Subscription } from '$lib/jarvisClient';
	import { Button, Input, Panel, Pill, ScreenState, Select, SkeletonRows } from '$lib/ui';

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
	 * Distinct from the `This console` panel below, which shows *this web
	 * server's* environment. Those are genuinely not editable from here: the
	 * admin token never reaches the browser, so a page that offered to change
	 * it would be offering something it cannot do.
	 */
	let settings = $state<SettingRow[]>([]);
	let settingsSupported = $state(true);
	/** Per-key draft values, so typing does not fight the server's copy. */
	let drafts = $state<Record<string, string>>({});
	let busyKey = $state('');
	let fieldError = $state<Record<string, string>>({});
	let restartNeeded = $state<string[]>([]);

	/**
	 * Whose voice Jarvis answers, as the server reports it.
	 *
	 * This was read-only plus a delete, on the reasoning that the phone has the
	 * microphone and that a second enrolment surface would be a second place
	 * for the prompt list to drift. The first is a preference and browsers have
	 * microphones too; the second stopped being true — the phrases live in
	 * jarvis-core and arrive in this very payload as `prompts`, so both
	 * surfaces read one list from one place. See `docs/voice-identity.md`.
	 *
	 * What actually kept enrolment off this page was the credential: the relay
	 * demands the caller's own Jarvis token and refuses to fall back to the
	 * admin one, and a browser holds neither. It now also accepts an unlocked
	 * console session, which is the same door already in front of the pairing
	 * secret and in front of FORGET below.
	 *
	 * The payload never contains the voiceprint — counts, scores and timestamps
	 * only — so "is somebody enrolled" cannot also answer "what do they sound
	 * like". That is enforced on the server, not here.
	 */
	let speaker = $state<Record<string, any> | null>(null);
	/** The same payload, never null: what the panel reads. A snippet does not carry the `{#if}`'s narrowing. */
	const who = $derived<Record<string, any>>(speaker ?? {});
	let speakerBusy = $state(false);
	let speakerError = $state('');

	async function loadSpeaker(): Promise<void> {
		const res = await fetch('/api/voice/speaker').catch(() => null);
		if (!res || !res.ok) {
			speaker = null;
			return;
		}
		speaker = await res.json().catch(() => null);
	}

	async function forgetVoice(): Promise<void> {
		if (speakerBusy) return;
		speakerBusy = true;
		speakerError = '';
		try {
			const res = await fetch('/api/voice/speaker', { method: 'DELETE' });
			if (!res.ok) {
				// 401 is the console lock, and it is the likely one — say what to
				// do rather than showing a status code.
				speakerError =
					res.status === 401
						? 'Unlock the console with its password first (the pairing panel above).'
						: await failureText(res);
				return;
			}
			toasts.success('Voiceprint deleted', 'Jarvis answers any voice again.');
			await loadSpeaker();
		} finally {
			speakerBusy = false;
		}
	}

	async function failureText(res: Response): Promise<string> {
		const body = await res.json().catch(() => null);
		return (body && (body.message || body.detail)) || `the server answered ${res.status}`;
	}

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

	/** The choices a `<Select>` offers, with the configured value kept even when it is not among them. */
	function choicesOf(row: SettingRow): { value: string; label: string }[] {
		const choices = (row.choices ?? []).map((choice) => ({ value: choice, label: choice }));
		const current = draftOf(row);
		// What is configured is not among what could be discovered. Shown rather
		// than silently reset to the first option.
		if (!(row.choices ?? []).includes(current)) choices.push({ value: current, label: current || '(unset)' });
		return choices;
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

	async function saveSetting(row: SettingRow): Promise<void> {
		if (!conn) return;
		busyKey = row.key;
		fieldError = { ...fieldError, [row.key]: '' };
		try {
			const result = await conn.client.setSetting(row.key, coerceSetting(row.type, draftOf(row)));
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

	/**
	 * How big the text is, in this browser.
	 *
	 * Not a house setting and deliberately not sent anywhere: it is a property of
	 * the screen somebody is reading, and the same house is read from a phone at
	 * arm's length and a wall panel across a room. Applied the moment it is
	 * picked — a text-size control you have to save is one you cannot judge.
	 */
	let textSize = $state('standard');

	function chooseTextSize(id: string): void {
		const size = writeTextSize(localStorage, id);
		applyTextSize(document, size);
		textSize = size.id;
		toasts.success(`Text size · ${size.label}`, 'Remembered in this browser.');
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

	// Dial, load, subscribe — as a function the RECONNECT button can run again.
	// See `$lib/ui` OfflineState for why a page’s socket does not reattach.
	let disposed = false;
	let redialling = $state(false);
	// The socket being replaced reports its close asynchronously; without a
	// generation the late 'closed' overwrites the new socket's 'open'.
	let dial = 0;

	async function connect(): Promise<void> {
		if (redialling) return;
		redialling = true;
		const mine = ++dial;
		void sub?.unsubscribe();
		sub = null;
		conn?.close();
		conn = null;
		err = '';
		try {
			const connection = await openConnection({
				onStatus: (s) => {
					if (mine === dial) status = s;
				}
			});
			if (disposed || mine !== dial) {
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
		} finally {
			redialling = false;
		}
	}

	onMount(() => {
		disposed = false;
		textSize = readTextSize(localStorage).id;
		loadSpeaker();
		fetch('/api/config')
			.then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/api/config → ${r.status}`))))
			.then((c) => (config = c))
			// Was swallowed silently, which left the whole panel showing
			// placeholders with nothing to explain why.
			.catch((e) => (hint = describeError(e)));

		void connect();

		return () => {
			disposed = true;
			void sub?.unsubscribe();
			conn?.close();
			conn = null;
		};
	});

	// The screen's status region. Loading and empty belong to the individual
	// lists below (this page has more than one); what is page-wide is the link
	// being down and the page's own failure, and `ScreenState` owns both.
	let screen = $derived<'ready' | 'error' | 'offline'>(
		status === 'closed' || status === 'error' ? 'offline' : err ? 'error' : 'ready'
	);
</script>

<div class="stack">
	<p class="lede" data-testid="assistant-screen">
		link {status} · relay <code>{typeof location === 'undefined' ? '' : relayUrl()}</code>
	</p>

	<ScreenState
		status={screen}
		errorTitle="This page hit an error"
		errorDetail={err}
		onretry={connect}
		onreconnect={connect}
		busy={redialling}
		errorTestid="error"
	/>

	{#if hint}<p class="line warn" data-testid="hint">{hint}</p>{/if}
	{#if config.problem}<p class="line bad" data-testid="config-problem" role="alert">{config.problem}</p>{/if}

	{#if restartNeeded.length}
		<p class="line" data-testid="restart-needed">
			<Pill tone="warn">needs a restart</Pill>
			<span>
				Saved, but {restartNeeded.length === 1 ? 'this setting needs' : 'these settings need'} a
				restart of jarvis-core to take effect: {restartNeeded.join(', ')}.
			</span>
		</p>
	{/if}

	{#if settingsSupported}
		{#if !groups.length && status !== 'closed' && status !== 'error'}
			<!-- Connected, and told nothing yet: the window a skeleton is for. The
			     settings arrive in one command, so this is usually brief — and a brief
			     blank page is still a blank page. -->
			<Panel title="Settings" meta="…">
				{#snippet children()}<SkeletonRows rows={6} label="Loading settings" />{/snippet}
			</Panel>
		{/if}
		{#each groups as [group, rows] (group)}
			<Panel title={group} meta={`${rows.length}`} testid="group-{group.toLowerCase()}">
				{#snippet children()}
					{#each rows as row (row.key)}
						{@const locked = row.source === 'package'}
						<div class="setting" data-testid="setting-{row.key}">
							<div class="what">
								<b>{row.label}</b>
								<code>{row.key}</code>
							</div>
							<div class="control">
								{#if row.type === 'choice' && row.choices?.length}
									<Select
										value={draftOf(row)}
										testid="input-{row.key}"
										disabled={locked}
										options={choicesOf(row)}
										onchange={(e) =>
											(drafts = { ...drafts, [row.key]: (e.currentTarget as HTMLSelectElement).value })}
									/>
								{:else if row.type === 'boolean'}
									<Select
										value={draftOf(row)}
										testid="input-{row.key}"
										disabled={locked}
										options={[
											{ value: 'true', label: 'on' },
											{ value: 'false', label: 'off' }
										]}
										onchange={(e) =>
											(drafts = { ...drafts, [row.key]: (e.currentTarget as HTMLSelectElement).value })}
									/>
								{:else}
									<Input
										value={draftOf(row)}
										testid="input-{row.key}"
										disabled={locked}
										mono={row.type !== 'string'}
										oninput={(e) =>
											(drafts = { ...drafts, [row.key]: (e.currentTarget as HTMLInputElement).value })}
									/>
								{/if}
							</div>
							<div class="acts">
								<Pill tone={row.source === 'overlay' ? 'live' : 'neutral'} testid="source-{row.key}">{row.source}</Pill>
								<!-- SAVE is lit only once something changed: the accent is spent on
								     the one thing on this page that is about to happen. -->
								<Button
									variant={isDirty(row) ? 'primary' : 'ghost'}
									testid="save-{row.key}"
									disabled={locked || busyKey === row.key || !isDirty(row)}
									title={locked
										? 'This setting is fixed in configuration.yaml'
										: busyKey === row.key
											? 'Saving'
											: !isDirty(row)
												? 'Nothing has changed yet'
												: `Save ${row.label}`}
									onclick={() => saveSetting(row)}
								>
									{busyKey === row.key ? '…' : 'SAVE'}
								</Button>
								{#if row.source === 'overlay' || row.source === 'unapplied'}
									<Button
										testid="reset-{row.key}"
										disabled={busyKey === row.key}
										title="Put the value in configuration.yaml back"
										aria-label="Reset {row.label} to the value in configuration.yaml"
										onclick={() => resetSetting(row)}
									>
										RESET
									</Button>
								{/if}
							</div>
							{#if row.note}<p class="note" data-testid="note-{row.key}">{row.note}</p>{/if}
							{#if row.unapplied_reason}
								<p class="note bad" data-testid="unapplied-{row.key}" role="alert">{row.unapplied_reason}</p>
							{:else if locked}
								<p class="note" data-testid="package-{row.key}">
									Set by packages/{row.package}.yaml — edit that file to change it.
								</p>
							{/if}
							{#if fieldError[row.key]}
								<p class="note bad" data-testid="error-{row.key}" role="alert">{fieldError[row.key]}</p>
							{/if}
						</div>
					{/each}
				{/snippet}
			</Panel>
		{/each}
	{/if}

	<!--
	  Text size.

	  Above the pairing panel because it is the one setting on this page that
	  changes what you can read while you read it, and below the house settings
	  because it changes nothing about the house. It is stored in this browser and
	  goes nowhere near jarvis-core: the right size for a phone in a hallway and
	  for a monitor on a desk are different answers to the same question, and a
	  house-wide value would have to be wrong for one of them.
	-->
	<Panel title="Text size" meta="this browser only" testid="text-size">
		{#snippet children()}
			<div class="setting">
				<div class="what">
					<b>Scale</b>
					<span class="dim">multiplies every size in the interface</span>
				</div>
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
			</div>
			<p class="note">
				STANDARD is whatever text size this browser is already set to — so raising it in the
				browser and raising it here compound, which is the intent. Everything in the console and
				the voice screen is sized in <code>rem</code>, so one number moves all of it at once.
			</p>
		{/snippet}
	</Panel>

	<Pairing />

	<!--
	  Whose voice Jarvis answers.

	  Shown here because this is where an operator finds out what the house is
	  doing; enrolled FROM THE PHONE, because that is where the microphone is. The
	  numbers are on screen for the same reason the phone shows them: a biometric
	  gate whose threshold was guessed is a gate that locks the owner out, and the
	  only defence is being able to see the owner's own scores before enforcing.
	-->
	{#if who.supported}
		<Panel title="Whose voice" meta={who.enrolled ? 'enrolled' : 'nobody enrolled'} testid="voice-identity">
			{#snippet children()}
				<div class="setting">
					<div class="what"><b>Mode</b><code>voice: speaker: mode</code></div>
					<span class="value">
						<Pill tone={who.active ? 'live' : 'neutral'} testid="speaker-mode">{who.mode ?? 'off'}</Pill>
					</span>
				</div>
				<div class="setting">
					<div class="what"><b>Enrolled</b><code>voice_profile</code></div>
					<span class="value" data-testid="speaker-samples">
						{#if who.enrolled}
							{who.samples} of {who.max_samples} samples
						{:else}
							nobody — the gate is inert until somebody enrols
						{/if}
					</span>
				</div>

				{#if who.enrolled}
					<div class="setting">
						<div class="what">
							<b>Threshold</b><span class="dim">mean squared z · lower is stricter</span>
						</div>
						<span class="value" data-testid="speaker-threshold">
							{who.threshold}
							{#if who.threshold_measured !== false && who.worst_self_score != null}
								<span class="dim">
									· their own worst sample scores {who.worst_self_score}, enrolment
									suggests {who.suggested_threshold}
								</span>
							{:else}
								<!--
									Scoring one enrolment sample means holding it out and rebuilding the
									profile from the rest, and that rebuilt profile needs the minimum too.
									So at exactly the minimum there is nothing to measure with, and this
									row used to print "their own worst sample scores Infinity, enrolment
									suggests 4" — 4 being the server's default — beside advice that says
									to read the scores before enforcing.
								-->
								<span class="dim">
									· not measurable yet: scoring one sample needs {who.min_samples} others,
									so this needs {who.measure_samples ?? who.min_samples + 1} in all.
									{who.suggested_threshold} is the default, not a measurement.
								</span>
							{/if}
						</span>
					</div>
				{/if}

				<EnrolVoice status={speaker} onDone={loadSpeaker} />

				<div class="setting">
					<div class="what"><b>Forget this voice</b><span class="dim">deletes the voiceprint</span></div>
					<div class="acts">
						<Button
							variant="danger"
							testid="speaker-forget"
							disabled={!who.enrolled || speakerBusy}
							title={!who.enrolled
								? 'Nobody is enrolled'
								: speakerBusy
									? 'Deleting'
									: 'Delete the voiceprint — Jarvis answers any voice again'}
							onclick={forgetVoice}
						>
							{speakerBusy ? 'deleting…' : 'FORGET'}
						</Button>
					</div>
				</div>
				{#if speakerError}
					<p class="note bad" data-testid="speaker-error" role="alert">{speakerError}</p>
				{/if}

				<p class="note">
					Enrol here or from the phone; both read the same phrases from the server, and
					samples add up rather than replacing each other. Whether Jarvis <i>refuses</i> other voices is
					<code>voice: speaker: mode</code> in <code>configuration.yaml</code>, and the honest
					order is enrol, leave it in <code>observe</code> for a few days, read the scores, then
					<code>enforce</code>. It stops a guest, a television and a stranger at the window; it
					does not stop a recording, and it is not a second factor — the tier system still asks a
					human before anything irreversible.
				</p>
			{/snippet}
		</Panel>
	{/if}

	<!--
	  This console's OWN environment, as opposed to the house settings above.

	  Everything in this panel is a server-side environment variable of the web
	  server, readable and not settable from a browser — the token in particular
	  never leaves the server. Keeping it visually apart from the editable groups
	  is the point: a row you cannot change, sitting among rows you can, reads as
	  a control that is broken.
	-->
	<Panel title="This console" meta={config.backend ?? '…'} live={status === 'open'} testid="console-env">
		{#snippet children()}
			<div class="setting">
				<div class="what"><b>Backend</b><span class="dim">how this console reaches Jarvis</span></div>
				<span class="value">
					<Pill tone={status === 'open' ? 'live' : 'neutral'} testid="backend-kind">{config.backend ?? '…'}</Pill>
				</span>
			</div>
			<div class="setting">
				<div class="what"><b>URL</b><code>{config.backendUrlVar ?? 'JARVIS_URL'}</code></div>
				<span class="value mono" data-testid="backend-url">{config.backendUrl || 'not configured'}</span>
			</div>
			<div class="setting">
				<div class="what"><b>Token</b><code>{config.backendTokenVar ?? 'JARVIS_TOKEN'}</code></div>
				<span class="value" data-testid="backend-token">
					{config.tokenConfigured ? '•••••••• held server-side' : 'not configured'}
				</span>
			</div>
			<div class="setting">
				<div class="what"><b>Version</b><span class="dim">reported by the backend</span></div>
				<span class="value mono">{backendConfig?.version ?? backendConfig?.ha_version ?? 'unknown'}</span>
			</div>
			<div class="setting">
				<div class="what"><b>Voice pipeline</b><code>JARVIS_PIPELINE</code></div>
				<span class="value" data-testid="pipeline-name">
					{config.pipeline || 'not set'}{#if pipelineNames.length}
						<span class="dim"> · available: {pipelineNames.join(', ')}</span>
					{/if}
				</span>
			</div>
			<p class="note">
				These are server-side environment variables — the browser never receives the token. Change
				<code>JARVIS_BACKEND</code>, <code>JARVIS_URL</code>, <code>JARVIS_TOKEN</code> or
				<code>JARVIS_PIPELINE</code> where the web server runs, then restart it.
			</p>
		{/snippet}
	</Panel>

	<!--
	  A diagnostic, folded away.

	  It is a raw firehose of every event on the bus, and it was sitting open at
	  the bottom of the settings page as if it were a setting — the longest panel
	  on the screen, below the things people actually came to change. Collapsed by
	  default, one click away, and the summary says what is inside so nobody has to
	  open it to find out.
	-->
	<details class="fold" data-testid="event-stream">
		<summary>
			<span>Event stream</span>
			<span class="meta" data-testid="live-filter">{liveFilter || '(all events)'}</span>
		</summary>
		<div class="fold-body">
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
			</div>
			<pre data-testid="event-log" aria-label="Live event stream">{log
					.map((e) => `${e.at}  ${e.type}  ${e.body}`)
					.join('\n') || 'waiting for events…'}</pre>
		</div>
	</details>
</div>

<style>
	/* Panels stack down the page with one gutter between them. */
	.stack {
		display: grid;
		gap: var(--jv-space-4);
	}
	/* The section's one-line status. The destination's title and lede sit
	   above in the layout; this is what only this section knows. Set in the
	   body face — it is a sentence — with only the address in mono: the look
	   spec reads a whole mono paragraph as M48's monospace prose come back. */
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
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.line.warn {
		color: var(--jv-warn);
	}
	.line.bad,
	.note.bad {
		color: var(--jv-danger-text);
	}

	/* One setting: what it is, the control, the actions — on a hairline. */
	.setting {
		display: grid;
		grid-template-columns: minmax(12rem, 1fr) minmax(10rem, 1.4fr) auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
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
	.control {
		min-width: 0;
	}
	.value {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.acts {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	.note {
		grid-column: 1 / -1;
		margin: 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 80ch;
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

	/* The folded diagnostic: a panel whose head is its own disclosure. */
	.fold {
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		overflow: hidden;
	}
	.fold > summary {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		cursor: pointer;
		list-style: none;
	}
	.fold > summary::-webkit-details-marker {
		display: none;
	}
	.fold > summary::after {
		content: '▸';
		color: var(--jv-text-faint);
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	.fold[open] > summary {
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.fold[open] > summary::after {
		transform: rotate(90deg);
	}
	.meta,
	.count {
		font-family: var(--jv-font-chrome);
		font-weight: var(--jv-weight-body);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		text-transform: none;
		color: var(--jv-text-faint);
		margin-left: auto;
	}
	.fold-body {
		padding: var(--jv-space-4);
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
		.setting {
			grid-template-columns: minmax(0, 1fr);
		}
		.acts {
			justify-content: flex-start;
		}
		.seg {
			grid-column: 1;
			justify-self: start;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.fold > summary::after {
			transition: none;
		}
	}
</style>
