<script lang="ts">
	/**
	 * Enrolling a voice from the browser.
	 *
	 * The console could always SEE whose voice Jarvis answers and could delete
	 * it; the one thing it could not do was create it, and the reason was
	 * credentials rather than capability — the enrol relay demanded the phone's
	 * own Jarvis token and a browser has none. It now also accepts the console
	 * password, which is the same door already in front of the pairing secret
	 * and in front of deleting this very profile.
	 *
	 * Everything about "what happens next" lives in `$lib/enrolment.ts` and is
	 * tested in Node. This file owns the microphone, the worklet and the
	 * buttons — the parts that need a browser and cannot be unit-tested — and
	 * nothing else. When a state question comes up here, it belongs there.
	 */
	import { Button } from '$lib/ui';
	import { MicCapture } from '$lib/audio/capture';
	import {
		ENROLMENT_RATE,
		ENROLMENT_WIDTH,
		MAX_SAMPLE_MS,
		beginSession,
		durationMs,
		joinChunks,
		pcmBytes,
		progress,
		rejectLocally,
		remaining,
		startRecording,
		startSending,
		withAccepted,
		withRejected,
		type EnrolmentSession,
		type SpeakerStatus
	} from '$lib/enrolment';

	let {
		status,
		onDone
	}: {
		status: SpeakerStatus | null;
		/** Called after every accepted sample, so the parent can refresh counts. */
		onDone?: () => void;
	} = $props();

	let session = $state<EnrolmentSession | null>(null);
	let error = $state('');
	let level = $state(0);
	let recording = $state(false);

	let mic: MicCapture | null = null;
	let chunks: Int16Array[] = [];
	let stopTimer: ReturnType<typeof setTimeout> | null = null;

	const pct = $derived(session ? Math.round(progress(session) * 100) : 0);
	const left = $derived(session ? remaining(session) : 0);

	function begin(): void {
		error = '';
		session = beginSession(status);
		if (!session.slots.length) {
			error = 'the server sent no enrolment phrases, so there is nothing to read';
		}
	}

	function cancel(): void {
		void stopMic();
		session = null;
		error = '';
	}

	async function stopMic(): Promise<void> {
		if (stopTimer) {
			clearTimeout(stopTimer);
			stopTimer = null;
		}
		recording = false;
		level = 0;
		const running = mic;
		mic = null;
		await running?.stop();
	}

	async function record(index: number): Promise<void> {
		if (!session || recording) return;
		error = '';
		chunks = [];
		session = startRecording(session, index);
		try {
			mic = new MicCapture({
				onChunk: (pcm) => chunks.push(pcm),
				onLevel: (rms) => (level = rms)
			});
			await mic.start();
			recording = true;
		} catch (err) {
			// A refused microphone is the single most likely failure here and the
			// browser's own message ("Permission denied") does not say what to do
			// about it, so say it.
			await stopMic();
			session = withRejected(
				session,
				index,
				'the browser refused the microphone — allow it for this site and try again'
			);
			return;
		}
		// A recorder that never stops is a tab that holds the microphone open
		// for the rest of the session.
		stopTimer = setTimeout(() => void finish(index), MAX_SAMPLE_MS);
	}

	async function finish(index: number): Promise<void> {
		if (!session || !recording) return;
		await stopMic();
		const samples = joinChunks(chunks);
		chunks = [];

		const local = rejectLocally(samples);
		if (local) {
			session = withRejected(session, index, local);
			return;
		}

		const ms = durationMs(samples.length);
		session = startSending(session, index, ms);
		try {
			const body = pcmBytes(samples);
			const res = await fetch(
				`/api/voice/speaker/enrol?rate=${ENROLMENT_RATE}&width=${ENROLMENT_WIDTH}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/octet-stream' },
					body: body as BodyInit
				}
			);
			if (!res.ok) {
				// jarvis-core writes its refusals for a person to act on — "that
				// sample has no measurable pitch, it is too quiet". Show its words,
				// not ours.
				const payload = await res.json().catch(() => null);
				const detail =
					payload?.detail ||
					payload?.message ||
					(res.status === 401
						? 'unlock the console with its password first'
						: `the server answered ${res.status}`);
				session = withRejected(session, index, String(detail));
				return;
			}
			session = withAccepted(session, index);
			onDone?.();
		} catch {
			session = withRejected(session, index, 'could not reach Jarvis');
		}
	}
</script>

<div class="enrol" data-testid="enrol-voice">
	{#if !session}
		<div class="r">
			<div class="what">
				<b>{status?.enrolled ? 'Enrol again' : 'Enrol a voice'}</b>
				<span class="dim">
					{#if status?.enrolled}
						adds samples to the existing profile — it widens it, it does not replace it
					{:else}
						read {status?.min_samples ?? 3} phrases aloud
					{/if}
				</span>
			</div>
			<div class="acts">
				<Button testid="enrol-start" title="Read the server's phrases into this browser's microphone" onclick={begin}>ENROL</Button>
			</div>
		</div>
		{#if error}<p class="bad" role="alert">{error}</p>{/if}
	{:else}
		<div class="r head">
			<div class="what">
				<b>Enrolling</b>
				<span class="dim" data-testid="enrol-remaining">
					{#if left > 0}
						{left} more {left === 1 ? 'phrase' : 'phrases'} needed
					{:else}
						enough samples — more only makes it better
					{/if}
				</span>
			</div>
			<div class="acts">
				<Button testid="enrol-cancel" title="Stop enrolling; what was accepted is kept" onclick={cancel}>DONE</Button>
			</div>
		</div>

		<!--
			A real progress bar, against the SERVER's minimum. `aria-valuenow` so
			it is not a decorative div to a screen reader, and the same number in
			text beside it so it is not colour-only either.
		-->
		<div
			class="bar"
			role="progressbar"
			aria-valuemin="0"
			aria-valuemax="100"
			aria-valuenow={pct}
			aria-label="Enrolment progress"
			data-testid="enrol-progress"
			data-percent={pct}
		>
			<span class="fill" style="width: {pct}%"></span>
		</div>

		<ol class="prompts">
			{#each session.slots as slot, i (slot.prompt + i)}
				<li class="prompt" class:current={i === session.at} data-state={slot.state}>
					<span class="phrase">
						<span class="mark" aria-hidden="true">
							{#if slot.state === 'accepted'}[ok]{:else if slot.state === 'rejected'}[--]{:else}[&nbsp;&nbsp;]{/if}
						</span>
						<span>{slot.prompt}</span>
					</span>
					<span class="act">
						{#if slot.state === 'sending'}
							<span class="dim">checking…</span>
						{:else if recording && i === session.at}
							<span class="meter" aria-hidden="true">
								<span class="meter-fill" style="width: {Math.min(100, level * 320)}%"></span>
							</span>
							<Button variant="danger" testid="enrol-stop-{i}" title="Stop recording this phrase" onclick={() => finish(i)}>STOP</Button>
						{:else}
							<!-- The phrase to read next is the one lit control while a
							     session is open; the others wait their turn. -->
							<Button
								variant={i === session.at ? 'primary' : 'ghost'}
								testid="enrol-record-{i}"
								disabled={recording}
								title={recording ? 'Another phrase is being recorded' : 'Record this phrase'}
								onclick={() => record(i)}
							>
								{slot.state === 'accepted' ? 'AGAIN' : slot.state === 'rejected' ? 'RETRY' : 'RECORD'}
							</Button>
						{/if}
					</span>
					{#if slot.detail}
						<span class="detail" data-testid="enrol-detail-{i}">{slot.detail}</span>
					{/if}
				</li>
			{/each}
		</ol>
		{#if error}<p class="bad" role="alert">{error}</p>{/if}
	{/if}
</div>

<style>
	.enrol {
		display: block;
	}
	.r {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		padding: var(--jv-space-3) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.r.head {
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
	.acts {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--jv-space-2);
	}
	.bad {
		margin: 0;
		padding: var(--jv-space-2) 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-danger-text);
	}
	.bar {
		height: var(--jv-space-1);
		border-radius: var(--jv-rule-live);
		background: var(--jv-line-hair);
		overflow: hidden;
		margin: 0 0 var(--jv-space-3);
	}
	.fill {
		display: block;
		height: 100%;
		background: var(--jv-accent);
		transition: width var(--jv-dur-fast) var(--jv-ease-out);
	}
	.prompts {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.prompt {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-2);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.prompt:last-child {
		border-bottom: 0;
	}
	.phrase {
		display: flex;
		gap: var(--jv-space-2);
		min-width: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text);
	}
	.mark {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
		flex: 0 0 auto;
	}
	.prompt[data-state='accepted'] .mark {
		color: var(--jv-ok);
	}
	.prompt[data-state='rejected'] .mark {
		color: var(--jv-danger-text);
	}
	/* The phrase being read now: washed, with the live rule. */
	.prompt.current {
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.act {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.meter {
		display: inline-block;
		width: var(--jv-space-7);
		height: var(--jv-space-1);
		border-radius: var(--jv-rule-live);
		background: var(--jv-line-hair);
		overflow: hidden;
	}
	.meter-fill {
		display: block;
		height: 100%;
		background: var(--jv-accent);
	}
	.detail {
		grid-column: 1 / -1;
		color: var(--jv-danger-text);
		font-size: var(--jv-fs-xs);
	}
	@media (prefers-reduced-motion: reduce) {
		.fill {
			transition: none;
		}
	}
</style>
