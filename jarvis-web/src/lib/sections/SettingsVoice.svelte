<script lang="ts">
	/**
	 * SETTINGS › Voice: the wake word, the voice, and whose voice it answers.
	 *
	 * The three rows a person comes for, in plain words; then the speaker
	 * identity panel, which used to sit at the bottom of one long Assistant
	 * page; then EVERYTHING the server files under Voice, as it describes it.
	 */
	import { onMount } from 'svelte';
	import EnrolVoice from '$lib/components/EnrolVoice.svelte';
	import SettingPlain from '$lib/components/SettingPlain.svelte';
	import SettingRaw from '$lib/components/SettingRaw.svelte';
	import SettingsFold from '$lib/components/SettingsFold.svelte';
	import { SectionLink } from '$lib/sectionLink.svelte';
	import { SettingsStore } from '$lib/settingsStore.svelte';
	import type { SpeakerPerson, SpeakerStatus } from '$lib/enrolment';
	import {
		Button,
		EmptyState,
		ErrorState,
		OfflineState,
		Panel,
		Pill,
		ScreenState,
		SkeletonRows, SettingRow } from '$lib/ui';
	import { featuredOf, sectionOfGroup } from './settingsPlan';

	const store = new SettingsStore();
	const link = new SectionLink(async (conn) => {
		await store.load(conn.client);
	});

	/**
	 * Whose voice Jarvis answers, as the server reports it.
	 *
	 * The payload never contains the voiceprint — counts, scores and timestamps
	 * only — so "is somebody enrolled" cannot also answer "what do they sound
	 * like". That is enforced on the server, not here. Enrolled here or from
	 * the phone: both read one list of phrases from the server, so the two
	 * surfaces cannot drift. See `docs/voice-identity.md`.
	 *
	 * Since M71 it lists PEOPLE: one row each, with a way to forget one. The
	 * top level still describes the first person under the keys the panel has
	 * always read, so nothing written for "the owner" changed meaning.
	 */
	let speaker = $state<SpeakerStatus | null>(null);
	/** The same payload, never null: what the panel reads. A snippet does not carry the `{#if}`'s narrowing. */
	const who = $derived<SpeakerStatus>(speaker ?? {});
	const people = $derived<SpeakerPerson[]>(who.people ?? []);
	const totalSamples = $derived(people.reduce((sum, p) => sum + (p.samples ?? 0), 0));
	/**
	 * The panel's own four states. Its data is one HTTP read, independent of
	 * the section's socket, so the section's `<ScreenState>` cannot speak for
	 * it: a panel that vanished while loading, or on a 500, read as "this
	 * Jarvis has no voice identity" — the one thing it must never say by
	 * accident, because the remedy for that is "update jarvis-core".
	 */
	let speakerState = $state<'loading' | 'ready' | 'error' | 'offline'>('loading');
	let speakerDetail = $state('');
	let speakerBusy = $state(false);
	let speakerError = $state('');

	async function loadSpeaker(): Promise<void> {
		if (speaker === null) speakerState = 'loading';
		const res = await fetch('/api/voice/speaker').catch(() => null);
		if (!res) {
			// No answer at all — the console itself is unreachable. What is on
			// screen is the last answer, and says so.
			speakerState = 'offline';
			return;
		}
		if (!res.ok) {
			speakerDetail = await failureText(res);
			speakerState = 'error';
			return;
		}
		const payload = await res.json().catch(() => null);
		if (!payload || typeof payload !== 'object') {
			speakerDetail = 'the console sent an unreadable answer';
			speakerState = 'error';
			return;
		}
		speaker = payload;
		speakerState = 'ready';
	}

	async function failureText(res: Response): Promise<string> {
		const body = await res.json().catch(() => null);
		return (body && (body.message || body.detail)) || `the server answered ${res.status}`;
	}

	/** DELETE one person, or everyone when `label` is absent. */
	async function forget(label?: string): Promise<void> {
		if (speakerBusy) return;
		speakerBusy = true;
		speakerError = '';
		try {
			const url = label ? `/api/voice/speaker?label=${encodeURIComponent(label)}` : '/api/voice/speaker';
			const res = await fetch(url, { method: 'DELETE' });
			if (!res.ok) {
				// 401 is the console lock, and it is the likely one — say what to
				// do rather than showing a status code.
				speakerError =
					res.status === 401
						? 'Unlock the console with its password first (SETTINGS › Console, pairing).'
						: await failureText(res);
				return;
			}
			await loadSpeaker();
		} finally {
			speakerBusy = false;
		}
	}

	const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '-');

	onMount(() => {
		void loadSpeaker();
		return link.mount();
	});

	const featured = featuredOf('voice');
	const raw = $derived(store.rows.filter((row) => sectionOfGroup(row.group) === 'voice'));
	let everything = $state(false);
</script>

<div class="stack">
	<p class="lede" data-testid="settings-voice-lede">
		{#if speakerState === 'loading'}
			reading whose voice
		{:else if who.supported === false}
			voice identity not supported
		{:else if people.length === 1}
			one voice enrolled
		{:else if people.length > 1}
			{people.length} voices enrolled
		{:else}
			answers any voice
		{/if}
		· link {link.status}
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

	{#if store.restartNeeded.length}
		<p class="line" data-testid="restart-needed">
			<Pill tone="warn">needs a restart</Pill>
			<span>Saved, but these need a restart of jarvis-core to take effect: {store.restartNeeded.join(', ')}.</span>
		</p>
	{/if}

	{#if store.supported}
		{#if !store.loaded && link.status !== 'closed' && link.status !== 'error'}
			<Panel title="Voice" meta="…">
				{#snippet children()}<SkeletonRows rows={3} label="Loading settings" />{/snippet}
			</Panel>
		{:else}
			<Panel title="Voice" meta={`${featured.length}`} testid="group-voice">
				{#snippet children()}
					{#each featured as item (item.key)}
						{@const row = store.row(item.key)}
						{#if row}
							<SettingPlain {store} {row} label={item.label} why={item.why} />
						{/if}
					{/each}
				{/snippet}
			</Panel>
		{/if}
	{/if}

	<!--
	  Whose voice Jarvis answers.

	  The numbers are on screen for the same reason the phone shows them: a
	  biometric gate whose threshold was guessed is a gate that locks the owner
	  out, and the only defence is being able to see the owner's own scores
	  before enforcing.
	-->
	<Panel
		title="Whose voice"
		meta={speakerState === 'ready' && who.supported !== false
			? people.length
				? `${people.length} enrolled`
				: 'nobody enrolled'
			: '…'}
		testid="voice-identity"
	>
		{#snippet children()}
			{#if speakerState === 'loading'}
				<div data-testid="speaker-loading">
					<SkeletonRows rows={3} label="Loading whose voice Jarvis answers" />
				</div>
			{:else if speakerState === 'error'}
				<ErrorState
					title="Couldn't read whose voice Jarvis answers"
					detail={`${speakerDetail}. Retry, or check docker compose logs jarvis-core.`}
					onretry={loadSpeaker}
					testid="speaker-error-state"
				/>
			{:else if speakerState === 'offline'}
				<OfflineState
					body={speaker
						? 'Jarvis could not be reached, so nothing here is live; what is shown is the last answer.'
						: 'Jarvis could not be reached, so nothing is known about whose voice it answers.'}
					onreconnect={loadSpeaker}
					busy={speakerBusy}
					testid="speaker-offline"
				/>
			{/if}
			{#if speakerState !== 'loading' && who.supported === false}
				<EmptyState
					title="This Jarvis has no voice identity"
					body="The console reached the server and it has no /api/voice/speaker. Update jarvis-core; nothing on this page can add it."
					testid="speaker-unsupported"
				/>
			{:else if speakerState === 'ready' || (speakerState === 'offline' && speaker)}
				<SettingRow label="Mode" why="whether other voices are refused">
					<span class="value">
						<Pill tone={who.active ? 'live' : 'neutral'} testid="speaker-mode">{who.mode ?? 'off'}</Pill>
					</span>
				</SettingRow>
				<SettingRow label="Enrolled" why="whose samples the gate compares a voice with">
					<span class="value" data-testid="speaker-samples">
						{#if people.length === 1}
							{people[0].samples} of {people[0].max_samples ?? who.max_samples} samples
						{:else if people.length > 1}
							{people.length} people · {totalSamples} samples
						{:else}
							nobody — the gate is inert until somebody enrols
						{/if}
					</span>
				</SettingRow>

				<!--
				  One row per person. `data-jv-row` so the menu inventory measures
				  its one control at rest (REMOVE) as a row's, not the page's; the
				  numbers are each person's own, because a threshold is per voice.
				-->
				{#if people.length}
					<ol class="people" data-testid="speaker-people">
						{#each people as person (person.label)}
							<li class="person" data-jv-row data-testid="person-{slug(person.label)}">
								<div class="what">
									<b>{person.label}</b>
									<span class="dim" data-testid="person-samples-{slug(person.label)}">
										{person.samples} of {person.max_samples ?? who.max_samples} samples
										{#if person.threshold != null}
											· threshold {person.threshold}
										{/if}
										{#if person.threshold_measured !== false && person.worst_self_score != null}
											· their worst sample scores {person.worst_self_score}
										{:else}
											· not measurable yet
										{/if}
										{#if !person.enrolled}
											· needs {Math.max(0, (who.min_samples ?? 3) - (person.samples ?? 0))} more
										{/if}
									</span>
								</div>
								<div class="acts">
									<Button
										variant="danger"
										testid="person-remove-{slug(person.label)}"
										disabled={speakerBusy}
										title={speakerBusy ? 'Deleting' : `Delete ${person.label}'s voiceprint; everyone else stays`}
										onclick={() => forget(person.label)}
									>
										REMOVE
									</Button>
								</div>
							</li>
						{/each}
					</ol>
				{/if}

				{#if who.enrolled}
					<SettingRow label="Threshold" why="mean squared z · lower is stricter">
						<span class="value" data-testid="speaker-threshold">
							{who.threshold}
							{#if who.configured_threshold != null}
								<!-- `voice: speaker: threshold:` wins over every profile's own
								     measurement; the screen has to say which number is live. -->
								<span class="dim" data-testid="speaker-threshold-configured">
									· set in configuration.yaml; enrolment suggests {who.suggested_threshold}
								</span>
							{:else if who.threshold_measured !== false && who.worst_self_score != null}
								<span class="dim">
									· their own worst sample scores {who.worst_self_score}, enrolment
									suggests {who.suggested_threshold}
								</span>
							{:else}
								<!-- Scoring one enrolment sample means holding it out and rebuilding
								     the profile from the rest, and that rebuilt profile needs the
								     minimum too. So at exactly the minimum there is nothing to
								     measure with, and the row says so rather than printing Infinity. -->
								<span class="dim">
									· not measurable yet: scoring one sample needs {who.min_samples} others,
									so this needs {who.measure_samples ?? (who.min_samples ?? 3) + 1} in all.
									{who.suggested_threshold} is the default, not a measurement.
								</span>
							{/if}
						</span>
					</SettingRow>
				{/if}

				<EnrolVoice status={speaker} onDone={loadSpeaker} />

				<SettingRow
					label={people.length > 1 ? 'Forget everyone' : 'Forget this voice'}
					why={people.length > 1 ? 'deletes every voiceprint' : 'deletes the voiceprint'}
				>
					{#snippet acts()}
						<Button
							variant="danger"
							testid="speaker-forget"
							disabled={!who.enrolled || speakerBusy}
							title={!who.enrolled
								? 'Nobody is enrolled'
								: speakerBusy
									? 'Deleting'
									: 'Delete every voiceprint — Jarvis answers any voice again'}
							onclick={() => forget()}
						>
							{speakerBusy ? 'deleting…' : 'FORGET'}
						</Button>
					{/snippet}
				</SettingRow>
				{#if speakerError}
					<p class="note bad" data-testid="speaker-error" role="alert">{speakerError}</p>
				{/if}

				<p class="note">
					Enrol here or from the phone; both read the same phrases from the server, and samples add
					up rather than replacing each other. A new name is a new person — up to
					{who.max_people ?? 8} — and a turn is compared with everyone, so Jarvis knows who is
					speaking and the agent is told. Whether Jarvis <i>refuses</i> other voices is
					<code>voice: speaker: mode</code> in <code>configuration.yaml</code>, and the honest order is
					enrol, leave it in <code>observe</code> for a few days, read the scores, then
					<code>enforce</code>. It stops a guest, a television and a stranger at the window; it does
					not stop a recording, and it is not a second factor — the tier system still asks a human
					before anything irreversible.
				</p>
			{/if}
		{/snippet}
	</Panel>

	{#if store.supported && store.loaded}
		<SettingsFold
			title="Everything"
			meta={`${raw.length} setting${raw.length === 1 ? '' : 's'} · as the server lists them`}
			bind:open={everything}
			testid="everything"
			summaryTestid="everything-summary"
		>
			{#snippet children()}
				{#each raw as row (row.key)}
					<SettingRaw {store} {row} />
				{/each}
			{/snippet}
		</SettingsFold>
	{/if}
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
	.line {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		flex-wrap: wrap;
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
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
	.acts {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-2);
		flex-wrap: wrap;
	}
	/* One hairline row per person, washed like the phrase being read. */
	.people {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.person {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-2) var(--jv-space-2);
		border-bottom: 1px solid var(--jv-line-hair);
		background: var(--jv-wash);
	}
	.note {
		grid-column: 1 / -1;
		margin: 0;
		font-size: var(--jv-fs-xs);
		line-height: 1.6;
		color: var(--jv-text-dim);
		max-width: 80ch;
	}
	.note.bad {
		color: var(--jv-danger-text);
	}
	code {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text);
	}
	@media (max-width: 720px) {
		.acts {
			justify-content: flex-start;
		}
	}
</style>
