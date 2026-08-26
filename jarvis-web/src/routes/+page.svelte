<script lang="ts">
	import { onMount } from 'svelte';
	import { PipelineClient, type PipelineEvent, type PipelineState } from '$lib/pipeline';
	import { MicCapture } from '$lib/audio/capture';
	import { Player } from '$lib/audio/playback';
	import { EnergyVAD } from '$lib/wake';
	import ChatPanel from '$lib/components/ChatPanel.svelte';
	import { Panel, Reactor, ScreenState } from '$lib/ui';
	import { setHudStatus } from '$lib/hudStatus.svelte';
	import { prefersReducedMotion, watchReducedMotion } from '$lib/motion';
	import {
		assistantPlaceholder,
		fromArchive,
		settled,
		userMessage,
		withDelta,
		withError,
		withFinal,
		withMemoryUsed,
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
	// Muting is the only voice control this screen has. There is no
	// push-to-talk: the mic opens when the page does and the VAD decides when a
	// turn starts, so the one thing a person needs from a button is a way to be
	// sure nothing is listening. Remembered across reloads, because a kill
	// switch that forgets is not one.
	let muted = $state(false);
	// Why the mic is not open, when it is not. Distinguishes "you muted it" from
	// "the browser said no" from "this machine has no microphone", which all
	// look identical from a still reactor.
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
	// Re-made once /api/config answers, so an install can tune the end-of-speech
	// pause without a rebuild. Constructed with the default up front because the
	// microphone may open before that request lands.
	let vad = new EnergyVAD();
	const bargeVad = new EnergyVAD({ startThreshold: 0.06, minSpeechMs: 150 });

	let pipelineName = 'Jarvis';
	let pipelineId: string | null = null;
	let pendingEnd = false;
	let e2eMode = false;
	let micLevel = 0;

	// Latency instrumentation: ms from end-of-audio to key events.
	let tAudioEnd = 0;
	let lat: { stt?: number; firstDelta?: number; tts?: number } = {};

	/*
	 * --- this turn ---------------------------------------------------------
	 *
	 * The stamp of every pipeline event, so the THIS TURN panel can say what
	 * each stage cost — transcribe, first token, speak — from the events the
	 * page already receives rather than from a number somebody typed. Reset
	 * when a turn starts; read by the panel as durations between stamps.
	 */
	let stamps = $state<Record<string, number>>({});
	let turnStartedAt = $state<number | null>(null);
	let turnAt = $state('');
	let replyAt = $state('');

	function clock(d = new Date()): string {
		const p = (n: number) => n.toString().padStart(2, '0');
		return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
	}

	function beginTurn(): void {
		stamps = {};
		turnStartedAt = performance.now();
		turnAt = clock();
		replyAt = '';
	}

	function stamp(type: string): void {
		if (stamps[type] === undefined) stamps = { ...stamps, [type]: performance.now() };
	}

	/** ms between two stamps, or null while either is missing. */
	function between(from: string, to: string): number | null {
		const a = stamps[from];
		const b = stamps[to];
		return a !== undefined && b !== undefined && b >= a ? b - a : null;
	}

	/** The four stages C2 names, each a measured duration or a dash. */
	const stages = $derived([
		{ key: 'listen', label: 'listen · vad', ms: between('run-start', 'audio-end') },
		{ key: 'transcribe', label: 'transcribe · whisper', ms: between('audio-end', 'stt-end') ?? between('stt-start', 'stt-end') },
		{
			key: 'first-token',
			label: 'first token · model',
			ms: between('stt-end', 'first-delta') ?? between('intent-start', 'first-delta')
		},
		{ key: 'speak', label: 'speak · piper', ms: between('tts-start', 'tts-end') }
	]);
	const turnTotalMs = $derived(between('run-start', 'run-end'));
	const turnLive = $derived(turnStartedAt !== null && stamps['run-end'] === undefined);
	const fmtMs = (ms: number | null): string => (ms === null ? '—' : `${Math.round(ms)} ms`);
	const fmtS = (ms: number | null): string => (ms === null ? '' : `${(ms / 1000).toFixed(2)} s`);

	function fmtLat(): string {
		const parts: string[] = [];
		if (lat.stt != null) parts.push(`transcribe ${lat.stt.toFixed(0)} ms`);
		if (lat.firstDelta != null) parts.push(`first token ${lat.firstDelta.toFixed(0)} ms`);
		if (lat.tts != null) parts.push(`speak ${lat.tts.toFixed(0)} ms`);
		return parts.join(' · ');
	}

	/** The tools this turn called: the ones on the reply being written or just written. */
	const turnTools = $derived.by(() => {
		const last = messages[messages.length - 1];
		return last && last.role === 'assistant' ? last.tools : [];
	});

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
							stamp('first-delta');
							replyAt = clock();
						}
						response += d;
						messages = withDelta(messages, d);
					},
					onResponse: (text) => {
						if (text) response = text;
						if (!replyAt) replyAt = clock();
						messages = withFinal(messages, text);
					},
					onMemoryUsed: (notes) => {
						messages = withMemoryUsed(messages, notes);
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
					onEvent: (ev: PipelineEvent) => {
						stamp(ev.type);
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
						// On the message rather than only in the error line: by
						// the time a second question has been asked, a banner is
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
					// The reactor listens to the room; chat mode does not.
					//
					// Standing in front of the reactor, an always-on VAD is the
					// whole point — you speak and it answers. In front of a
					// keyboard it is the opposite: every remark made in the room
					// becomes a turn, and each one lands in the transcript and the
					// history sidebar as though it had been asked. Chat mode takes
					// voice on the button instead, so speech is still a first-class
					// way in and only ever when it was meant.
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
		beginTurn();
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
	 * The voice screen had no `<input>` at all, so somebody who denied the
	 * microphone prompt — or is on a machine with none — could not say anything
	 * to Jarvis by any means. This is the same pipeline the voice path uses,
	 * entered one stage later: `startTextRun` sets `start_stage: 'intent'`, so
	 * the answer still streams back and is still spoken.
	 */
	async function sendText(text = draft.trim()): Promise<void> {
		if (!text || sending) return;
		sending = true;
		errorMsg = '';
		// Echoed straight into the exchange, because there is no stt-end coming
		// to fill it in and the layout assumes the top line says what you asked.
		// It is also already true — you typed it.
		transcript = text;
		response = '';
		lat = {};
		latText = '';
		beginTurn();
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
		stamp('audio-end');
		client?.startTextRun(text, {
			pipeline: pipelineId,
			conversationId: openConversationId,
			// In chat mode the toggle decides. Outside it the voice screen has
			// always spoken its replies, and that stays true.
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
	 * reactor should not spend a round trip per turn maintaining a list nobody
	 * is looking at. An older jarvis-core answers `unknown_command`, which is a
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
	 * The reactor's mic is a mute switch over an always-on VAD. That is right
	 * in front of the reactor and wrong in front of a keyboard, so here the
	 * same hardware is driven the other way round: nothing is captured until
	 * this is pressed, and the VAD's job is only to notice when the sentence
	 * has ended.
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
		// the reactor — otherwise the button would appear to do nothing.
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
		stamp('audio-end');
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

	// --- presentation: labels that track pipeline state ----------------------
	const LABEL: Record<string, string> = {
		idle: 'STANDBY',
		listening: 'LISTENING',
		thinking: 'PROCESSING',
		speaking: 'RESPONDING'
	};
	// `booting` is not a pipeline state. It is the window between the server
	// rendering this page and the browser having run any of it: no handler is
	// bound yet and there is no socket. Reporting STANDBY there — the word this
	// screen uses for "ready, say something" — is a claim the markup makes
	// before anything can act on it. `online` already knew the difference; the
	// label did not.
	let stateLabel = $derived(
		statusMsg === 'booting'
			? 'CONNECTING'
			: statusMsg === 'disconnected'
				? 'OFFLINE'
				: (LABEL[turnState] ?? turnState.toUpperCase())
	);
	let online = $derived(statusMsg !== 'disconnected' && statusMsg !== 'booting');

	/*
	 * The taste checkpoint's handle. `docs/motion-review/2-orb-states.webm` is
	 * recorded by dispatching `jarvis:orb-demo` with a state name; while a demo
	 * state is set the instrument wears it and breathes a synthetic level, so a
	 * person can watch the four states without a microphone. Nothing else
	 * dispatches it, and it never touches the pipeline.
	 */
	let demoState = $state<PipelineState | null>(null);
	let demoPhase = 0;

	/** What the instrument wears: an error outranks everything, a demo the pipeline. */
	const reactorState = $derived<'idle' | 'listening' | 'thinking' | 'speaking' | 'error'>(
		errorMsg ? 'error' : (demoState ?? turnState)
	);

	// The bar's readout, written from here: the page owns the pipeline and the
	// layout owns the bar (see hudStatus.svelte.ts).
	$effect(() => {
		setHudStatus({
			label: stateLabel,
			tone: !online ? 'off' : turnState === 'idle' ? 'neutral' : 'live',
			busy: statusMsg === 'booting' || statusMsg === 'connecting',
			state: turnState
		});
	});

	/** The caption under the instrument: state · how it is listening. */
	const caption = $derived(
		[
			stateLabel.toLowerCase(),
			muted ? 'muted' : micReady ? 'hands-free' : micError ? 'mic closed' : 'mic opening'
		].join(' · ')
	);

	// The four states. Loading is the boot sequence, which owns the whole
	// viewport; what is left for the status region is the link being down (the
	// screen had an OFFLINE label and no way back) and a turn that failed.
	let screen = $derived<'ready' | 'error' | 'offline'>(
		statusMsg === 'disconnected' ? 'offline' : errorMsg ? 'error' : 'ready'
	);

	/** The mic ring's arc: a little at rest, all the way round at full level. */
	const RING_C = 2 * Math.PI * 20;
	const ringDash = $derived(`${(RING_C * (0.15 + 0.85 * orbLevel)).toFixed(1)} ${RING_C.toFixed(1)}`);

	const sessionMeta = $derived(openConversationId ? `session ${openConversationId.slice(0, 6)}` : 'this session');

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
			.then((c) => {
				pipelineName = c.pipeline ?? 'Jarvis';
				const hangover = Number(c.hangoverMs);
				if (Number.isFinite(hangover) && hangover > 0) {
					vad = new EnergyVAD({ hangoverMs: hangover });
				}
			})
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
		// screen would sit at STANDBY looking attentive and deaf.
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

		const onDemo = (e: Event) => {
			const wanted = (e as CustomEvent<string>).detail;
			demoState = (['idle', 'listening', 'thinking', 'speaking'] as const).includes(wanted as any)
				? (wanted as PipelineState)
				: null;
		};
		window.addEventListener('jarvis:orb-demo', onDemo);

		// The audio level, followed frame by frame. This is the other half of the
		// instrument's motion — the arc carries the voice — so it stops when the
		// instrument does. Left running under reduced motion it would be a
		// per-frame loop feeding a picture that is only ever redrawn on a state
		// change, which costs a wall panel its battery to animate nothing.
		let raf = 0;
		const tick = () => {
			if (demoState && demoState !== 'idle') {
				demoPhase += 0.06;
				orbLevel = 0.45 + 0.4 * Math.abs(Math.sin(demoPhase)) * (0.6 + 0.4 * Math.abs(Math.sin(demoPhase * 2.7)));
			} else {
				orbLevel = turnState === 'speaking' ? player.level() * 2 : Math.min(micLevel * 4, 1);
			}
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
			window.removeEventListener('jarvis:orb-demo', onDemo);
			unwatchMotion();
		};
	});
</script>

<svelte:head><title>Jarvis</title></svelte:head>

{#if chatMode}
	<ChatPanel
		{messages}
		{conversations}
		conversationId={openConversationId}
		{historyError}
		busy={sending || turnState === 'thinking'}
		turnState={reactorState}
		{muted}
		{micLabel}
		{orbLevel}
		{capturing}
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
	<main class="voice" data-state={reactorState} data-testid="voice-screen">
		<!-- The stage: the instrument, over three faint field lines. -->
		<section class="stage" aria-hidden="true">
			<svg class="field" viewBox="0 0 1400 1400">
				<circle cx="700" cy="700" r="320" />
				<circle cx="700" cy="700" r="480" stroke-dasharray="1 10" />
				<circle cx="700" cy="700" r="660" />
			</svg>
			<div class="instrument">
				<Reactor size={360} fluid level={orbLevel} state={reactorState} testid="reactor" label="Jarvis" />
			</div>
			<p class="cap" data-testid="caption">{caption}</p>
		</section>

		<section class="exchange" aria-label="Conversation">
			{#if transcript}
				<span class="who" aria-hidden="true">you · {turnAt}</span>
			{/if}
			<p class="q" data-testid="transcript" aria-live="polite" aria-label="What you said">
				{transcript}
			</p>
			{#if response || turnState === 'thinking'}
				<span class="who j" aria-hidden="true">jarvis{replyAt ? ` · ${replyAt}` : ''}</span>
			{/if}
			<p class="a" data-testid="response" aria-live="polite" aria-label="Jarvis says">
				{response}{#if turnState === 'thinking' || turnState === 'listening'}<span
						class="caret"
						aria-hidden="true"
					></span>{/if}
			</p>
			{#if turnTools.length}
				<ul class="calls" data-testid="turn-calls" aria-label="Tools this turn used">
					{#each turnTools as tool (tool.key)}
						<li class={tool.state}>
							<i aria-hidden="true"></i><b>{tool.name}</b>
							<span class="args">{Object.values(tool.arguments ?? {}).join(' · ')}</span>
							{#if tool.state === 'ok'}<em class="ok">ok</em>{:else if tool.state === 'failed'}<em class="bad">{tool.error ?? 'failed'}</em>{/if}
							{#if tool.durationMs}<span class="ms">· {tool.durationMs} ms</span>{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if !transcript && !response && screen === 'ready'}
				<p class="empty" data-testid="voice-empty">
					Say something. The reactor is listening; the answer and what it did to the house appear here.
				</p>
			{/if}
			<ScreenState
				status={screen}
				errorTitle="That turn did not finish"
				errorDetail={errorMsg}
				onretry={() => void connectWs()}
				onreconnect={() => void connectWs()}
				errorTestid="error"
				offlineBody="The link to Jarvis closed. Nothing is being heard until it is back — the next thing you say will not arrive."
			/>
		</section>

		<aside class="side transcript-panel">
			<Panel title="Transcript" meta={sessionMeta} testid="transcript-panel">
				{#snippet children()}
					{#if messages.length}
						<ol class="rows">
							{#each messages as message, i (message.id)}
								<li class:j={message.role === 'assistant'} class:last={i === messages.length - 1}>
									<span class="who" class:j={message.role === 'assistant'}>{message.role === 'assistant' ? 'jarvis' : 'you'}</span>
									<p>{message.content || (message.tools.length ? message.tools.map((t) => t.name).join(' · ') : message.pending ? '…' : '')}</p>
								</li>
							{/each}
						</ol>
					{:else}
						<p class="none">Nothing said yet.</p>
					{/if}
				{/snippet}
			</Panel>
		</aside>

		<aside class="side turn-panel">
			<Panel title="This turn" meta={turnTotalMs !== null ? fmtS(turnTotalMs) : turnLive ? 'live' : '—'} live={turnLive || turnTotalMs !== null} testid="turn-panel">
				{#snippet children()}
					<div class="stages" aria-hidden="true">
						{#each stages as stage (stage.key)}
							<i style:flex={Math.max(1, stage.ms ?? 1)} class:live={stage.ms === null && turnLive} class:done={stage.ms !== null}></i>
						{/each}
					</div>
					<dl class="k" data-testid="latency" aria-label="Pipeline latency">
						{#each stages as stage (stage.key)}
							<div><dt>{stage.label}</dt><dd class:live={stage.ms === null && turnLive}>{fmtMs(stage.ms)}</dd></div>
						{/each}
					</dl>
					{#if turnTools.length}
						<ul class="calls small" aria-label="Tool calls">
							{#each turnTools as tool (tool.key)}
								<li class={tool.state}><i aria-hidden="true"></i><b>{tool.name}</b>{#if tool.state === 'ok'}<em class="ok">ok</em>{/if}{#if tool.durationMs}<span class="ms">{tool.durationMs} ms</span>{/if}</li>
							{/each}
						</ul>
					{/if}
				{/snippet}
			</Panel>
		</aside>

		<footer class="dock" data-testid="dock">
			<!--
			  No `aria-label`. One used to sit here saying "Mute the microphone",
			  which OVERRODE the visible text — so when the button read
			  MIC BLOCKED — ALLOW IT IN THE BROWSER, a screen reader announced a
			  working mute button and the one thing the sighted user could see
			  was the one thing the blind user could not. `aria-pressed` still
			  carries the mute state, which is the part the label cannot say.
			-->
			<button
				type="button"
				class="mic"
				class:active={capturing}
				class:muted
				data-testid="mic"
				data-mic={micReady ? 'open' : 'closed'}
				onclick={toggleMute}
				aria-pressed={muted}
			>
				<span class="ring" aria-hidden="true">
					<svg viewBox="0 0 44 44">
						<circle cx="22" cy="22" r="20" class="track" />
						<circle cx="22" cy="22" r="20" class="arc" stroke-dasharray={ringDash} transform="rotate(-90 22 22)" />
					</svg>
					<span class="g"></span>
				</span>
				<span class="mic-label">{micLabel}</span>
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
					placeholder="Say it, or type it"
					autocomplete="off"
					bind:value={draft}
				/>
				<button type="submit" class="send" data-testid="text-send" disabled={!draft.trim()}
					title={draft.trim() ? 'Send this to Jarvis' : 'Type something to send'}>
					SEND
				</button>
			</form>

			<button
				type="button"
				class="mode"
				data-testid="mode-toggle"
				data-mode="orb"
				aria-pressed={false}
				aria-label="Switch to text chat"
				title="Read the conversation instead of hearing it"
				onclick={toggleChatMode}
			>
				<span class="on">Voice</span><span>Chat</span>
			</button>

			<span class="keys" aria-hidden="true">↵ send · g d devices · g k tasks</span>
		</footer>
	</main>
{/if}

<style>
	/*
	 * Reactor II's chat view (docs/design/c2-reactor.html?view=chat), as a grid:
	 * the transcript at the left, the instrument and the exchange in the
	 * middle, this turn at the right, the dock along the bottom. Under the
	 * shared bar, on the ground, with no chrome of its own.
	 */
	.voice {
		--side: calc(var(--jv-space-7) * 6.6667);
		position: relative;
		display: grid;
		grid-template-columns: var(--side) minmax(0, 1fr) var(--side);
		grid-template-rows: auto minmax(0, 1fr) auto;
		grid-template-areas:
			'transcript stage turn'
			'transcript exchange turn'
			'dock dock dock';
		gap: var(--jv-space-4) var(--jv-space-6);
		/*
		 * MIN-height, and nothing hidden. A long answer or a laptop in
		 * landscape at 500px tall pushes the dock past the bottom edge, and with
		 * the overflow hidden there was no scroll path to it — the one control
		 * on the page became unreachable at the moment there was most to read.
		 */
		min-height: calc(100vh - var(--jv-space-7) - var(--jv-space-2));
		min-height: calc(100dvh - var(--jv-space-7) - var(--jv-space-2));
		padding: var(--jv-space-4) var(--jv-space-6) var(--jv-space-6);
		overflow-x: hidden;
		color: var(--jv-text);
		font-family: var(--jv-font-body);
		background: radial-gradient(ellipse 90% 70% at 50% 110%, var(--jv-bg-raised), transparent 70%), var(--jv-bg);
	}

	/* --- the stage --- */
	.stage {
		grid-area: stage;
		position: relative;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: flex-start;
		gap: var(--jv-space-4);
		padding-top: var(--jv-space-2);
		z-index: 1;
	}
	.field {
		position: absolute;
		left: 50%;
		top: calc(var(--jv-measure-boot) / 2);
		width: calc(var(--jv-measure-orb) * 2.7);
		height: calc(var(--jv-measure-orb) * 2.7);
		transform: translate(-50%, -50%);
		pointer-events: none;
		z-index: -1;
	}
	.field circle {
		fill: none;
		stroke: var(--jv-line-hair);
		stroke-width: 1;
	}
	.instrument {
		width: min(38vmin, var(--jv-measure-boot));
		aspect-ratio: 1;
	}
	.cap {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-accent-deep);
		white-space: nowrap;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}

	/* --- the exchange --- */
	.exchange {
		grid-area: exchange;
		display: grid;
		align-content: start;
		gap: var(--jv-space-2);
		width: min(100%, calc(var(--jv-space-7) * 12.5));
		margin: 0 auto;
		z-index: 1;
	}
	.who {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-tight);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.who.j {
		color: var(--jv-accent-deep);
		margin-top: var(--jv-space-2);
	}
	.q {
		margin: 0;
		font-size: var(--jv-fs-lg);
		color: var(--jv-text-dim);
		min-height: var(--jv-rel-line);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.a {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-2xl);
		line-height: 1.38;
		letter-spacing: var(--jv-track-snug);
		color: var(--jv-text-bright);
		min-height: var(--jv-rel-line);
		overflow-wrap: anywhere;
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.caret {
		display: inline-block;
		width: var(--jv-rule-live);
		height: var(--jv-rel-caret);
		margin-left: var(--jv-space-1);
		vertical-align: -0.15em;
		background: var(--jv-accent);
		animation: blink var(--jv-dur-enter) steps(2) infinite;
	}
	.empty {
		margin: 0 auto;
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-faint);
		max-width: 44ch;
		text-align: center;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}

	/* The tool-call line: dot, name, arguments, verdict, time. */
	.calls {
		list-style: none;
		margin: var(--jv-space-1) 0 0;
		padding: 0;
		display: grid;
		gap: var(--jv-space-1);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		line-height: 1.9;
		color: var(--jv-text-faint);
	}
	.calls li {
		display: flex;
		align-items: baseline;
		gap: var(--jv-space-2);
		min-width: 0;
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.calls i {
		flex: none;
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: 50%;
		background: var(--jv-ok);
		align-self: center;
	}
	.calls li.running i {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.calls li.failed i {
		background: var(--jv-danger);
	}
	.calls b {
		font-weight: var(--jv-weight-body);
		color: var(--jv-text-dim);
	}
	.calls .args {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}
	.calls em {
		font-style: normal;
	}
	.calls .ok {
		color: var(--jv-ok);
	}
	.calls .bad {
		color: var(--jv-danger-text);
	}
	.calls .ms {
		font-variant-numeric: tabular-nums;
	}
	.calls.small li {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* --- the side panels --- */
	.side {
		z-index: 1;
		min-width: 0;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.transcript-panel {
		grid-area: transcript;
	}
	.turn-panel {
		grid-area: turn;
	}
	.side :global(.body) {
		padding: 0;
	}
	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
		max-height: calc(var(--jv-space-7) * 12);
		overflow-y: auto;
	}
	.rows li {
		padding: var(--jv-space-3) var(--jv-space-4);
		border-bottom: 1px solid var(--jv-line-hair);
		font-size: var(--jv-fs-sm);
		animation: jv-rise var(--jv-dur-base) var(--jv-ease-out) both;
	}
	.rows li p {
		margin: var(--jv-space-1) 0 0;
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.rows li.j p {
		color: var(--jv-text);
	}
	.rows li.last {
		background: var(--jv-wash);
	}
	.none {
		margin: 0;
		padding: var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.stages {
		display: flex;
		gap: var(--jv-rule-live);
		height: var(--jv-space-1);
		margin: var(--jv-space-4) var(--jv-space-4) var(--jv-space-2);
	}
	.stages i {
		background: var(--jv-line);
		border-radius: var(--jv-radius-sm);
		transform-origin: left;
		transition: flex var(--jv-dur-base) var(--jv-ease-out);
	}
	.stages i.done {
		background: var(--jv-text-dim);
	}
	.stages i.live {
		background: var(--jv-accent);
		box-shadow: 0 0 var(--jv-radius-md) var(--jv-glow);
		animation: jv-blink var(--jv-dur-pulse) var(--jv-ease-in-out) infinite;
	}
	.k {
		margin: 0;
	}
	.k div {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.k dt {
		margin: 0;
	}
	.k dd {
		margin: 0;
		font-family: var(--jv-font-chrome);
		color: var(--jv-text);
		font-variant-numeric: tabular-nums;
	}
	.k dd.live {
		color: var(--jv-accent);
	}
	.turn-panel .calls {
		padding: var(--jv-space-2) var(--jv-space-4) var(--jv-space-3);
	}

	/* --- the dock --- */
	.dock {
		grid-area: dock;
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto auto;
		align-items: center;
		gap: var(--jv-space-4);
		min-height: calc(var(--jv-space-7) + var(--jv-space-4));
		padding: var(--jv-space-2) var(--jv-space-4) var(--jv-space-2) var(--jv-space-3);
		background: var(--jv-panel);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		z-index: 2;
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.mic {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-3);
		background: transparent;
		border: 0;
		padding: 0;
		cursor: pointer;
		color: var(--jv-text-dim);
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		white-space: nowrap;
		transition: color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.mic:hover {
		color: var(--jv-text);
	}
	.mic .ring {
		position: relative;
		display: grid;
		place-items: center;
		width: calc(var(--jv-space-7) - var(--jv-space-1));
		height: calc(var(--jv-space-7) - var(--jv-space-1));
		flex: none;
	}
	.mic .ring svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		overflow: visible;
	}
	.mic .track {
		fill: none;
		stroke: var(--jv-line);
		stroke-width: 1;
	}
	.mic .arc {
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 1.5;
		stroke-linecap: round;
		transition: stroke-dasharray var(--jv-dur-fast) linear, stroke var(--jv-dur-base) var(--jv-ease-out);
	}
	/* The capsule: a small rounded glyph is the one round control on this screen. */
	.mic .g {
		width: calc(var(--jv-space-2) + var(--jv-space-1));
		height: var(--jv-space-4);
		border: 1.5px solid var(--jv-accent);
		border-radius: var(--jv-radius-md);
		position: relative;
		transition: border-color var(--jv-dur-base) var(--jv-ease-out);
	}
	.mic .g::after {
		content: '';
		position: absolute;
		left: calc(-1 * var(--jv-space-1) - 1px);
		right: calc(-1 * var(--jv-space-1) - 1px);
		bottom: calc(-1 * var(--jv-space-2));
		height: var(--jv-space-2);
		border: 1.5px solid var(--jv-accent);
		border-top: 0;
		border-radius: 0 0 var(--jv-radius-lg) var(--jv-radius-lg);
		transition: border-color var(--jv-dur-base) var(--jv-ease-out);
	}
	.mic.active .arc {
		filter: drop-shadow(0 0 var(--jv-space-1) var(--jv-glow));
	}
	/* Muted is a state the eye should catch without reading the label. */
	.mic.muted {
		color: var(--jv-text-faint);
	}
	.mic.muted .arc {
		stroke: var(--jv-tick);
	}
	.mic.muted .g,
	.mic.muted .g::after {
		border-color: var(--jv-tick);
	}
	.say {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		min-width: 0;
	}
	.say-input {
		flex: 1 1 auto;
		min-width: 0;
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-md);
		color: var(--jv-text-bright);
		background: transparent;
		border: 0;
		border-bottom: 1px solid transparent;
		padding: var(--jv-space-2) 0;
		transition: border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.say-input::placeholder {
		color: var(--jv-text-faint);
	}
	.say-input:focus {
		outline: none;
		border-bottom-color: var(--jv-line);
	}
	.send {
		flex: none;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-wide);
		text-transform: uppercase;
		color: var(--jv-text-dim);
		background: transparent;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-md);
		padding: var(--jv-space-2) var(--jv-space-4);
		cursor: pointer;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.send:hover:not(:disabled) {
		color: var(--jv-text-bright);
		border-color: var(--jv-text-dim);
	}
	.send:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	/* VOICE | CHAT: two words, the current one underlined. One control. */
	.mode {
		display: inline-flex;
		gap: var(--jv-space-4);
		background: transparent;
		border: 0;
		padding: 0;
		cursor: pointer;
		font-family: var(--jv-font-body);
		font-weight: var(--jv-weight-label);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-chrome);
		text-transform: uppercase;
		color: var(--jv-text-faint);
		white-space: nowrap;
	}
	.mode span {
		padding-bottom: var(--jv-space-1);
		border-bottom: 1px solid transparent;
		transition: color var(--jv-dur-fast) var(--jv-ease-out), border-color var(--jv-dur-fast) var(--jv-ease-out);
	}
	.mode span.on {
		color: var(--jv-text-bright);
		border-bottom-color: var(--jv-accent);
	}
	.mode:hover span:not(.on) {
		color: var(--jv-text);
	}
	.keys {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-snug);
		color: var(--jv-text-faint);
		border-left: 1px solid var(--jv-line-hair);
		padding-left: var(--jv-space-4);
		white-space: nowrap;
	}

	@keyframes blink {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0;
		}
	}

	/* --- narrower: the panels drop under the exchange, then stack --- */
	@media (max-width: 1180px) {
		/* The dock's hints go before its input does. */
		.keys {
			display: none;
		}
		.dock {
			grid-template-columns: auto minmax(0, 1fr) auto;
		}
		.voice {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
			grid-template-rows: auto auto auto auto;
			grid-template-areas:
				'stage stage'
				'exchange exchange'
				'transcript turn'
				'dock dock';
			gap: var(--jv-space-4);
			padding: var(--jv-space-4) var(--jv-space-5) var(--jv-space-5);
		}
		.instrument {
			width: min(44vmin, var(--jv-measure-boot));
		}
		.rows {
			max-height: calc(var(--jv-space-7) * 6);
		}
	}
	@media (max-width: 900px) {
		.mic-label {
			display: none;
		}
	}
	@media (max-width: 720px) {
		.voice {
			grid-template-columns: minmax(0, 1fr);
			grid-template-areas:
				'stage'
				'exchange'
				'dock'
				'turn'
				'transcript';
			padding: var(--jv-space-3) var(--jv-space-3) var(--jv-space-5);
		}
		.dock {
			grid-template-columns: auto minmax(0, 1fr);
			grid-template-rows: auto auto;
			gap: var(--jv-space-3);
		}
		.mic-label {
			display: none;
		}
		.mode {
			grid-column: 2;
			justify-self: end;
		}
		.keys {
			display: none;
		}
		.field {
			display: none;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.mic .arc,
		.stages i {
			transition: none;
		}
	}
</style>
