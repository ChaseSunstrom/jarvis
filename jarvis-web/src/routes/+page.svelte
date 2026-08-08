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

	// --- presentation: accent colour + labels that track pipeline state ---
	const ACCENT: Record<string, string> = {
		idle: '#2bb0d8',
		listening: '#3fd8ff',
		thinking: '#ff9e2c',
		speaking: '#ffcf5c',
		error: '#ff6b5c'
	};
	const LABEL: Record<string, string> = {
		idle: 'STANDBY',
		listening: 'LISTENING',
		thinking: 'PROCESSING',
		speaking: 'RESPONDING'
	};
	let accent = $derived(errorMsg ? ACCENT.error : (ACCENT[state] ?? ACCENT.idle));
	let stateLabel = $derived(
		statusMsg === 'disconnected' ? 'OFFLINE' : (LABEL[state] ?? state.toUpperCase())
	);
	let online = $derived(statusMsg !== 'disconnected' && statusMsg !== 'booting');
	let clock = $state('--:--:--');

	function tickClock(): void {
		const d = new Date();
		const p = (n: number) => n.toString().padStart(2, '0');
		clock = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
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

		tickClock();
		const clk = setInterval(tickClock, 1000);

		let raf = 0;
		const tick = () => {
			orbLevel = state === 'speaking' ? player.level() * 2 : Math.min(micLevel * 4, 1);
			raf = requestAnimationFrame(tick);
		};
		raf = requestAnimationFrame(tick);
		return () => {
			cancelAnimationFrame(raf);
			clearInterval(clk);
		};
	});
</script>

<svelte:window onkeydown={onKeyDown} onkeyup={onKeyUp} />

<main class="hud" style="--accent: {accent}" data-state={state}>
	<div class="grid" aria-hidden="true"></div>
	<span class="bracket tl" aria-hidden="true"></span>
	<span class="bracket tr" aria-hidden="true"></span>
	<span class="bracket bl" aria-hidden="true"></span>
	<span class="bracket br" aria-hidden="true"></span>

	<header class="topbar">
		<div class="brand">
			<span class="logo">JARVIS</span>
			<span class="tag">Just A Rather Very Intelligent System</span>
		</div>
		<div class="sysinfo">
			<span class="status" data-testid="status">
				<span class="dot {state}" class:off={!online}></span>
				{stateLabel}
			</span>
			<span class="clock" aria-label="time">{clock}</span>
		</div>
	</header>

	<section class="stage">
		<div class="orb-frame">
			<div class="orb-wrap">
				<Orb level={orbLevel} orbState={state} />
			</div>
		</div>
	</section>

	<section class="readout">
		<p class="transcript" data-testid="transcript">{transcript}</p>
		<p class="response" data-testid="response">
			{response}{#if state === 'thinking' || state === 'listening'}<span class="caret"></span>{/if}
		</p>
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
			<span class="ptt-ring" aria-hidden="true"></span>
			{capturing ? 'RELEASE TO SEND' : 'PUSH TO TALK'}
		</button>
		<div class="meta">
			<label class="handsfree">
				<input type="checkbox" bind:checked={handsFree} data-testid="handsfree" />
				<span>Hands-free</span>
			</label>
			<span class="hint">HOLD&nbsp;SPACE</span>
			{#if latText}<span class="latency" data-testid="latency" title="latency">{latText}</span>{/if}
		</div>
	</footer>
</main>

<style>
	:global(html, body) {
		margin: 0;
		height: 100%;
		background: #04070c;
	}
	:global(*) {
		box-sizing: border-box;
	}

	.hud {
		--chrome: 'SFMono-Regular', ui-monospace, 'Cascadia Code', 'Cascadia Mono', Menlo,
			Consolas, monospace;
		--body: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
		--dim: color-mix(in srgb, var(--accent) 60%, #8fb3c0);
		--line: color-mix(in srgb, var(--accent) 32%, transparent);
		--line-soft: color-mix(in srgb, var(--accent) 14%, transparent);

		position: relative;
		height: 100vh;
		height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr auto auto;
		padding: clamp(0.9rem, 2.5vw, 2rem);
		gap: clamp(0.5rem, 2vh, 1.5rem);
		color: #d7edf5;
		font-family: var(--body);
		overflow: hidden;
		background:
			radial-gradient(
				ellipse 70% 55% at 50% 44%,
				color-mix(in srgb, var(--accent) 16%, transparent),
				transparent 70%
			),
			#04070c;
		transition: background 0.6s ease;
	}

	/* faint technical grid */
	.grid {
		position: absolute;
		inset: 0;
		pointer-events: none;
		opacity: 0.5;
		background-image:
			linear-gradient(var(--line-soft) 1px, transparent 1px),
			linear-gradient(90deg, var(--line-soft) 1px, transparent 1px);
		background-size: 46px 46px;
		mask-image: radial-gradient(ellipse 75% 75% at 50% 50%, #000 40%, transparent 88%);
		-webkit-mask-image: radial-gradient(ellipse 75% 75% at 50% 50%, #000 40%, transparent 88%);
	}

	/* corner framing brackets */
	.bracket {
		position: absolute;
		width: clamp(22px, 4vw, 46px);
		height: clamp(22px, 4vw, 46px);
		pointer-events: none;
		border: 2px solid var(--line);
		transition: border-color 0.6s ease;
	}
	.bracket.tl { top: 14px; left: 14px; border-right: 0; border-bottom: 0; }
	.bracket.tr { top: 14px; right: 14px; border-left: 0; border-bottom: 0; }
	.bracket.bl { bottom: 14px; left: 14px; border-right: 0; border-top: 0; }
	.bracket.br { bottom: 14px; right: 14px; border-left: 0; border-top: 0; }

	/* --- top bar --- */
	.topbar {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		z-index: 1;
	}
	.brand {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.logo {
		font-family: var(--chrome);
		font-size: clamp(1.2rem, 3.2vw, 1.9rem);
		font-weight: 600;
		letter-spacing: 0.55em;
		color: var(--accent);
		text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 55%, transparent);
		transition: color 0.6s ease;
	}
	.tag {
		font-family: var(--chrome);
		font-size: clamp(0.5rem, 1.4vw, 0.68rem);
		letter-spacing: 0.24em;
		text-transform: uppercase;
		color: var(--dim);
		opacity: 0.7;
	}
	.sysinfo {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 0.3rem;
		font-family: var(--chrome);
	}
	.status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: clamp(0.62rem, 1.6vw, 0.78rem);
		letter-spacing: 0.24em;
		color: var(--accent);
	}
	.clock {
		font-size: clamp(0.62rem, 1.6vw, 0.78rem);
		letter-spacing: 0.2em;
		color: var(--dim);
		font-variant-numeric: tabular-nums;
		opacity: 0.8;
	}
	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		background: var(--accent);
		box-shadow: 0 0 10px var(--accent);
		animation: blink 2.4s ease-in-out infinite;
	}
	.dot.listening, .dot.thinking { animation-duration: 0.9s; }
	.dot.speaking { animation-duration: 1.3s; }
	.dot.off {
		background: #3d5560;
		box-shadow: none;
		animation: none;
	}
	@keyframes blink {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.35; }
	}

	/* --- centre stage --- */
	.stage {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 0;
		z-index: 1;
	}
	.orb-frame {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
		width: min(58vmin, 520px);
		height: min(58vmin, 520px);
	}
	/* slowly rotating outer ring behind the canvas for depth */
	.orb-frame::before {
		content: '';
		position: absolute;
		inset: -3%;
		border-radius: 50%;
		border: 1px solid var(--line-soft);
		border-top-color: var(--line);
		border-right-color: var(--line);
		animation: spin 18s linear infinite;
	}
	.orb-frame::after {
		content: '';
		position: absolute;
		inset: 6%;
		border-radius: 50%;
		border: 1px dashed var(--line-soft);
		animation: spin 40s linear infinite reverse;
	}
	@keyframes spin { to { transform: rotate(360deg); } }
	.orb-wrap {
		position: relative;
		width: 100%;
		height: 100%;
		filter: drop-shadow(0 0 26px color-mix(in srgb, var(--accent) 35%, transparent));
	}
	/* --- readout --- */
	.readout {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: flex-start;
		gap: 0.5rem;
		min-height: 6.5rem;
		max-width: min(86vw, 52rem);
		margin: 0 auto;
		text-align: center;
		z-index: 1;
	}
	.transcript {
		font-family: var(--chrome);
		color: var(--dim);
		font-size: clamp(0.8rem, 2vw, 1rem);
		letter-spacing: 0.04em;
		min-height: 1.3rem;
		margin: 0;
		opacity: 0.9;
	}
	.transcript:not(:empty)::before {
		content: '‹ ';
		opacity: 0.55;
	}
	.transcript:not(:empty)::after {
		content: ' ›';
		opacity: 0.55;
	}
	.response {
		color: #eaf7fc;
		font-size: clamp(1.15rem, 3vw, 1.6rem);
		line-height: 1.45;
		font-weight: 300;
		min-height: 2rem;
		margin: 0;
		text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 40%, transparent);
	}
	.caret {
		display: inline-block;
		width: 0.5ch;
		height: 1.05em;
		margin-left: 0.15em;
		vertical-align: -0.15em;
		background: var(--accent);
		box-shadow: 0 0 8px var(--accent);
		animation: caret 1s steps(2) infinite;
	}
	@keyframes caret { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
	.error {
		font-family: var(--chrome);
		color: #ff6b5c;
		font-size: 0.8rem;
		letter-spacing: 0.05em;
		margin: 0.2rem 0 0;
	}

	/* --- controls --- */
	.controls {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.8rem;
		z-index: 1;
	}
	.ptt {
		position: relative;
		background: color-mix(in srgb, var(--accent) 12%, #06121a);
		border: 1px solid var(--line);
		color: var(--accent);
		padding: 0.85rem 2.6rem;
		border-radius: 999px;
		font-family: var(--chrome);
		font-size: clamp(0.72rem, 1.8vw, 0.9rem);
		letter-spacing: 0.22em;
		cursor: pointer;
		transition: background 0.18s, box-shadow 0.18s, color 0.18s, transform 0.1s;
		box-shadow: 0 0 0 color-mix(in srgb, var(--accent) 40%, transparent);
	}
	.ptt:hover {
		background: color-mix(in srgb, var(--accent) 22%, #06121a);
		box-shadow: 0 0 24px color-mix(in srgb, var(--accent) 30%, transparent);
	}
	.ptt:active { transform: scale(0.98); }
	.ptt.active {
		background: color-mix(in srgb, var(--accent) 35%, #06121a);
		color: #fff;
		box-shadow: 0 0 34px color-mix(in srgb, var(--accent) 55%, transparent);
	}
	.ptt.active .ptt-ring {
		position: absolute;
		inset: -4px;
		border-radius: 999px;
		border: 1px solid var(--accent);
		opacity: 0.6;
		animation: ptt-pulse 1.4s ease-out infinite;
	}
	@keyframes ptt-pulse {
		0% { transform: scale(1); opacity: 0.6; }
		100% { transform: scale(1.25); opacity: 0; }
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 1.2rem;
		font-family: var(--chrome);
		font-size: 0.66rem;
		letter-spacing: 0.18em;
		color: var(--dim);
		text-transform: uppercase;
	}
	.handsfree {
		display: flex;
		gap: 0.45rem;
		align-items: center;
		cursor: pointer;
		user-select: none;
	}
	.handsfree input {
		appearance: none;
		width: 0.85rem;
		height: 0.85rem;
		border: 1px solid var(--line);
		border-radius: 3px;
		background: transparent;
		cursor: pointer;
		position: relative;
	}
	.handsfree input:checked {
		background: var(--accent);
		box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 60%, transparent);
	}
	.hint {
		opacity: 0.55;
	}
	.latency {
		opacity: 0.7;
		font-variant-numeric: tabular-nums;
		letter-spacing: 0.08em;
		text-transform: none;
	}

	@media (max-width: 560px) {
		.tag { display: none; }
	}
</style>
