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
	import { Button, Panel, Pill, ScreenState, SkeletonRows } from '$lib/ui';
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

	async function failureText(res: Response): Promise<string> {
		const body = await res.json().catch(() => null);
		return (body && (body.message || body.detail)) || `the server answered ${res.status}`;
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
						? 'Unlock the console with its password first (SETTINGS › Console, pairing).'
						: await failureText(res);
				return;
			}
			await loadSpeaker();
		} finally {
			speakerBusy = false;
		}
	}

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
		{who.supported ? (who.enrolled ? 'one voice enrolled' : 'answers any voice') : 'voice identity not supported'} · link {link.status}
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
	{#if who.supported}
		<Panel title="Whose voice" meta={who.enrolled ? 'enrolled' : 'nobody enrolled'} testid="voice-identity">
			{#snippet children()}
				<div class="setting">
					<div class="what"><b>Mode</b><span class="dim">whether other voices are refused</span></div>
					<span class="value">
						<Pill tone={who.active ? 'live' : 'neutral'} testid="speaker-mode">{who.mode ?? 'off'}</Pill>
					</span>
				</div>
				<div class="setting">
					<div class="what"><b>Enrolled</b><span class="dim">samples of the owner's voice</span></div>
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
								<!-- Scoring one enrolment sample means holding it out and rebuilding
								     the profile from the rest, and that rebuilt profile needs the
								     minimum too. So at exactly the minimum there is nothing to
								     measure with, and the row says so rather than printing Infinity. -->
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
					Enrol here or from the phone; both read the same phrases from the server, and samples add
					up rather than replacing each other. Whether Jarvis <i>refuses</i> other voices is
					<code>voice: speaker: mode</code> in <code>configuration.yaml</code>, and the honest order is
					enrol, leave it in <code>observe</code> for a few days, read the scores, then
					<code>enforce</code>. It stops a guest, a television and a stranger at the window; it does
					not stop a recording, and it is not a second factor — the tier system still asks a human
					before anything irreversible.
				</p>
			{/snippet}
		</Panel>
	{/if}

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
		.setting {
			grid-template-columns: minmax(0, 1fr);
		}
		.acts {
			justify-content: flex-start;
		}
	}
</style>
