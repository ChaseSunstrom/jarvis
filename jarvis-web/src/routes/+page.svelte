<script lang="ts">
	import { onMount } from 'svelte';
	import { PipelineClient, type PipelineState } from '$lib/pipeline';
	import { MicCapture } from '$lib/audio/capture';
	import { Player } from '$lib/audio/playback';
	import { EnergyVAD } from '$lib/wake';
	import Orb from '$lib/components/Orb.svelte';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import ModeToggle from '$lib/components/ModeToggle.svelte';
	import { accentFor } from '$lib/tokens';
	import { prefersReducedMotion, watchReducedMotion } from '$lib/motion';
	import {
		assistantPlaceholder,
		fromArchive,
		settled,
		userMessage,
		withDelta,
		withError,
		withFinal,
		withThinking,
		withToolEnd,
		withToolStart,
		type ChatMessage
	} from '$lib/chat';
	import {
		deleteConversation,
		getConversation,
		listConversations
	} from '$lib/conversations';
	import type { ConversationSummary } from '$lib/jarvisClient';

	// `turnState` rather than `state`: a variable called `state` in a component
	// makes `$state` ambiguous to the tooling — svelte-check reads it as the
	// store-subscription form of that variable and reports the whole file as
	// untyped, which is how a page this central ended up unchecked.
	let turnState = $state<PipelineState>('idle');
	let transcript = $state('');
	let response = $state('');
	let statusMsg = $state('booting');
	let errorMsg = $state('');
	let capturing = $state(false);
	// Muting is the only voice control this HUD has left. There is no
	// push-to-talk: the mic opens when the page does and the VAD decides when a
	// turn starts, so the one thing a person needs from a button is a way to be
	// sure nothing is listening. Remembered across reloads, because a kill
	// switch that forgets is not one.
	let muted = $state(false);
	// Why the mic is not open, when it is not. Distinguishes "you muted it" from
	// "the browser said no" from "this machine has no microphone", which all
	// look identical from a dead orb.
	let micError = $state('');
	let orbLevel = $state(0);
	let latText = $state('');
	/** What is in the type-instead box, and whether it is mid-send. */
	let draft = $state('');
	let sending = $state(false);

	/*
	 * --- chat mode ---------------------------------------------------------
	 *
	 * The same assistant, read instead of heard. It is a MODE on this page and
	 * not a route of its own, for one reason: both modes need the one socket,
	 * the one PipelineClient and the one microphone, and a route boundary would
	 * tear all three down on every switch — which would mean a turn in flight
	 * dies because somebody wanted to read the answer rather than hear it.
	 *
	 * So the toggle swaps the markup and nothing else. Everything below feeds
	 * both surfaces from the same callbacks.
	 */
	const MODE_KEY = 'jarvis.mode';
	let chatMode = $state(false);
	let messages = $state<ChatMessage[]>([]);
	let conversations = $state<ConversationSummary[]>([]);
	let historyError = $state('');
	/** The conversation the transcript on screen belongs to. */
	let openConversationId = $state<string | null>(null);
	/**
	 * Whether a typed question is also answered out loud.
	 *
	 * Off by default and remembered. A console left open in a bedroom that
	 * reads every reply to the room is not a feature, and the person typing is
	 * by definition looking at the screen. Spoken questions are always answered
	 * out loud whatever this says — that path never comes through here.
	 */
	const SPEAK_KEY = 'jarvis.chat.speak';
	let speakReplies = $state(false);

	let ws: WebSocket | null = null;
	let client: PipelineClient | null = null;
	let mic: MicCapture | null = null;
	// Reactive, and reported in the DOM, because "the button says LISTENING" and
	// "the microphone is open" are different claims and only the second one
	// matters. The label cannot tell them apart — it reads LISTENING whenever
	// nothing has gone wrong, including when nothing has been tried.
	let micReady = $state(false);
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

	// The dial that is currently in flight, so a click that lands while the
	// on-mount connect is still handshaking joins it instead of opening a second
	// socket. Two sockets is not merely wasteful: `client` would be rebound to
	// the newer one while the older one's `onmessage` still routes frames into
	// it, and a run's events can end up on a socket nobody is listening to.
	let wsPending: Promise<void> | null = null;

	function connectWs(): Promise<void> {
		if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve();
		if (wsPending) return wsPending;
		wsPending = openSocket().finally(() => (wsPending = null));
		return wsPending;
	}

	function openSocket(): Promise<void> {
		return new Promise((resolve, reject) => {
			const proto = location.protocol === 'https:' ? 'wss' : 'ws';
			const socket = new WebSocket(`${proto}://${location.host}/ws`);
			ws = socket;
			ws.binaryType = 'arraybuffer';
			client = new PipelineClient(
				(data) => {
					if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
				},
				{
					onState: (s) => {
						turnState = s;
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
						// A SPOKEN turn's question only exists once it has been
						// transcribed, so this is where it joins the transcript.
						// A typed one was added when it was sent — see sendText.
						if (text) {
							messages = [...messages, userMessage(text), assistantPlaceholder()];
						}
					},
					onDelta: (d) => {
						if (lat.firstDelta == null) {
							lat.firstDelta = performance.now() - tAudioEnd;
							latText = fmtLat();
						}
						response += d;
						messages = withDelta(messages, d);
					},
					onResponse: (text) => {
						if (text) response = text;
						messages = withFinal(messages, text);
					},
					onToolStart: (call) => {
						messages = withToolStart(messages, call);
					},
					onToolEnd: (call) => {
						messages = withToolEnd(messages, call);
					},
					onThinking: (delta) => {
						messages = withThinking(messages, delta);
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
						if (turnState === 'speaking') {
							turnState = 'idle';
							statusMsg = 'idle';
						}
					},
					onRunEnd: () => {
						if (turnState !== 'speaking') {
							turnState = 'idle';
							statusMsg = 'idle';
						}
						messages = settled(messages);
						// The conversation id only exists once the backend has
						// answered, so this is the first moment the sidebar can
						// show the turn that just happened — and the moment the
						// title, taken from the first thing said, is decided.
						openConversationId = client?.conversationId ?? openConversationId;
						void refreshHistory();
					},
					onError: (code, message) => {
						errorMsg = `${code}: ${message}`;
						capturing = false;
						statusMsg = 'error';
						// On the message rather than only in the HUD's error line:
						// by the time a second question has been asked, a banner is
						// about neither of them.
						messages = withError(messages, code, message);
					}
				}
			);
			socket.onopen = () => resolve();
			socket.onerror = () => reject(new Error('websocket error'));
			socket.onmessage = (e) => {
				if (typeof e.data === 'string') client?.handleMessage(e.data);
			};
			socket.onclose = () => {
				// Only the socket that is still the live one may declare us offline.
				if (ws !== socket) return;
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
				if (muted) {
					// Fed nothing while muted, so the VAD cannot be sitting on
					// half a phrase from before the mute when it comes back.
					vad.reset();
				} else if (!capturing && turnState === 'idle') {
					// The orb listens to the room; chat mode does not.
					//
					// Standing in front of the orb, an always-on VAD is the whole
					// point — you speak and it answers. In front of a keyboard it is
					// the opposite: every remark made in the room becomes a turn, and
					// each one lands in the transcript and the history sidebar as
					// though it had been asked. Chat mode takes voice on the button
					// instead, so speech is still a first-class way in and only ever
					// when it was meant.
					if (!chatMode && vad.feed(r) === 'speech-start') void startInteraction();
				} else if (capturing) {
					if (vad.feed(r) === 'speech-end') stopCapture();
				}
				// Barge-in: user speaks over TTS -> kill playback, new run.
				if (!muted && !chatMode && turnState === 'speaking' && bargeVad.feed(r) === 'speech-start') {
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
		if (capturing || muted) return;
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
		// The conversation on screen, explicitly — not whichever one the client
		// last happened to run. Without this a spoken turn used `client
		// .conversationId`, which is null after "new conversation" and after any
		// reconnect, so the backend minted a fresh id and the answer appeared in
		// a NEW row in the sidebar while the user was looking at an old one.
		client?.startRun({ pipeline: pipelineId, conversationId: openConversationId });
		if (e2eMode) setTimeout(() => stopCapture(), 1500);
	}

	/**
	 * Ask by typing.
	 *
	 * The HUD had no `<input>` at all, so somebody who denied the microphone
	 * prompt — or is on a machine with none — could not say anything to Jarvis by
	 * any means. This is the same pipeline the voice path uses, entered one stage
	 * later: `startTextRun` sets `start_stage: 'intent'`, so the answer still
	 * streams back and is still spoken.
	 */
	async function sendText(text = draft.trim()): Promise<void> {
		if (!text || sending) return;
		sending = true;
		errorMsg = '';
		// Echoed straight into the readout, because there is no stt-end coming to
		// fill it in and the HUD's whole layout assumes the top line says what you
		// asked. It is also already true — you typed it.
		transcript = text;
		response = '';
		lat = {};
		latText = '';
		// Both messages up front, before the socket work: the placeholder is
		// where the tool rows and the reasoning land, and a turn that spends
		// nine seconds in tool calls with nothing on screen reads as a page
		// that has stopped working.
		messages = [...messages, userMessage(text), assistantPlaceholder()];
		try {
			await connectWs();
		} catch {
			errorMsg = 'cannot reach server websocket';
			messages = withError(messages, 'offline', 'cannot reach the server');
			sending = false;
			return;
		}
		if (!pipelineId && client) {
			try {
				pipelineId = await client.resolvePipelineId(pipelineName);
			} catch (e) {
				console.warn('pipeline list failed, using preferred', e);
			}
		}
		// The same baseline the spoken path measures from: the moment there is
		// nothing left for the human to do and the wait begins.
		tAudioEnd = performance.now();
		client?.startTextRun(text, {
			pipeline: pipelineId,
			conversationId: openConversationId,
			// In chat mode the toggle decides. Outside it the HUD has always
			// spoken its replies, and that stays true.
			speak: chatMode ? speakReplies : true
		});
		draft = '';
		statusMsg = 'processing';
		sending = false;
	}

	// --- chat mode: history --------------------------------------------------
	/**
	 * Reload the conversation list.
	 *
	 * Only in chat mode: the sidebar is the only thing that reads it, and the
	 * orb should not spend a round trip per turn maintaining a list nobody is
	 * looking at. An older jarvis-core answers `unknown_command`, which is a
	 * missing feature and not an error worth a banner — the sidebar just says
	 * so and everything else works.
	 */
	async function refreshHistory(): Promise<void> {
		if (!chatMode || !client) return;
		try {
			conversations = await listConversations((p) => client!.command(p));
			historyError = '';
		} catch (e) {
			conversations = [];
			historyError =
				(e as { code?: string })?.code === 'unknown_command'
					? 'This jarvis-core does not keep conversation history.'
					: 'Could not load past conversations.';
		}
	}

	/** Open a past conversation: its transcript on screen, its id on the wire. */
	async function openConversation(id: string): Promise<void> {
		if (!client) return;
		try {
			const conversation = await getConversation((p) => client!.command(p), id);
			messages = fromArchive(conversation);
			openConversationId = id;
			// So the next turn continues THIS conversation rather than whichever
			// one the client last ran.
			client.conversationId = id;
			transcript = '';
			response = '';
			errorMsg = '';
		} catch {
			historyError = 'Could not open that conversation.';
		}
	}

	function newConversation(): void {
		messages = [];
		openConversationId = null;
		if (client) client.conversationId = null;
		transcript = '';
		response = '';
		errorMsg = '';
	}

	async function forgetConversation(id: string): Promise<void> {
		if (!client) return;
		try {
			await deleteConversation((p) => client!.command(p), id);
		} catch {
			historyError = 'Could not forget that conversation.';
			return;
		}
		// Clear the view too if it was the one being read; leaving a transcript
		// on screen under an id the server has forgotten would resume nothing.
		if (openConversationId === id) newConversation();
		await refreshHistory();
	}

	/**
	 * Chat mode's microphone: press, speak, and it stops when you do.
	 *
	 * The orb's mic is a mute switch over an always-on VAD. That is right in
	 * front of the orb and wrong in front of a keyboard, so here the same
	 * hardware is driven the other way round: nothing is captured until this is
	 * pressed, and the VAD's job is only to notice when the sentence has ended.
	 *
	 * Nothing leaves the browser in between — `MicCapture.onChunk` sends only
	 * while `capturing` — so the button is the privacy boundary as well as the
	 * control, and chat mode needs no separate mute.
	 */
	async function toggleVoiceTurn(): Promise<void> {
		if (capturing) {
			stopCapture();
			return;
		}
		// A press is consent to listen, so it also lifts a mute left over from
		// the orb — otherwise the button would appear to do nothing.
		if (muted) {
			muted = false;
			try {
				localStorage.setItem(MUTE_KEY, '0');
			} catch {
				/* private mode; the unmute still holds for this page */
			}
		}
		try {
			await ensureMic();
			micError = '';
		} catch (e) {
			micError = micTrouble(e);
			return;
		}
		await startInteraction();
	}

	function toggleChatMode(): void {
		chatMode = !chatMode;
		try {
			localStorage.setItem(MODE_KEY, chatMode ? 'chat' : 'orb');
		} catch {
			// Private mode. The toggle still works for this page.
		}
		if (chatMode) void refreshHistory();
	}

	function toggleSpeakReplies(): void {
		speakReplies = !speakReplies;
		try {
			localStorage.setItem(SPEAK_KEY, speakReplies ? '1' : '0');
		} catch {
			/* not worth a message */
		}
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

	const MUTE_KEY = 'jarvis.muted';

	async function toggleMute(): Promise<void> {
		muted = !muted;
		try {
			localStorage.setItem(MUTE_KEY, muted ? '1' : '0');
		} catch {
			// Private mode, or storage disabled. The mute still works for this
			// page; it just will not be remembered, which is not worth a message.
		}
		if (muted) {
			// Actually stop, rather than merely ignoring what arrives: a run in
			// flight is already streaming audio, and a mute that lets the current
			// sentence finish uploading is not a mute.
			if (capturing) stopCapture();
			player.stopAll();
			mic?.stop();
			micReady = false;
			mic = null;
			micLevel = 0;
			return;
		}
		try {
			await ensureMic();
			micError = '';
		} catch (e) {
			micError = micTrouble(e);
		}
	}

	/** Why getUserMedia said no, in words somebody can act on. */
	function micTrouble(e: unknown): string {
		const name = (e as { name?: string } | null)?.name ?? '';
		if (name === 'NotAllowedError' || name === 'SecurityError') {
			return 'MIC BLOCKED — ALLOW IT IN THE BROWSER';
		}
		if (name === 'NotFoundError' || name === 'OverconstrainedError') {
			return 'NO MICROPHONE FOUND';
		}
		return 'MIC UNAVAILABLE';
	}

	/** What the one remaining control says. */
	let micLabel = $derived(
		muted ? 'MUTED — CLICK TO LISTEN' : micError ? micError : 'LISTENING — CLICK TO MUTE'
	);

	// --- presentation: accent colour + labels that track pipeline state ---
	// The colours come from `$lib/tokens` (STATE_ACCENT), the same table the
	// design tokens declare — the HUD does not own a private palette.
	const LABEL: Record<string, string> = {
		idle: 'STANDBY',
		listening: 'LISTENING',
		thinking: 'PROCESSING',
		speaking: 'RESPONDING'
	};
	let accent = $derived(accentFor(turnState, Boolean(errorMsg)));
	// `booting` is not a pipeline state. It is the window between the server
	// rendering this page and the browser having run any of it: no handler is
	// bound to the PTT button yet and there is no socket. Reporting STANDBY
	// there — the word this HUD uses for "ready, say something" — is a claim the
	// markup makes before anything can act on it, and a press in that window
	// goes nowhere. `online` already knew the difference; the label did not.
	let stateLabel = $derived(
		statusMsg === 'booting'
			? 'CONNECTING'
			: statusMsg === 'disconnected'
				? 'OFFLINE'
				: (LABEL[turnState] ?? turnState.toUpperCase())
	);
	let online = $derived(statusMsg !== 'disconnected' && statusMsg !== 'booting');
	let clock = $state('--:--:--');

	function tickClock(): void {
		const d = new Date();
		const p = (n: number) => n.toString().padStart(2, '0');
		clock = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
	}

	onMount(() => {
		const params = new URLSearchParams(location.search);
		e2eMode = params.has('e2e');
		try {
			muted = localStorage.getItem(MUTE_KEY) === '1';
			// `?mode=chat` wins over the remembered choice, so a link can open
			// straight into chat and the e2e suite can reach it without a click.
			const wanted = params.get('mode') ?? localStorage.getItem(MODE_KEY);
			chatMode = wanted === 'chat';
			speakReplies = localStorage.getItem(SPEAK_KEY) === '1';
		} catch {
			muted = false;
			chatMode = params.get('mode') === 'chat';
		}
		fetch('/api/config')
			.then((r) => r.json())
			.then((c) => (pipelineName = c.pipeline ?? 'Jarvis'))
			.catch(() => {});
		connectWs()
			.then(() => {
				statusMsg = 'idle';
				// The sidebar's first fill. After a socket, because the commands
				// go down it, and only in chat mode — see refreshHistory.
				void refreshHistory();
				// Headless Chromium has no microphone, so the VAD that starts
				// every turn in real use never fires and the suite would wait
				// forever for a transcript. This is the one thing ?e2e=1 has to
				// stand in for now that there is no button to click: the turn
				// itself, its audio and its end are all the real code paths.
				//
				// Not in chat mode: there the suite types, which is the real
				// entry point for that surface and needs no stand-in.
				if (e2eMode && !chatMode) void startInteraction();
			})
			.catch(() => (statusMsg = 'disconnected'));

		// Open the microphone with the page. There is no button that does this
		// any more, so if it does not happen here it does not happen — and the
		// HUD would sit at STANDBY looking attentive and deaf.
		//
		// getUserMedia resolves without a prompt once this origin has been
		// granted, which is the second visit onward; the first shows the
		// browser's own permission UI, which is the right place for that
		// question to be asked. A refusal is reported on the button rather than
		// retried, because retrying a denial is how a page gets itself blocked.
		if (!muted) {
			void ensureMic().catch((e) => {
				micError = micTrouble(e);
				console.warn('mic unavailable', e);
			});
		}

		tickClock();
		const clk = setInterval(tickClock, 1000);

		// The audio level, followed frame by frame. This is the other half of the
		// orb's motion — it swells the ball with the voice, and it scales the
		// no-WebGL fallback outright — so it stops when the orb does. Left running
		// under reduced motion it would be a per-frame loop feeding a picture that
		// is only ever redrawn on a state change, which costs a wall panel its
		// battery to animate nothing.
		let raf = 0;
		const tick = () => {
			orbLevel = turnState === 'speaking' ? player.level() * 2 : Math.min(micLevel * 4, 1);
			raf = requestAnimationFrame(tick);
		};
		const follow = (reduced: boolean) => {
			if (reduced) {
				if (raf) cancelAnimationFrame(raf);
				raf = 0;
				orbLevel = 0;
			} else if (!raf) {
				raf = requestAnimationFrame(tick);
			}
		};
		follow(prefersReducedMotion());
		const unwatchMotion = watchReducedMotion(follow);
		return () => {
			if (raf) cancelAnimationFrame(raf);
			clearInterval(clk);
			unwatchMotion();
		};
	});
</script>

{#if chatMode}
	<ChatPanel
		{messages}
		{conversations}
		conversationId={openConversationId}
		{historyError}
		busy={sending || turnState === 'thinking'}
		{turnState}
		{muted}
		{micLabel}
		{orbLevel}
		{capturing}
		{accent}
		speak={speakReplies}
		onSend={(text) => void sendText(text)}
		onNew={newConversation}
		onOpen={(id) => void openConversation(id)}
		onDelete={(id) => void forgetConversation(id)}
		onVoice={() => void toggleVoiceTurn()}
		onToggleSpeak={toggleSpeakReplies}
		onToggleMode={toggleChatMode}
	/>
{:else}
<main class="hud" style="--accent: {accent}" data-state={turnState}>
	<div class="jv-grid" aria-hidden="true"></div>
	<span class="jv-bracket tl" aria-hidden="true"></span>
	<span class="jv-bracket tr" aria-hidden="true"></span>
	<span class="jv-bracket bl" aria-hidden="true"></span>
	<span class="jv-bracket br" aria-hidden="true"></span>

	<header class="topbar">
		<div class="brand">
			<span class="logo">JARVIS</span>
			<span class="tag">Just A Rather Very Intelligent System</span>
		</div>
		<div class="sysinfo">
			<span class="status" data-testid="status" role="status" aria-live="polite">
				<span class="dot {turnState}" class:off={!online} aria-hidden="true"></span>
				{stateLabel}
			</span>
			<div class="sysrow">
				<ModeToggle chat={chatMode} onToggle={toggleChatMode} />
				<span class="clock" aria-label="Local time">{clock}</span>
			</div>
		</div>
	</header>

	<section class="stage" aria-hidden="true">
		<div class="orb-frame">
			<div class="orb-wrap">
				<Orb level={orbLevel} orbState={turnState} />
			</div>
		</div>
	</section>

	<section class="readout" aria-label="Conversation">
		<p class="transcript" data-testid="transcript" aria-live="polite" aria-label="What you said">
			{transcript}
		</p>
		<p class="response" data-testid="response" aria-live="polite" aria-label="Jarvis says">
			{response}{#if turnState === 'thinking' || turnState === 'listening'}<span
					class="caret"
					aria-hidden="true"
				></span>{/if}
		</p>
		{#if errorMsg}
			<p class="error" data-testid="error" role="alert">{errorMsg}</p>
		{/if}
	</section>

	<footer class="controls">
		<!--
		  No `aria-label`. One used to sit here saying "Mute the microphone",
		  which OVERRODE the visible text — so when the button read
		  MIC BLOCKED — ALLOW IT IN THE BROWSER, a screen reader announced a
		  working mute button and the one thing the sighted user could see was the
		  one thing the blind user could not. `aria-pressed` still carries the
		  mute state, which is the part the label cannot say.
		-->
		<button
			type="button"
			class="ptt"
			class:active={capturing}
			class:muted
			data-testid="mic"
			data-mic={micReady ? 'open' : 'closed'}
			onclick={toggleMute}
			aria-pressed={muted}
		>
			<span class="ptt-ring" aria-hidden="true"></span>
			{micLabel}
		</button>

		<!--
		  Type instead of speaking.
		  A denied microphone prompt used to end the conversation permanently:
		  there was no `<input>` on this page at all, so there was no second way
		  to ask Jarvis anything. This runs the same pipeline — see sendText().
		-->
		<form
			class="say"
			data-testid="text-form"
			onsubmit={(e) => {
				e.preventDefault();
				void sendText();
			}}
		>
			<label class="jv-sr-only" for="hud-text">Type what you want to say to Jarvis</label>
			<input
				id="hud-text"
				type="text"
				class="say-input"
				data-testid="text-input"
				placeholder="or type it…"
				autocomplete="off"
				bind:value={draft}
			/>
			<button type="submit" class="say-send" data-testid="text-send" disabled={!draft.trim()}>
				SEND
			</button>
		</form>

		<div class="meta">
			<span class="hint" aria-hidden="true">
				{muted ? 'NOTHING IS BEING HEARD' : 'JUST SPEAK'}
			</span>
			{#if latText}<span class="latency" data-testid="latency" aria-label="Pipeline latency"
					>{latText}</span
				>{/if}
		</div>
	</footer>
</main>
{/if}

<style>
	.sysrow {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
	}

	/*
	 * The HUD's one liberty with the design system: `--accent` is a *live*
	 * colour that tracks the pipeline state (see STATE_ACCENT in $lib/tokens),
	 * so the shared line/dim tokens are re-derived from it here. Every shared
	 * utility below — .jv-grid, .jv-bracket — then picks the state colour up by
	 * inheritance instead of needing a HUD-specific copy.
	 */
	.hud {
		--chrome: var(--jv-font-chrome);
		--body: var(--jv-font-body);
		--dim: color-mix(in srgb, var(--accent) 60%, var(--jv-text-faint));
		--line: color-mix(in srgb, var(--accent) 32%, transparent);
		--line-soft: color-mix(in srgb, var(--accent) 14%, transparent);

		--jv-line: var(--line);
		--jv-line-soft: var(--line-soft);
		/* #000 here is a mask stencil, not a colour — only its alpha is read. */
		--jv-grid-mask: radial-gradient(ellipse 75% 75% at 50% 50%, #000 40%, transparent 88%);
		--jv-bracket-size: clamp(22px, 4vw, 46px);
		--jv-bracket-inset: 14px;

		position: relative;
		/*
		 * MIN-height, and nothing hidden.
		 *
		 * This was `height: 100dvh; overflow: hidden` on a four-row grid, which is
		 * correct exactly while the content fits. A long answer, or a laptop in
		 * landscape at 500 px tall, pushed the readout and the mute button past the
		 * bottom edge — and with the overflow hidden there was no scroll path to
		 * them at all: the one control on the page became unreachable at the moment
		 * there was most to read. The rows still fill the viewport when there is
		 * room, because `min-height` and `1fr` do that on their own.
		 */
		min-height: 100vh;
		min-height: 100dvh;
		display: grid;
		/*
		 * `min-content` as the stage row's floor: the orb is a fixed square, and a
		 * 1fr row is free to squeeze a track below its content, which would slide
		 * the orb over the readout instead of making the page taller.
		 */
		grid-template-rows: auto minmax(min-content, 1fr) auto auto;
		padding: clamp(0.9rem, 2.5vw, 2rem);
		gap: clamp(0.5rem, 2vh, 1.5rem);
		color: var(--jv-text);
		font-family: var(--body);
		/* Sideways is still forbidden — nothing here is wider than the viewport. */
		overflow-x: hidden;
		background:
			radial-gradient(
				ellipse 70% 55% at 50% 44%,
				color-mix(in srgb, var(--accent) 16%, transparent),
				transparent 70%
			),
			var(--jv-bg);
		transition: background 0.6s ease;
	}

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
		font-size: var(--jv-fs-display);
		font-weight: 600;
		letter-spacing: 0.55em;
		color: var(--accent);
		text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 55%, transparent);
		transition: color 0.6s ease;
	}
	.tag {
		font-family: var(--chrome);
		font-size: clamp(0.7rem, 1.4vw, 0.85rem);
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
		font-size: clamp(0.8rem, 1.6vw, 0.95rem);
		letter-spacing: 0.24em;
		color: var(--accent);
	}
	.clock {
		font-size: clamp(0.8rem, 1.6vw, 0.95rem);
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
		background: color-mix(in srgb, var(--jv-text-faint) 45%, var(--jv-bg));
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
		font-size: clamp(0.95rem, 2vw, 1.15rem);
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
		color: var(--jv-text-bright);
		font-size: clamp(1.25rem, 3vw, 1.7rem);
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
		color: var(--jv-danger);
		font-size: var(--jv-fs-md);
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
		background: color-mix(in srgb, var(--accent) 12%, var(--jv-bg-raised));
		border: 1px solid var(--line);
		color: var(--accent);
		padding: 0.85rem 2.6rem;
		border-radius: 999px;
		font-family: var(--chrome);
		font-size: clamp(0.85rem, 1.8vw, 1.05rem);
		letter-spacing: 0.22em;
		cursor: pointer;
		transition: background 0.18s, box-shadow 0.18s, color 0.18s, transform 0.1s;
		box-shadow: 0 0 0 color-mix(in srgb, var(--accent) 40%, transparent);
	}
	.ptt:hover {
		background: color-mix(in srgb, var(--accent) 22%, var(--jv-bg-raised));
		box-shadow: 0 0 24px color-mix(in srgb, var(--accent) 30%, transparent);
	}
	.ptt:active { transform: scale(0.98); }
	.ptt.active {
		background: color-mix(in srgb, var(--accent) 35%, var(--jv-bg-raised));
		color: var(--jv-text-bright);
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
	/*
	 * The type-instead row. Deliberately quieter than the mute button: speaking
	 * is still the way this thing is meant to be used, and a text box drawn as
	 * loudly as the orb's own control would say otherwise. It is always present
	 * rather than appearing when the microphone fails, because "there is another
	 * way to do this" is not a message worth hiding until the moment somebody is
	 * already stuck.
	 */
	.say {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		width: min(86vw, 34rem);
	}
	.say-input {
		flex: 1 1 auto;
		min-width: 0;
		font-family: var(--chrome);
		font-size: var(--jv-fs-sm);
		letter-spacing: 0.08em;
		color: var(--jv-text-bright);
		background: color-mix(in srgb, var(--accent) 6%, var(--jv-bg-raised));
		border: 1px solid var(--line-soft);
		border-radius: var(--jv-radius-pill);
		padding: 0.5rem 1rem;
		transition:
			border-color var(--jv-dur-fast) var(--jv-ease-out),
			box-shadow var(--jv-dur-fast) var(--jv-ease-out);
	}
	.say-input::placeholder {
		color: var(--dim);
		opacity: 0.7;
	}
	.say-input:hover,
	.say-input:focus {
		border-color: var(--line);
		box-shadow: 0 0 18px color-mix(in srgb, var(--accent) 18%, transparent);
	}
	.say-send {
		flex: none;
		font-family: var(--chrome);
		font-size: var(--jv-fs-xs);
		letter-spacing: 0.2em;
		color: var(--accent);
		background: transparent;
		border: 1px solid var(--line);
		border-radius: var(--jv-radius-pill);
		padding: 0.5rem 1.1rem;
		cursor: pointer;
		transition:
			background var(--jv-dur-fast) var(--jv-ease-out),
			box-shadow var(--jv-dur-fast) var(--jv-ease-out);
	}
	.say-send:hover:not(:disabled) {
		background: color-mix(in srgb, var(--accent) 16%, transparent);
		box-shadow: 0 0 18px color-mix(in srgb, var(--accent) 24%, transparent);
	}
	.say-send:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.meta {
		display: flex;
		align-items: center;
		gap: 1.2rem;
		font-family: var(--chrome);
		font-size: var(--jv-fs-sm);
		letter-spacing: 0.18em;
		color: var(--dim);
		text-transform: uppercase;
	}
	/* Muted is a state the eye should catch without reading the label. */
	.ptt.muted {
		border-color: color-mix(in srgb, var(--jv-text-faint) 60%, transparent);
		color: var(--jv-text-faint);
		box-shadow: none;
	}
	.ptt.muted .ptt-ring { animation: none; opacity: 0; }
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
