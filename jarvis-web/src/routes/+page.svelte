<script lang="ts">
	import { onMount } from 'svelte';
	import { PipelineClient, type PipelineState } from '$lib/pipeline';
	import { MicCapture } from '$lib/audio/capture';
	import { Player } from '$lib/audio/playback';
	import { EnergyVAD } from '$lib/wake';
	import Orb from '$lib/components/Orb.svelte';

	let state = $state<PipelineState>('idle');
	let transcript = $state('');
	let response = $state('');
	let statusMsg = $state('booting');
	let errorMsg = $state('');
	let capturing = $state(false);
	let handsFree = $state(false);
	let orbLevel = $state(0);
	let latText = $state('');

	let ws: WebSocket | null = null;
	let client: PipelineClient | null = null;
	let mic: MicCapture | null = null;
	let micReady = false;
	const player = new Player();
	const vad = new EnergyVAD();
	const bargeVad = new EnergyVAD({ startThreshold: 0.06, minSpeechMs: 150 });

	let pipelineName = 'Jarvis';
	let pipelineId: string | null = null;
	let pendingEnd = false;
	let e2eMode = false;
	let micLevel = 0;

	// Latency instrumentation (P9): ms from end-of-audio to key events.
	let tAudioEnd = 0;
	let lat: { stt?: number; firstDelta?: number; tts?: number } = {};

	function fmtLat(): string {
		const parts: string[] = [];
		if (lat.stt != null) parts.push(`stt ${lat.stt.toFixed(0)}ms`);
		if (lat.firstDelta != null) parts.push(`first-delta ${lat.firstDelta.toFixed(0)}ms`);
		if (lat.tts != null) parts.push(`tts-start ${lat.tts.toFixed(0)}ms`);
		return parts.join(' · ');
	}

	function connectWs(): Promise<void> {
		if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve();
		return new Promise((resolve, reject) => {
			const proto = location.protocol === 'https:' ? 'wss' : 'ws';
			ws = new WebSocket(`${proto}://${location.host}/ws`);
			ws.binaryType = 'arraybuffer';
			client = new PipelineClient(
				(data) => {
					if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
				},
				{
					onState: (s) => {
						state = s;
						statusMsg = s;
					},
					onReady: () => {
						if (pendingEnd) {
							pendingEnd = false;
							markAudioEnd();
						}
					},
					onTranscript: (text) => {
						transcript = text;
						lat.stt = performance.now() - tAudioEnd;
						latText = fmtLat();
					},
					onDelta: (d) => {
						if (lat.firstDelta == null) {
							lat.firstDelta = performance.now() - tAudioEnd;
							latText = fmtLat();
						}
						response += d;
					},
					onResponse: (text) => {
						if (text) response = text;
					},
					onEvent: (ev) => {
						if (ev.type === 'tts-start') {
							lat.tts = performance.now() - tAudioEnd;
							latText = fmtLat();
						}
					},
					onTtsUrl: async (url) => {
						console.log('[jarvis] latencies', { ...lat });
						try {
							await player.play(`/api/tts?path=${encodeURIComponent(url)}`);
						} catch (e) {
							console.warn('tts playback failed', e);
						}
						if (state === 'speaking') {
							state = 'idle';
							statusMsg = 'idle';
						}
					},
					onRunEnd: () => {
						if (state !== 'speaking') {
							state = 'idle';
							statusMsg = 'idle';
						}
					},
					onError: (code, message) => {
						errorMsg = `${code}: ${message}`;
						capturing = false;
						statusMsg = 'error';
					}
				}
			);
			ws.onopen = () => resolve();
			ws.onerror = () => reject(new Error('websocket error'));
			ws.onmessage = (e) => {
				if (typeof e.data === 'string') client?.handleMessage(e.data);
			};
			ws.onclose = () => {
				statusMsg = 'disconnected';
				ws = null;
			};
		});
	}

	async function ensureMic(): Promise<void> {
		if (micReady) return;
		mic = new MicCapture({
			onChunk: (pcm) => {
				if (capturing) client?.sendAudio(pcm);
			},
			onLevel: (r) => {
				micLevel = r;
				if (handsFree && !capturing && state === 'idle') {
					if (vad.feed(r) === 'speech-start') void startInteraction();
				} else if (handsFree && capturing) {
					if (vad.feed(r) === 'speech-end') stopCapture();
				}
				// Barge-in: user speaks over TTS -> kill playback, new run.
				if (state === 'speaking' && bargeVad.feed(r) === 'speech-start') {
					console.log('[jarvis] barge-in');
					player.stopAll();
					void startInteraction();
				}
			}
		});
		await mic.start();
		micReady = true;
	}

	async function startInteraction(): Promise<void> {
		if (capturing) return;
		errorMsg = '';
		transcript = '';
		response = '';
		lat = {};
		latText = '';
		pendingEnd = false;
		try {
			await connectWs();
		} catch {
			errorMsg = 'cannot reach server websocket';
			return;
		}
		try {
			await ensureMic();
		} catch (e) {
			// Keep going: the run can still complete with an empty audio stream
			// (useful under test); real use needs mic permission.
			console.warn('mic unavailable', e);
		}
		if (!pipelineId && client) {
			try {
				pipelineId = await client.resolvePipelineId(pipelineName);
			} catch (e) {
				console.warn('pipeline list failed, using preferred', e);
			}
		}
		capturing = true;
		statusMsg = 'listening';
		client?.startRun({ pipeline: pipelineId });
		if (e2eMode) setTimeout(() => stopCapture(), 1500);
	}

	function markAudioEnd(): void {
		tAudioEnd = performance.now();
		client?.endAudio();
	}

	function stopCapture(): void {
		if (!capturing) return;
		capturing = false;
		vad.reset();
		if (client?.sttBinaryHandlerId != null) markAudioEnd();
		else pendingEnd = true; // run-start not seen yet; end as soon as it is
		statusMsg = 'processing';
	}

	function togglePtt(): void {
		if (capturing) stopCapture();
		else void startInteraction();
	}

	function onKeyDown(e: KeyboardEvent): void {
		if (e.code === 'Space' && !e.repeat && !capturing) {
			e.preventDefault();
			void startInteraction();
		}
	}
	function onKeyUp(e: KeyboardEvent): void {
		if (e.code === 'Space' && capturing) {
			e.preventDefault();
			stopCapture();
		}
	}

	onMount(() => {
		e2eMode = new URLSearchParams(location.search).has('e2e');
		fetch('/api/config')
			.then((r) => r.json())
			.then((c) => (pipelineName = c.pipeline ?? 'Jarvis'))
			.catch(() => {});
		connectWs()
			.then(() => (statusMsg = 'idle'))
			.catch(() => (statusMsg = 'disconnected'));

		let raf = 0;
		const tick = () => {
			orbLevel = state === 'speaking' ? player.level() * 2 : Math.min(micLevel * 4, 1);
			raf = requestAnimationFrame(tick);
		};
		raf = requestAnimationFrame(tick);
		return () => cancelAnimationFrame(raf);
	});
</script>

<svelte:window onkeydown={onKeyDown} onkeyup={onKeyUp} />

<main class="hud">
	<div class="orb-wrap">
		<Orb level={orbLevel} orbState={state} />
	</div>

	<section class="readout">
		<p class="transcript" data-testid="transcript">{transcript}</p>
		<p class="response" data-testid="response">{response}</p>
		{#if errorMsg}
			<p class="error" data-testid="error">{errorMsg}</p>
		{/if}
	</section>

	<footer class="controls">
		<button
			class="ptt"
			class:active={capturing}
			data-testid="ptt"
			onclick={togglePtt}
			aria-pressed={capturing}
		>
			{capturing ? 'Release to send' : 'Push to talk'}
		</button>
		<label class="handsfree">
			<input type="checkbox" bind:checked={handsFree} data-testid="handsfree" />
			Hands-free (VAD)
		</label>
		<p class="status" data-testid="status">
			<span class="dot {state}"></span>
			{statusMsg}{latText ? ` — ${latText}` : ''}
		</p>
	</footer>
</main>

<style>
	:global(html, body) {
		margin: 0;
		height: 100%;
		background: #05080d;
	}
	.hud {
		height: 100vh;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 1.4rem;
		background:
			radial-gradient(ellipse 80% 55% at 50% 42%, rgba(14, 60, 78, 0.35), transparent 70%),
			#05080d;
		color: #bfeaf5;
		font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
		overflow: hidden;
	}
	.orb-wrap {
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.readout {
		min-height: 7rem;
		max-width: min(80vw, 46rem);
		text-align: center;
	}
	.transcript {
		color: #7fd7ea;
		font-size: 1.05rem;
		min-height: 1.4rem;
		margin: 0 0 0.6rem;
		opacity: 0.85;
	}
	.transcript:not(:empty)::before {
		content: '» ';
		opacity: 0.5;
	}
	.response {
		color: #e8f6fb;
		font-size: 1.3rem;
		line-height: 1.5;
		min-height: 2rem;
		margin: 0;
		text-shadow: 0 0 14px rgba(80, 200, 235, 0.35);
	}
	.error {
		color: #ff7b6b;
		font-size: 0.9rem;
	}
	.controls {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.6rem;
	}
	.ptt {
		background: rgba(16, 46, 60, 0.7);
		border: 1px solid #1e7d99;
		color: #aee9f7;
		padding: 0.7rem 2.2rem;
		border-radius: 999px;
		font-size: 1rem;
		letter-spacing: 0.06em;
		cursor: pointer;
		transition:
			background 0.15s,
			box-shadow 0.15s;
	}
	.ptt:hover {
		background: rgba(24, 68, 88, 0.85);
	}
	.ptt.active {
		background: #0e5a74;
		box-shadow: 0 0 24px rgba(30, 200, 255, 0.5);
	}
	.handsfree {
		font-size: 0.8rem;
		opacity: 0.7;
		display: flex;
		gap: 0.4rem;
		align-items: center;
		cursor: pointer;
	}
	.status {
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		opacity: 0.6;
		margin: 0;
		display: flex;
		align-items: center;
		gap: 0.45rem;
	}
	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		background: #3d6b78;
		display: inline-block;
	}
	.dot.listening {
		background: #17d3ff;
		box-shadow: 0 0 8px #17d3ff;
	}
	.dot.thinking {
		background: #ffa626;
		box-shadow: 0 0 8px #ffa626;
	}
	.dot.speaking {
		background: #ffd25e;
		box-shadow: 0 0 8px #ffd25e;
	}
</style>
